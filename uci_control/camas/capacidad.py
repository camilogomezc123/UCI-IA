"""Plan de camas del día: capacidad contra demanda y nivel de escalamiento.

Sigue el método Real-Time Demand Capacity del IHI: cada mañana se predice cuántas camas se
liberan (capacidad) y cuántos ingresos llegarán (demanda), se compara y se arma el plan con los
pacientes a egresar primero. El nivel de escalamiento toma la idea de OPEL (NHS England): pocas
señales objetivas, umbrales fijos y acciones predefinidas por nivel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .. import config


@dataclass
class PacientePlan:
    cama: str
    estado: str
    destino: str
    probabilidad_egreso_hoy: float
    fecha_esperada_egreso: date | None
    dias_uci: int
    dias_listo_sin_salir: int
    barreras: list[dict]
    acciones: list[str]
    valor_dia: float


@dataclass
class PlanCamas:
    fecha: date
    camas_totales: int
    ocupadas: int
    libres: int
    egresos_esperados: float
    demanda_esperada: float
    brecha: float  # camas disponibles al final del día menos ingresos esperados
    nivel: int
    nivel_nombre: str
    acciones_nivel: list[str]
    listos: int
    listos_sin_salir_mas_de_un_dia: int
    estancados: int
    muy_estancados: int
    dias_cama_bloqueados_hoy: float
    valor_bloqueado_hoy: float
    ingresos_perdidos_estimados: float
    pacientes: list[PacientePlan] = field(default_factory=list)
    barreras_frecuentes: list[dict] = field(default_factory=list)
    flujo: dict = field(default_factory=dict)


NIVELES = {
    1: ("Normal", ["Ronda de egresos antes de las 10 a. m.", "Meta: un tercio de los egresos antes del mediodía"]),
    2: ("Presión moderada", ["Comité de camas a las 11 a. m. con hospitalización", "Destrabar hoy las barreras de los pacientes listos",
                             "Confirmar fecha esperada de egreso de todos los pacientes"]),
    3: ("Presión alta", ["Coordinación médica asigna un responsable por cada paciente listo", "Escalar autorizaciones y remisiones al asegurador",
                         "Usar camas de intermedio y sala de egresos como puente", "Revisar ingresos programados de cirugía"]),
    4: ("Crítico", ["Activar el plan de contingencia institucional", "Reunión con gerencia y aseguradores el mismo día",
                    "Priorizar ingresos por gravedad (triage de la guía SCCM)", "Evaluar diferir cirugías programadas que requieren UCI"]),
}


def nivel_escalamiento(ocupacion: float, brecha: float, bloqueadas_pct: float) -> int:
    """Ocupación en %, brecha en camas y porcentaje de camas con paciente listo que no ha salido."""
    if ocupacion >= 100 and brecha <= -3 and bloqueadas_pct >= 15:
        return 4
    if ocupacion > 95 or brecha <= -3 or bloqueadas_pct >= 10:
        return 3
    if ocupacion >= 85 or brecha < 0:
        return 2
    return 1


def plan_del_dia(fecha: date, pacientes: list[PacientePlan], camas_totales: int | None = None,
                 demanda_esperada: float | None = None, flujo: dict | None = None) -> PlanCamas:
    total = camas_totales or config.CAMAS_UCI_TOTAL
    ocupadas = len(pacientes)
    libres = max(total - ocupadas, 0)
    egresos = round(sum(p.probabilidad_egreso_hoy for p in pacientes), 1)
    demanda = demanda_esperada if demanda_esperada is not None else config.SOLICITUDES_CAMA_DIA * config.PROPORCION_SOLICITUDES_ADMITIBLES
    brecha = round(libres + egresos - demanda, 1)
    listos = [p for p in pacientes if p.estado == "listo"]
    bloqueadas_pct = 100 * len(listos) / total if total else 0
    nivel = nivel_escalamiento(100 * ocupadas / total if total else 0, brecha, bloqueadas_pct)
    conteo: dict[str, dict] = {}
    for p in listos:
        for b in p.barreras:
            c = conteo.setdefault(b["codigo"], {**b, "pacientes": 0})
            c["pacientes"] += 1
    valor = sum(p.valor_dia for p in listos)
    ingresos_perdidos = max(-brecha, 0.0)  # ingresos esperados que hoy no tendrían cama
    return PlanCamas(
        fecha=fecha, camas_totales=total, ocupadas=ocupadas, libres=libres, egresos_esperados=egresos,
        demanda_esperada=round(demanda, 1), brecha=brecha, nivel=nivel, nivel_nombre=NIVELES[nivel][0],
        acciones_nivel=NIVELES[nivel][1], listos=len(listos),
        listos_sin_salir_mas_de_un_dia=sum(p.dias_listo_sin_salir >= 1 for p in listos),
        estancados=sum(p.dias_uci >= config.DIAS_PACIENTE_ESTANCADO for p in pacientes),
        muy_estancados=sum(p.dias_uci >= config.DIAS_PACIENTE_MUY_ESTANCADO for p in pacientes),
        dias_cama_bloqueados_hoy=float(len(listos)), valor_bloqueado_hoy=valor, ingresos_perdidos_estimados=ingresos_perdidos,
        pacientes=sorted(pacientes, key=lambda p: (-p.probabilidad_egreso_hoy, -p.dias_listo_sin_salir, p.cama)),
        barreras_frecuentes=sorted(conteo.values(), key=lambda c: -c["pacientes"]), flujo=flujo or {},
    )
