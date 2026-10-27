"""Evaluación de riesgo por paciente a partir del tablero de ORBIT.

Cada riesgo sale de escalas validadas que ORBIT ya calcula (SOFA, NEWS2, RASS, CPOT, IMS,
MUST) o de puntajes aditivos con factores conocidos de la literatura. Se devuelven los
factores que pesaron para que el equipo pueda auditar cada nivel.

Mientras no haya desenlaces por episodio (egreso, muerte, estancia final) unidos al tablero,
estos puntajes no están calibrados contra la población de la clínica. La línea 1 del
macroproyecto de investigación es justamente esa calibración.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import config
from ..conectores.orbit_tablero import PacienteTablero

_CAPITULOS = [
    ("A", "INFECCIOSO"), ("B", "INFECCIOSO"), ("C", "ONCO-HEMATO"), ("D0", "ONCO-HEMATO"),
    ("D1", "ONCO-HEMATO"), ("D2", "ONCO-HEMATO"), ("D3", "ONCO-HEMATO"), ("D4", "ONCO-HEMATO"),
    ("D5", "ONCO-HEMATO"), ("D6", "ONCO-HEMATO"), ("D7", "ONCO-HEMATO"), ("D8", "ONCO-HEMATO"),
    ("E", "METABOLICO"), ("F", "MENTAL"), ("G", "NEUROLOGICO"), ("I", "CARDIOVASCULAR"),
    ("J", "RESPIRATORIO"), ("K", "GASTRO"), ("L", "PIEL"), ("M", "MUSCULOESQUELETICO"),
    ("N", "RENAL"), ("O", "OBSTETRICO"), ("Q", "CONGENITO"), ("S", "TRAUMA"), ("T", "TRAUMA"),
]
# Códigos que acompañan pero no definen el motivo de UCI.
_SECUNDARIOS = ("E43", "E44", "E46", "Z", "R0", "R1", "R2", "R3", "R4", "R5", "R6", "I10", "E11", "E10", "E03")


def agrupador_diagnostico(cie10: list[str]) -> str:
    """Agrupador del libro de costos para el diagnóstico principal.

    El choque séptico (R572) y la sepsis cuentan como infecciosos aunque estén después.
    """
    if any(c.startswith(("R572", "A40", "A41")) for c in cie10):
        return "INFECCIOSO"
    principales = [c for c in cie10 if not c.startswith(_SECUNDARIOS)] or cie10
    for codigo in principales:
        for prefijo, grupo in _CAPITULOS:
            if codigo.startswith(prefijo):
                return grupo
    return "OTROS"


@dataclass
class Riesgo:
    nivel: str  # alto, medio, bajo
    puntaje: float
    factores: list[str] = field(default_factory=list)


@dataclass
class EvaluacionPaciente:
    cama: str
    agrupador: str
    mortalidad: Riesgo
    deterioro: Riesgo
    estancia_prolongada: Riesgo
    infeccion: Riesgo
    egreso_proximo: Riesgo
    dias_restantes_estimados: float
    valor_dia: float
    valor_en_juego: float  # valor facturable estimado de los días que faltan
    alertas: list[str] = field(default_factory=list)


def _nivel(puntaje: float, alto: float, medio: float) -> str:
    return "alto" if puntaje >= alto else "medio" if puntaje >= medio else "bajo"


def _mortalidad(p: PacienteTablero) -> Riesgo:
    factores = []
    m = p.sofa_mortalidad
    if m is None:
        return Riesgo("sin dato", 0, ["SOFA sin registrar en ORBIT"])
    factores.append(f"Mortalidad esperada por SOFA {m:.0%}")
    if p.edad and p.edad >= 80:
        factores.append("80 años o más")
    if p.oncologico:
        factores.append("Paciente oncológico")
    return Riesgo(_nivel(m, 0.30, 0.15), m, factores)


def _deterioro(p: PacienteTablero) -> Riesgo:
    """NEWS2 (umbral 5 y 7 del Royal College of Physicians) más soportes activos."""
    factores, puntos = [], 0.0
    if p.news is not None:
        puntos += p.news
        factores.append(f"NEWS {p.news:.0f}")
    if p.vasoactivo:
        puntos += 2
        factores.append(f"Soporte {p.soporte_hemodinamico.lower()}")
    if p.cpot is not None and p.cpot >= 3:
        puntos += 1
        factores.append(f"CPOT {p.cpot:.0f} (dolor no controlado)")
    if p.eva is not None and p.eva > 4:
        puntos += 1
        factores.append(f"EVA {p.eva:.0f}")
    if p.news is None:
        factores.append("NEWS sin registrar")
    return Riesgo(_nivel(puntos, 7, 5), puntos, factores)


def _estancia(p: PacienteTablero) -> Riesgo:
    """Factores de estancia prolongada en UCI: VMI, sepsis, disfunción orgánica, desnutrición,
    dependencia funcional y estancia acumulada."""
    factores, puntos = [], 0
    dias = p.dias_hospitalizacion or 0
    if dias >= config.DIAS_ESTANCIA_PROLONGADA:
        puntos += 3
        factores.append(f"{dias:.0f} días hospitalizado")
    elif dias >= config.ESTANCIA_MEDIA_UCI:
        puntos += 1
        factores.append(f"{dias:.0f} días hospitalizado (sobre la media de {config.ESTANCIA_MEDIA_UCI})")
    if p.vmi:
        puntos += 2
        factores.append("Ventilación mecánica invasiva")
    if agrupador_diagnostico(p.cie10) == "INFECCIOSO":
        puntos += 1
        factores.append("Sepsis o diagnóstico infeccioso")
    if p.sofa_mortalidad is not None and p.sofa_mortalidad >= 0.22:
        puntos += 1
        factores.append("SOFA con mortalidad esperada de 22% o más")
    if p.must_alto:
        puntos += 1
        factores.append("Riesgo nutricional alto (MUST)")
    if p.dependiente:
        puntos += 1
        factores.append(f"Barthel {p.barthel.lower()}")
    return Riesgo(_nivel(puntos, 5, 3), puntos, factores)


def _infeccion(p: PacienteTablero) -> Riesgo:
    """Riesgo de infección asociada a la atención: dispositivos, antimicrobianos de amplio
    espectro y estancia."""
    factores, puntos = [], 0
    dvmi = p.dias_vmi()
    if p.vmi:
        puntos += 2
        factores.append(f"VMI{f' hace {dvmi} días' if dvmi is not None else ''} (riesgo de NAV)")
    restringidos = [a.nombre for a in p.antimicrobianos if a.restringido]
    if restringidos:
        puntos += 1
        factores.append("Antimicrobianos de uso restringido: " + ", ".join(restringidos))
    if (p.dias_hospitalizacion or 0) >= 7:
        puntos += 1
        factores.append("Más de 7 días hospitalizado")
    if any("INFECCION" in r.upper() for r in p.riesgos):
        puntos += 1
        factores.append("Riesgo de IAAS marcado en ORBIT")
    return Riesgo(_nivel(puntos, 4, 2), puntos, factores)


def _egreso(p: PacienteTablero) -> Riesgo:
    """Probabilidad de que el paciente pueda salir de la cama UCI en las próximas 24-48 h."""
    factores, puntos = [], 0
    if p.criterio == "OTROS":
        puntos += 3
        factores.append("ORBIT ya lo marca con criterio de hospitalización o alta")
    elif p.criterio == "UCIN":
        puntos += 1
        factores.append("Criterio de cuidado intermedio")
    if p.sin_soportes:
        puntos += 1
        factores.append("Sin soporte vasoactivo ni ventilatorio invasivo")
    if p.news is not None and p.news < 5:
        puntos += 1
        factores.append(f"NEWS {p.news:.0f}")
    vent = (p.soporte_ventilatorio or "").upper()
    if "AIRE" in vent or "BAJO FLUJO" in vent:
        puntos += 1
        factores.append(p.soporte_ventilatorio)
    if not p.sin_soportes and p.criterio == "OTROS":
        factores.append("Inconsistente: criterio de piso con soporte activo; verificar el registro")
        puntos -= 2
    return Riesgo(_nivel(puntos, 5, 3), puntos, factores)


def _dias_restantes(p: PacienteTablero) -> float:
    """Supuesto editable mientras no haya un modelo calibrado con desenlaces reales."""
    if p.criterio == "OTROS" and p.sin_soportes:
        return 0.5
    dias = 1.5 if p.criterio == "UCIN" else 2.5
    if p.vmi:
        dias += 4
    if p.vasoactivo:
        dias += 2
    return dias


def evaluar(p: PacienteTablero) -> EvaluacionPaciente:
    agrupador = agrupador_diagnostico(p.cie10)
    valor_dia = config.VALOR_DIA_POR_AGRUPADOR.get(agrupador, config.VALOR_DIA_UCI_PROMEDIO)
    restantes = _dias_restantes(p)
    alertas = []
    if p.criterio == "OTROS" and not p.sin_soportes:
        alertas.append("Criterio de hospitalización con soporte activo: el registro de ORBIT no es coherente")
    if p.news is None and p.criterio != "OTROS":
        alertas.append("NEWS sin registrar")
    if p.sofa_mortalidad is None and p.criterio == "UCI":
        alertas.append("SOFA sin registrar en paciente con criterio UCI")
    return EvaluacionPaciente(
        cama=p.cama,
        agrupador=agrupador,
        mortalidad=_mortalidad(p),
        deterioro=_deterioro(p),
        estancia_prolongada=_estancia(p),
        infeccion=_infeccion(p),
        egreso_proximo=_egreso(p),
        dias_restantes_estimados=restantes,
        valor_dia=valor_dia,
        valor_en_juego=round(restantes * valor_dia),
        alertas=alertas,
    )
