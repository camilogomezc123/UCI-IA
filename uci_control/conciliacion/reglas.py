"""Reglas de discrepancia entre ORBIT, la entrega de turno y la facturación.

Cada regla recibe el expediente de una cama y el contexto del día, y devuelve cero o más
discrepancias. Para agregar una regla basta con escribir la función y sumarla a REGLAS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Callable, Iterable

from .. import config
from ..conectores.costos import es_cargo_estancia, es_estancia_uci
from ..modelos import Categoria, Condicion, Discrepancia, ExpedienteCama, Severidad


@dataclass
class Contexto:
    fecha: date


Regla = Callable[[ExpedienteCama, Contexto], Iterable[Discrepancia]]


def _d(exp: ExpedienteCama, regla: str, titulo: str, sev: Severidad, cat: Categoria, detalle: str, fuentes, impacto=None):
    return Discrepancia(
        regla=regla, titulo=titulo, severidad=sev, categoria=cat, detalle=detalle,
        cama=exp.cama, episodio=exp.episodio, fuentes=list(fuentes), impacto_cop=impacto,
    )


def _frase(texto: str) -> str:
    return texto[:1].upper() + texto[1:]


def _ocupada(exp: ExpedienteCama) -> bool:
    return bool(exp.orbit and exp.orbit.ocupada)


def r01_piso_con_soporte(exp, ctx):
    """Condición piso según enfermería con soporte vasoactivo o ventilación invasiva en cualquier fuente."""
    e, o = exp.entrega, exp.orbit
    if not e or e.condicion != Condicion.PISO:
        return
    soportes = []
    if e.vasoactivos:
        soportes.append(f"vasoactivos en la entrega ({', '.join(e.vasoactivos)})")
    if e.vmi:
        soportes.append("VMI en la entrega")
    if o and o.vasopresor:
        soportes.append("vasopresor en ORBIT")
    if o and o.vmi:
        soportes.append("VMI en ORBIT")
    if soportes:
        yield _d(exp, "R01", "Condición piso con soporte activo", Severidad.ALTA, Categoria.CLINICA,
                 "Enfermería reporta condición piso, pero hay " + " y ".join(soportes) + ". Confirmar antes de trasladar.",
                 ["entrega", "orbit"])


def r02_criterio_distinto(exp, ctx):
    e, o = exp.entrega, exp.orbit
    if not (e and o) or Condicion.DESCONOCIDA in (e.condicion, o.criterio) or e.condicion == o.criterio:
        return
    yield _d(exp, "R02", "Nivel de atención distinto entre fuentes", Severidad.MEDIA, Categoria.DATOS,
             f"ORBIT registra criterio {o.criterio.value}; la entrega dice {e.condicion.value}.", ["orbit", "entrega"])


def r03_vmi_discordante(exp, ctx):
    e, o = exp.entrega, exp.orbit
    if not (e and o) or e.vmi is None or o.vmi is None or e.vmi == o.vmi:
        return
    yield _d(exp, "R03", "Ventilación invasiva discordante", Severidad.ALTA, Categoria.CLINICA,
             f"ORBIT: VMI {'sí' if o.vmi else 'no'}. Entrega: {e.ventilacion or 'sin dato'}.", ["orbit", "entrega"])


def r04_vasoactivo_discordante(exp, ctx):
    e, o = exp.entrega, exp.orbit
    if not (e and o) or o.vasopresor is None:
        return
    en_entrega = bool(e.vasoactivos)
    if en_entrega == o.vasopresor:
        return
    yield _d(exp, "R04", "Soporte vasoactivo discordante", Severidad.ALTA, Categoria.CLINICA,
             f"ORBIT: vasopresor {'sí' if o.vasopresor else 'no'}. Entrega: "
             f"{', '.join(e.vasoactivos) if en_entrega else 'sin vasoactivos'}.", ["orbit", "entrega"])


def r05_cobertura(exp, ctx):
    if _ocupada(exp) and not exp.entrega:
        yield _d(exp, "R05", "Cama ocupada sin entrega de turno", Severidad.MEDIA, Categoria.DATOS,
                 "ORBIT la registra ocupada y ninguna entrega del turno la menciona.", ["orbit"])
    if exp.entrega and not _ocupada(exp):
        estado = "no existe en el censo" if not exp.orbit else "figura libre en ORBIT"
        yield _d(exp, "R05", "Entrega de una cama que no está ocupada en ORBIT", Severidad.ALTA, Categoria.DATOS,
                 f"La entrega reporta paciente en esta cama, pero la cama {estado}.", ["entrega", "orbit"])


def r06_episodio(exp, ctx):
    e, o = exp.entrega, exp.orbit
    if e and o and e.episodio and o.episodio and e.episodio.strip() != o.episodio.strip():
        yield _d(exp, "R06", "Número de ingreso distinto entre fuentes", Severidad.CRITICA, Categoria.SEGURIDAD,
                 "El episodio de la entrega no coincide con el de ORBIT: posible paciente equivocado o cama mal digitada.",
                 ["entrega", "orbit"])


def r07_fecha_ingreso(exp, ctx):
    e, o = exp.entrega, exp.orbit
    problemas = []
    if o and o.fecha_ingreso_uci and o.fecha_ingreso_hospital and o.fecha_ingreso_uci < o.fecha_ingreso_hospital:
        problemas.append("en ORBIT el ingreso a UCI es anterior al ingreso hospitalario")
    if e and o and e.fecha_ingreso_uci and o.fecha_ingreso_uci:
        dif = abs((e.fecha_ingreso_uci - o.fecha_ingreso_uci).days)
        if dif > config.DIAS_TOLERANCIA_FECHA_INGRESO:
            problemas.append(f"la fecha de ingreso a UCI difiere {dif} días entre la entrega y ORBIT")
    if problemas:
        yield _d(exp, "R07", "Fecha de ingreso a UCI incoherente", Severidad.MEDIA, Categoria.DATOS,
                 _frase("; ".join(problemas)) + ".", ["entrega", "orbit"])


def r08_dispositivos(exp, ctx):
    if not exp.entrega:
        return
    for disp in exp.entrega.dispositivos:
        if not disp.fecha_insercion or disp.justificacion:
            continue
        dias = (ctx.fecha - disp.fecha_insercion).days
        if dias <= config.DIAS_DISPOSITIVO_REVISAR:
            continue
        sev = Severidad.ALTA if dias > config.DIAS_DISPOSITIVO_CRITICO else Severidad.MEDIA
        yield _d(exp, "R08", "Dispositivo prolongado sin justificación", sev, Categoria.CLINICA,
                 f"{disp.tipo}: {dias} días desde la inserción y sin necesidad registrada.", ["entrega"])


def r09_pendientes(exp, ctx):
    if not exp.entrega:
        return
    for p in exp.entrega.pendientes:
        if not p.fecha_solicitud:
            continue
        horas = (ctx.fecha - p.fecha_solicitud).days * 24
        es_admin = bool(re.search(r"autoriz|convenio|eps", p.tipo + " " + p.descripcion, re.I))
        limite = config.HORAS_AUTORIZACION_MAX if es_admin else config.HORAS_PENDIENTE_MAX
        if horas <= limite:
            continue
        impacto = config.COSTO_DIA_UCI * horas / 24 if es_admin else None
        yield _d(exp, "R09", "Pendiente vencido" if not es_admin else "Autorización sin resolver",
                 Severidad.ALTA if es_admin else Severidad.MEDIA,
                 Categoria.FINANCIERA if es_admin else Categoria.FLUJO,
                 f"[{p.tipo}] {p.descripcion}: {horas // 24} días desde la solicitud (límite {limite} h).",
                 ["entrega"], impacto)


def r10_aislamiento(exp, ctx):
    e, o = exp.entrega, exp.orbit
    if not (e and o):
        return
    a_e = (e.aislamiento or "").strip().lower()
    a_o = (o.aislamiento or "").strip().lower()
    if bool(a_e) != bool(a_o):
        yield _d(exp, "R10", "Aislamiento declarado en una sola fuente", Severidad.ALTA, Categoria.SEGURIDAD,
                 f"Entrega: {e.aislamiento or 'sin aislamiento'}. ORBIT: {o.aislamiento or 'sin aislamiento'}.",
                 ["entrega", "orbit"])


def r11_escalas(exp, ctx):
    o = exp.orbit
    if not _ocupada(exp):
        return
    problemas = []
    if not o.news:
        problemas.append("NEWS vacío")
    elif not re.fullmatch(r"\d{1,2}", o.news.strip()):
        problemas.append(f"NEWS con formato inválido («{o.news}»)")
    if not o.sofa:
        problemas.append("SOFA vacío")
    elif re.fullmatch(r"\d{1,3}\s*-\s*\d{1,3}\s*%", o.sofa.strip()):
        pass  # el tablero de ORBIT exporta el SOFA como rango de mortalidad esperada
    elif not re.fullmatch(r"\d{1,2}", o.sofa.strip()) or int(o.sofa.strip()) > 24:
        problemas.append(f"SOFA con formato inválido («{o.sofa}»; se espera un puntaje de 0 a 24)")
    if problemas:
        yield _d(exp, "R11", "Escalas de gravedad incompletas", Severidad.BAJA, Categoria.DATOS,
                 _frase("; ".join(problemas)) + ". Sin SOFA no se puede calcular el SMR.", ["orbit"])


def r12_cama_sin_cargo(exp, ctx):
    if not _ocupada(exp):
        return
    if not any(es_cargo_estancia(c) for c in exp.cargos):
        yield _d(exp, "R12", "Cama ocupada sin cargo de estancia", Severidad.ALTA, Categoria.FINANCIERA,
                 f"ORBIT la registra ocupada el {ctx.fecha:%d/%m} y facturación no tiene cargo de estancia para ese día.",
                 ["orbit", "costos"], config.TARIFA_CAMA_UCI)


def r13_cargo_sin_cama(exp, ctx):
    if _ocupada(exp):
        return
    estancias = [c for c in exp.cargos if es_cargo_estancia(c)]
    if estancias:
        yield _d(exp, "R13", "Cargo de estancia sin cama ocupada", Severidad.MEDIA, Categoria.FINANCIERA,
                 f"Facturación tiene {len(estancias)} cargo(s) de estancia, pero la cama no figura ocupada en ORBIT.",
                 ["costos", "orbit"], sum(c.valor for c in estancias))


def r14_estancia_evitable(exp, ctx):
    e, o = exp.entrega, exp.orbit
    if not e or e.condicion != Condicion.PISO or not _ocupada(exp):
        return
    if e.vasoactivos or e.vmi or o.vasopresor or o.vmi:
        return  # lo cubre R01: primero hay que aclarar el soporte
    yield _d(exp, "R14", "Paciente en condición piso ocupando cama UCI", Severidad.MEDIA, Categoria.FLUJO,
             "Candidato para la ronda de flujo: cada día en esta condición es estancia evitable.",
             ["entrega"], config.COSTO_DIA_UCI)


def r15_inconsistencia_interna(exp, ctx):
    if not exp.entrega:
        return
    for aviso in exp.entrega.advertencias:
        yield _d(exp, "R15", "Inconsistencia dentro de la entrega", Severidad.MEDIA, Categoria.DATOS, aviso, ["entrega"])


def r16_glosa_pertinencia(exp, ctx):
    if not exp.entrega or exp.entrega.condicion != Condicion.PISO:
        return
    uci = [c for c in exp.cargos if es_estancia_uci(c)]
    if uci:
        yield _d(exp, "R16", "Estancia UCI facturada a paciente en condición piso", Severidad.ALTA, Categoria.FINANCIERA,
                 f"Se cargó «{uci[0].concepto}» y enfermería reporta condición piso: riesgo de glosa por pertinencia.",
                 ["costos", "entrega"], sum(c.valor for c in uci))


REGLAS: list[Regla] = [
    r01_piso_con_soporte, r02_criterio_distinto, r03_vmi_discordante, r04_vasoactivo_discordante,
    r05_cobertura, r06_episodio, r07_fecha_ingreso, r08_dispositivos, r09_pendientes, r10_aislamiento,
    r11_escalas, r12_cama_sin_cargo, r13_cargo_sin_cama, r14_estancia_evitable, r15_inconsistencia_interna,
    r16_glosa_pertinencia,
]
