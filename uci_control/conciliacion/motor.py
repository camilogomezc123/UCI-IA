"""Motor de conciliación: relaciona las tres fuentes por cama, episodio y fecha y aplica las reglas."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..modelos import (
    CargoCosto, Categoria, Discrepancia, EntregaEstacion, ExpedienteCama, RegistroOrbit, Severidad,
)
from .reglas import REGLAS, Contexto, Regla


@dataclass
class ResultadoConciliacion:
    fecha: date
    expedientes: dict[str, ExpedienteCama]
    discrepancias: list[Discrepancia]
    alertas_globales: list[Discrepancia] = field(default_factory=list)

    def por_severidad(self) -> dict[str, int]:
        conteo = {s.value: 0 for s in Severidad}
        for d in self.discrepancias:
            conteo[d.severidad.value] += 1
        return conteo


def conciliar(
    fecha: date,
    censo: list[RegistroOrbit],
    entregas: list[EntregaEstacion],
    cargos: list[CargoCosto],
    reglas: list[Regla] = REGLAS,
) -> ResultadoConciliacion:
    expedientes: dict[str, ExpedienteCama] = {}
    globales: list[Discrepancia] = []

    def expediente(cama: str) -> ExpedienteCama:
        return expedientes.setdefault(cama, ExpedienteCama(cama=cama))

    for r in censo:
        if r.fecha == fecha and r.cama:
            expediente(r.cama).orbit = r

    for estacion in entregas:
        for alerta in estacion.alertas:
            globales.append(Discrepancia(
                regla="R17", titulo="Alerta en el correo de entrega", severidad=Severidad.CRITICA
                if "credencial" in alerta else Severidad.MEDIA,
                categoria=Categoria.SEGURIDAD if "credencial" in alerta else Categoria.DATOS,
                detalle=f"{estacion.estacion or estacion.fuente}: {alerta}", fuentes=["entrega"],
            ))
        for cama in estacion.camas:
            if not cama.cama:
                continue
            exp = expediente(cama.cama)
            if exp.entrega is not None:
                globales.append(Discrepancia(
                    regla="R05", titulo="Cama reportada en dos entregas", severidad=Severidad.ALTA,
                    categoria=Categoria.DATOS, cama=cama.cama,
                    detalle=f"La cama aparece en «{exp.entrega.estacion}» y en «{cama.estacion}».",
                    fuentes=["entrega"],
                ))
                continue
            exp.entrega = cama

    # Los cargos se unen por episodio cuando existe; si no, por cama.
    por_episodio = {exp.episodio: exp for exp in expedientes.values() if exp.episodio}
    for cargo in cargos:
        if cargo.fecha != fecha:
            continue
        destino = por_episodio.get(cargo.episodio) if cargo.episodio else None
        if destino is None and cargo.cama:
            destino = expediente(cargo.cama)
        if destino is not None:
            destino.cargos.append(cargo)

    ctx = Contexto(fecha=fecha)
    todas: list[Discrepancia] = []
    for cama in sorted(expedientes, key=_orden_cama):
        exp = expedientes[cama]
        for regla in reglas:
            exp.discrepancias.extend(regla(exp, ctx))
        todas.extend(exp.discrepancias)
    todas.extend(globales)

    todas.sort(key=lambda d: (-d.severidad.peso, _orden_cama(d.cama or "")))
    for i, d in enumerate(todas, 1):
        d.id = f"D{i:03d}"
    globales_finales = [d for d in todas if d.cama is None]
    return ResultadoConciliacion(fecha=fecha, expedientes=expedientes, discrepancias=todas, alertas_globales=globales_finales)


def _orden_cama(cama: str):
    letras = "".join(c for c in cama if not c.isdigit())
    numero = "".join(c for c in cama if c.isdigit())
    return (letras, int(numero) if numero else 0)
