"""Indicadores del día calculados de forma determinística a partir de la conciliación."""

from __future__ import annotations

from collections import Counter

from . import config
from .conciliacion.motor import ResultadoConciliacion
from .modelos import Condicion


def pesos(valor: float | None) -> str:
    return f"${valor:,.0f}".replace(",", ".") if valor else "$0"


def calcular(resultado: ResultadoConciliacion) -> dict:
    exps = list(resultado.expedientes.values())
    en_censo = [e for e in exps if e.orbit]
    ocupadas = [e for e in en_censo if e.orbit.ocupada]
    condiciones = Counter(e.condicion.value for e in ocupadas)

    def alguna(e, campo_orbit, campo_entrega):
        o = getattr(e.orbit, campo_orbit, None) if e.orbit else None
        v = getattr(e.entrega, campo_entrega, None) if e.entrega else None
        return bool(o) or bool(v)

    dias_dispositivo: Counter = Counter()
    dispositivos_prolongados = 0
    for e in ocupadas:
        for d in (e.entrega.dispositivos if e.entrega else []):
            if d.fecha_insercion:
                dias = (resultado.fecha - d.fecha_insercion).days
                dias_dispositivo[d.tipo.upper()] += dias
                dispositivos_prolongados += dias > config.DIAS_DISPOSITIVO_REVISAR

    pendientes = Counter(p.tipo for e in ocupadas if e.entrega for p in e.entrega.pendientes)
    reglas = Counter(d.regla for d in resultado.discrepancias)
    impacto = lambda regla: sum(d.impacto_cop or 0 for d in resultado.discrepancias if d.regla == regla)
    n_piso = condiciones.get(Condicion.PISO.value, 0)

    return {
        "fecha": resultado.fecha.isoformat(),
        "camas_en_censo": len(en_censo),
        "camas_ocupadas": len(ocupadas),
        "ocupacion_pct": round(100 * len(ocupadas) / len(en_censo), 1) if en_censo else None,
        "camas_con_entrega": sum(1 for e in ocupadas if e.entrega),
        "condicion": {c.value: condiciones.get(c.value, 0) for c in Condicion},
        "condicion_piso_pct": round(100 * n_piso / len(ocupadas), 1) if ocupadas else None,
        "ventilacion_invasiva": sum(alguna(e, "vmi", "vmi") for e in ocupadas),
        "soporte_vasoactivo": sum(alguna(e, "vasopresor", "vasoactivos") for e in ocupadas),
        "terapia_reemplazo_renal": sum(bool(e.entrega and e.entrega.trr) for e in ocupadas),
        "dias_dispositivo": dict(dias_dispositivo),
        "dispositivos_mas_de_7_dias": dispositivos_prolongados,
        "pendientes_por_tipo": dict(pendientes),
        "discrepancias_por_severidad": resultado.por_severidad(),
        "discrepancias_por_regla": dict(sorted(reglas.items())),
        "financiero": {
            "costo_estancia_evitable_dia_cop": impacto("R14"),
            "derecho_cama_sin_cargar_cop": impacto("R12"),
            "estancia_uci_en_riesgo_de_glosa_cop": impacto("R16"),
            "costo_espera_autorizaciones_cop": impacto("R09"),
            "supuestos": (
                f"Costo día UCI {pesos(config.COSTO_DIA_UCI)} y tarifa de cama {pesos(config.TARIFA_CAMA_UCI)} "
                "(promedios de la ejecución presupuestal de septiembre; no son costos por episodio)."
            ),
        },
    }
