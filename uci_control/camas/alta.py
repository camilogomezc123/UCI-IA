"""¿Puede este paciente salir hoy de la cama UCI?

Criterios clínicos de alta (lo que SAFER llama "clinical criteria for discharge") tomados del
tablero de ORBIT, en línea con la guía SCCM 2016: el paciente sale de la UCI cuando su estado
fisiológico se estabilizó y ya no necesita monitoreo ni intervenciones propias de la UCI.

- listo: cumple todos los criterios. Debe salir hoy, idealmente antes del mediodía.
- casi: le falta un criterio menor (no ventilación invasiva ni vasoactivo). Probable mañana.
- no: sigue necesitando la UCI.

El destino sugerido separa hospitalización de cuidado intermedio. El SWIFT (Gajic, Crit Care
Med 2008) estima el riesgo de reingreso a UCI; sin gases arteriales ni Glasgow se calcula
parcial y lo dice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from math import ceil

from .. import config
from ..conectores.comun import sin_tildes
from ..conectores.orbit_tablero import PacienteTablero
from ..prediccion.riesgo import _dias_restantes


@dataclass
class Swift:
    puntaje: int
    completo: bool
    faltantes: list[str]

    @property
    def alto(self) -> bool:
        return self.puntaje >= config.SWIFT_UMBRAL_ALTO


def swift(dias_uci: float, desde_piso_u_otra_institucion: bool | None = None, pafi: float | None = None,
          glasgow: int | None = None, paco2: float | None = None) -> Swift:
    """Stability and Workload Index for Transfer. Los datos que falten suman 0 y se listan."""
    faltantes, p = [], 0
    if desde_piso_u_otra_institucion is None:
        faltantes.append("origen del ingreso")
    elif desde_piso_u_otra_institucion:
        p += 8
    p += 0 if dias_uci < 2 else 1 if dias_uci <= 10 else 14
    if pafi is None:
        faltantes.append("PaO2/FiO2")
    else:
        p += 0 if pafi > 400 else 5 if pafi >= 150 else 10 if pafi >= 100 else 13
    if glasgow is None:
        faltantes.append("Glasgow")
    else:
        p += 0 if glasgow > 14 else 6 if glasgow >= 11 else 14 if glasgow >= 8 else 24
    if paco2 is None:
        faltantes.append("PaCO2")
    elif paco2 > 45:
        p += 5
    return Swift(p, not faltantes, faltantes)


@dataclass
class EvaluacionAlta:
    cama: str
    estado: str  # listo, casi, no
    destino: str  # hospitalización, intermedio, continuar en UCI
    criterios_cumplidos: list[str] = field(default_factory=list)
    criterios_faltantes: list[str] = field(default_factory=list)
    probabilidad_egreso_hoy: float = 0.0
    fecha_esperada_egreso: date | None = None
    swift: Swift | None = None
    alertas: list[str] = field(default_factory=list)


_O2_BAJO = ("AIRE", "BAJO FLUJO", "CANULA", "CÁNULA", "VENTURI", "NINGUNO")


def evaluar_alta(p: PacienteTablero, dias_uci: float | None = None, laboratorio: dict | None = None) -> EvaluacionAlta:
    """`dias_uci` sale del historial (primer día en el tablero); sin historial se usa la estancia
    hospitalaria del tablero. `laboratorio` admite pafi, glasgow, paco2 y desde_piso."""
    ok, falta = [], []

    def criterio(cumple: bool | None, texto: str, menor: bool = False):
        if cumple is None:
            return
        (ok if cumple else falta).append(texto + (" (menor)" if menor and not cumple else ""))

    vent = sin_tildes(p.soporte_ventilatorio or "").upper()
    criterio(not p.vasoactivo, "Sin soporte vasoactivo")
    criterio(not p.vmi, "Sin ventilación invasiva")
    criterio("ALTO FLUJO" not in vent and "VMNI" not in vent, "Sin alto flujo ni ventilación no invasiva")
    criterio(not vent or any(k in vent for k in _O2_BAJO) or "VMI" in vent, "Oxígeno de bajo flujo o aire ambiente", menor=True)
    criterio(None if p.news is None else p.news < 5, "NEWS menor de 5", menor=True)
    criterio(None if p.rass is None else -1 <= p.rass <= 1, "RASS entre -1 y +1", menor=True)
    dolor = None
    if p.cpot is not None or p.eva is not None:
        dolor = (p.cpot or 0) < 3 and (p.eva or 0) <= 4
    criterio(dolor, "Dolor controlado", menor=True)

    mayores = [c for c in falta if not c.endswith("(menor)")]
    if not falta:
        estado = "listo"
    elif not mayores and len(falta) == 1:
        estado = "casi"
    else:
        estado = "no"
    # ORBIT ya marcó criterio de piso o intermedio: refuerza el estado.
    if estado == "casi" and p.criterio == "OTROS":
        estado = "listo"

    if estado == "no":
        destino = "continuar en UCI"
    elif p.criterio == "OTROS" and (p.news or 0) < 5:
        destino = "hospitalización"
    else:
        destino = "intermedio"

    prob = {"listo": 0.8, "casi": 0.35, "no": 0.05}[estado]
    if estado == "listo" and p.criterio == "UCI":
        prob = 0.6  # cumple criterios pero el tratante aún no cambia el criterio en ORBIT

    dias = dias_uci if dias_uci is not None else (p.dias_hospitalizacion or 0)
    lab = laboratorio or {}
    sw = swift(dias, lab.get("desde_piso"), lab.get("pafi"), lab.get("glasgow"), lab.get("paco2"))
    alertas = []
    if estado != "no" and sw.alto:
        alertas.append(f"SWIFT {sw.puntaje}: riesgo alto de reingreso; egreso a intermedio y seguimiento de respuesta rápida")
    if estado == "listo" and p.criterio == "UCI":
        alertas.append("Cumple criterios de alta pero ORBIT sigue con criterio UCI: confirmar en la ronda")

    dias_hasta = 0 if estado == "listo" else 1 if estado == "casi" else max(1, ceil(_dias_restantes(p)))
    return EvaluacionAlta(p.cama, estado, destino, ok, falta, prob, p.fecha + timedelta(days=dias_hasta), sw, alertas)
