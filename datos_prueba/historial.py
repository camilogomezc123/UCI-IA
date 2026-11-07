"""UCI sintética de varios días para probar el aprendizaje del estado diario.

Todo es inventado (documentos SINT-H-*, ingresos EP-H-*). Cada paciente tiene una gravedad que
baja día a día con ruido; de ella salen los soportes, el NEWS y el criterio de ORBIT. El egreso
depende del estado de la mañana (soportes, gravedad, barreras como la espera de cama) y del día
de la semana, así que un modelo que mira el estado diario debe ganarle a uno que solo mira los
días en UCI.

Cada día simulado trae el tablero de la mañana, las entregas de turno por estación y la verdad:
los pacientes que salen durante el día. `escribir_correos` los deja como .eml (entregas con el
formato estructurado y, si se pide, el tablero adjunto) para probar la carga del histórico.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

from uci_control.conectores.orbit_tablero import PacienteTablero, seudonimo
from uci_control.modelos import Condicion, Dispositivo, EntregaCama, Pendiente

ESTACIONES = {"UCI Torre B": range(1, 13), "UCI Torre C": range(13, 25)}
_BARRERAS = {"cama": ("traslado", "Traslado a piso sin cama asignada", "PENDIENTE TRASLADO A PISO SIN CAMA ASIGNADA"),
             "autorizacion": ("autorización", "Autorización de la EPS para egreso", "PENDIENTE AUTORIZACION DE LA EPS")}


@dataclass
class _Paciente:
    n: int
    cama: str
    estacion: str
    ingreso: date
    gravedad: float
    edad: int
    piso_previo: float
    barrera: str | None = None

    @property
    def documento(self) -> str:
        return f"SINT-H-{self.n:05d}"


@dataclass
class DiaSimulado:
    fecha: date
    tablero: list[PacienteTablero]
    entregas: dict[str, list[EntregaCama]]  # por estación
    egresan: set[str]  # seudónimos de los que salen durante el día
    documentos: dict[str, str] = field(default_factory=dict)  # cama -> documento, solo para escribir el tablero


def _poisson(rnd: random.Random, media: float) -> int:
    k, p, umbral = 0, 1.0, math.exp(-media)
    while True:
        p *= rnd.random()
        if p <= umbral:
            return k
        k += 1


def _riesgo(p: _Paciente, dia: date) -> float:
    g = p.gravedad
    if g > 2.3:  # ventilación invasiva o vasoactivo
        z = -6.0
    else:
        z = -3.6 + 2.6 * (g < 1.0) + 1.0 * (g < 0.5) - 1.8 * (p.barrera is not None)
    z -= 0.9 * (dia.weekday() >= 5)
    return 1 / (1 + math.exp(-z))


def _ventilacion(g: float) -> str:
    return "VMI" if g > 2.3 else "ALTO FLUJO" if g > 1.6 else "CANULA NASAL BAJO FLUJO" if g > 0.8 else "AIRE AMBIENTE"


def _tablero(p: _Paciente, dia: date, rnd: random.Random) -> PacienteTablero:
    g = p.gravedad
    vmi, vaso = g > 2.3, g > 2.6
    return PacienteTablero(
        cama=p.cama, fecha=dia, seudonimo=seudonimo(p.documento), edad=p.edad, sexo="F" if p.n % 2 else "M",
        pagador="EPS EJEMPLO", cie10=["J960"] if p.n % 3 else ["A419"], oncologico=False,
        criterio="UCI" if g > 1.3 else "UCIN" if g > 0.6 else "OTROS", especialidades=["MEDICINA CRITICA"],
        soporte_hemodinamico="VASOPRESOR" if vaso else "NINGUNO", soporte_ventilatorio=_ventilacion(g),
        movilizacion_temprana=None, news=float(max(0, min(15, round(1 + 2.4 * g + rnd.gauss(0, 1))))),
        sofa_mortalidad=round(min(0.6, 0.05 + 0.12 * g), 2),
        barthel="DEPENDIENTE SEVERO" if g > 2 else "DEPENDIENTE MODERADO" if g > 1 else "INDEPENDIENTE",
        ims=0 if vmi else 4 if g > 1 else 7, rass=-3.0 if vmi else 0.0, cpot=1.0 if vmi else 0.0,
        eva=None if vmi else float(rnd.choice([0, 1, 2, 3, 6])), must_alto=g > 2, riesgos=[],
        dias_hospitalizacion=round((dia - p.ingreso).days + p.piso_previo + 0.3, 2),
        pendientes=[_BARRERAS[p.barrera][2]] if p.barrera else [],
    )


def _entrega(p: _Paciente, dia: date) -> EntregaCama:
    g = p.gravedad
    pendientes = [Pendiente(_BARRERAS[p.barrera][0], _BARRERAS[p.barrera][1])] if p.barrera else []
    return EntregaCama(
        cama=p.cama, estacion=p.estacion, fecha=dia, turno="Noche", episodio=f"EP-H-{p.n:05d}",
        condicion=Condicion.UCI if g > 1.3 else Condicion.UCIN if g > 0.6 else Condicion.PISO,
        ventilacion={"VMI": "VMI por tubo orotraqueal", "ALTO FLUJO": "Cánula de alto flujo 40 L",
                     "CANULA NASAL BAJO FLUJO": "Cánula nasal 2 L", "AIRE AMBIENTE": "Aire ambiente"}[_ventilacion(g)],
        vmi=g > 2.3, vasoactivos=["norepinefrina"] if g > 2.6 else [], trr=False,
        dispositivos=[Dispositivo("CVC", p.ingreso)] if g > 1.5 else [], antibioticos=["Meropenem"] if p.n % 3 == 0 else [],
        aislamiento=None, pendientes=pendientes,
    )


def simular(dias: int = 160, inicio: date = date(2026, 3, 2), llegadas: float = 2.4, semilla: int = 5,
            sin_tablero: set[date] = frozenset(), sin_entrega: set[tuple[date, str]] = frozenset()) -> list[DiaSimulado]:
    """`sin_tablero`: días sin tablero. `sin_entrega`: (día, estación) sin entrega de turno."""
    rnd = random.Random(semilla)
    camas = [(est, f"U{i}") for est, r in ESTACIONES.items() for i in r]
    ocupadas: dict[str, _Paciente] = {}
    n = 0
    for est, cama in camas[:18]:  # la unidad arranca con 18 pacientes de estancias variadas
        n += 1
        ocupadas[cama] = _Paciente(n, cama, est, inicio - timedelta(days=rnd.randint(1, 12)), rnd.uniform(0.3, 3.0),
                                   rnd.randint(25, 90), rnd.choice([0, 0, 0, 1.5, 3.2]))
    salida = []
    for d in range(dias):
        hoy = inicio + timedelta(days=d)
        tablero = [] if hoy in sin_tablero else [_tablero(p, hoy, rnd) for p in ocupadas.values()]
        entregas = {est: [_entrega(p, hoy) for p in ocupadas.values() if p.estacion == est]
                    for est in ESTACIONES if (hoy, est) not in sin_entrega}
        documentos = {p.cama: p.documento for p in ocupadas.values()}
        egresan = set()
        for cama, p in list(ocupadas.items()):
            if rnd.random() < _riesgo(p, hoy):
                egresan.add(seudonimo(p.documento))
                del ocupadas[cama]
        libres = [c for c in camas if c[1] not in ocupadas]
        for _ in range(min(_poisson(rnd, llegadas), len(libres))):
            est, cama = libres.pop(rnd.randrange(len(libres)))
            n += 1
            ocupadas[cama] = _Paciente(n, cama, est, hoy, rnd.uniform(1.0, 3.4), rnd.randint(20, 92), rnd.choice([0, 0, 0, 1.5, 4.2]))
        for p in ocupadas.values():
            p.gravedad = max(0.0, p.gravedad - 0.22 + rnd.gauss(0, 0.25))
            if p.barrera is None and p.gravedad < 1.0 and rnd.random() < 0.25:
                p.barrera = rnd.choice(list(_BARRERAS))
            elif p.barrera is not None and rnd.random() < 0.35:
                p.barrera = None
        salida.append(DiaSimulado(hoy, tablero, entregas, egresan, documentos))
    return salida


# ---------------------------------------------------------------- correos .eml

def _texto_entrega(estacion: str, dia: date, camas: list[EntregaCama]) -> str:
    l = ["ENTREGA DE TURNO DE ENFERMERÍA (formato estructurado)", f"Estación: {estacion}", f"Fecha: {dia - timedelta(days=1):%d/%m/%Y}",
         "Turno: Noche", ""]
    for c in camas:
        l += [f"CAMA: {c.cama}", f"Episodio: {c.episodio}", f"Condición: {c.condicion.value}", f"Ventilación: {c.ventilacion}",
              f"Vasoactivos: {'Norepinefrina 0,1 mcg/kg/min' if c.vasoactivos else 'No'}", "TRR: No"]
        if c.dispositivos:
            l.append("Dispositivos: " + "; ".join(f"{d.tipo} {d.fecha_insercion:%d/%m/%Y}" for d in c.dispositivos))
        l += [f"Antibióticos: {'; '.join(c.antibioticos) or 'Ninguno'}", "Aislamiento: Ninguno"]
        if c.pendientes:
            l += ["Pendientes:"] + [f"- [{p.tipo}] {p.descripcion}" for p in c.pendientes]
        l.append("")
    return "\n".join(l)


def _xlsx_tablero(dia: DiaSimulado) -> bytes:
    from io import BytesIO

    from openpyxl import Workbook

    from .tablero import COLUMNAS

    criterio = {"UCI": "CUIDADO INTENSIVO", "UCIN": "CUIDADO INTERMEDIO", "OTROS": "OTROS"}
    wb = Workbook()
    ws = wb.active
    ws.append(["TABLERO DE UCI"])
    ws.append([f"FECHA GENERACIÓN: {dia.fecha.isoformat()} 06:00:00"])
    ws.append(COLUMNAS)
    for i, p in enumerate(dia.tablero, 1):
        dias = p.dias_hospitalizacion or 0
        sofa = round((p.sofa_mortalidad or 0) * 100)
        ws.append([i, f"CAMA {p.cama[1:]}", dia.documentos[p.cama], "PACIENTE SINTETICO", p.edad, p.sexo, p.pagador,
                   " ".join(p.cie10), "NO APLICA", "", criterio[p.criterio], "MEDICINA CRITICA", "", "", p.soporte_hemodinamico,
                   p.soporte_ventilatorio, "", p.news, f"{sofa}-{sofa}%", p.barthel, f"IMS {p.ims}", p.rass, p.cpot, p.eva,
                   "<p><b>RIESGO ALTO</b></p>" if p.must_alto else "<p>RIESGO BAJO</p>", "", "\n".join(p.pendientes), "",
                   f"{int(dias)}dias {int((dias % 1) * 24)}hrs 0min"])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def escribir_correos(dias: list[DiaSimulado], carpeta: str | Path, con_tablero: bool = True) -> Path:
    """Un .eml por estación y por día, enviado a las 7:05 a. m. (entrega del turno de la noche).
    Con `con_tablero`, la entrega de la primera estación lleva el tablero del día adjunto."""
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    zona = timezone(timedelta(hours=-5))
    for dia in dias:
        for k, (estacion, camas) in enumerate(dia.entregas.items()):
            m = EmailMessage()
            m["Subject"] = f"Entrega de turno {estacion} {dia.fecha:%d/%m/%Y}"
            m["From"] = f"{estacion} <{estacion.lower().replace(' ', '.')}@ejemplo.invalid>"
            m["To"] = "coordinacion.uci@ejemplo.invalid"
            m["Date"] = format_datetime(datetime(dia.fecha.year, dia.fecha.month, dia.fecha.day, 7, 5, tzinfo=zona))
            m.set_content(_texto_entrega(estacion, dia.fecha, camas))
            if con_tablero and k == 0 and dia.tablero:
                nombre = f"Reporte_TableroControlUCI1{dia.fecha.isoformat()}.xlsx"
                m.add_attachment(_xlsx_tablero(dia), maintype="application",
                                 subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=nombre)
            (carpeta / f"{dia.fecha.isoformat()}_{k}.eml").write_bytes(bytes(m))
    return carpeta
