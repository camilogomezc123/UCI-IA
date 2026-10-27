"""Salidas del informe diario: Markdown para leer y JSON para tableros u otros sistemas."""

from __future__ import annotations

import json
from dataclasses import asdict
from enum import Enum

from .orquestador import InformeDiario, pesos
from .privacidad import limpiar_texto

_RESPONSABLE = {
    "medico_uci": "Médico UCI", "enfermeria": "Enfermería", "gestor_camas": "Gestor de camas",
    "autorizaciones": "Autorizaciones", "facturacion": "Facturación", "infectologia_proa": "Infectología / PROA",
    "sistemas": "Sistemas", "coordinacion": "Coordinación",
}
_PLAZO = {"inmediato": "Inmediato", "antes_10am": "Antes de las 10 am", "hoy": "Hoy", "esta_semana": "Esta semana"}


def _pesos(valor: float | None) -> str:
    return pesos(valor) if valor else "—"


def a_markdown(inf: InformeDiario) -> str:
    ind = inf.indicadores
    c = ind["condicion"]
    fin = ind["financiero"]
    sev = ind["discrepancias_por_severidad"]
    l = [
        f"# UCI CONTROL AI · Informe del {inf.fecha:%d/%m/%Y}",
        "",
        f"_{'Resumen y prioridades redactados con Claude y verificados por reglas.' if inf.generado_con_ia else 'Priorización por reglas (sin IA).'}_",
        "",
        "## Resumen",
        "",
        inf.resumen,
        "",
    ]
    if inf.alertas:
        l += ["## Alertas", ""]
        for a in inf.alertas:
            camas = f" ({', '.join(a['camas'])})" if a["camas"] else ""
            l.append(f"- **{a['nivel'].upper()}**{camas}: {limpiar_texto(a['mensaje'])}")
        l.append("")

    l += [
        "## Indicadores",
        "",
        "| Indicador | Valor |",
        "|---|---|",
        f"| Camas ocupadas | {ind['camas_ocupadas']} de {ind['camas_en_censo']} ({ind['ocupacion_pct']}%) |",
        f"| Camas ocupadas con entrega de turno | {ind['camas_con_entrega']} |",
        f"| Condición UCI / UCIN / piso | {c['UCI']} / {c['UCIN']} / {c['PISO']} ({ind['condicion_piso_pct']}% piso) |",
        f"| Ventilación invasiva | {ind['ventilacion_invasiva']} |",
        f"| Soporte vasoactivo | {ind['soporte_vasoactivo']} |",
        f"| Terapia de reemplazo renal | {ind['terapia_reemplazo_renal']} |",
        f"| Dispositivos con más de 7 días | {ind['dispositivos_mas_de_7_dias']} |",
        f"| Discrepancias (crítica / alta / media / baja) | {sev['critica']} / {sev['alta']} / {sev['media']} / {sev['baja']} |",
        f"| Estancia evitable del día (estimada) | {_pesos(fin['costo_estancia_evitable_dia_cop'])} |",
        f"| Derecho de cama sin cargar | {_pesos(fin['derecho_cama_sin_cargar_cop'])} |",
        f"| Estancia UCI en riesgo de glosa | {_pesos(fin['estancia_uci_en_riesgo_de_glosa_cop'])} |",
        "",
        f"_{fin['supuestos']}_",
        "",
        "## Prioridades del día",
        "",
        "| # | Cama | Acción | Responsable | Plazo | Motivo | Ref. |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in inf.prioridades:
        l.append(
            f"| {p['orden']} | {p['cama']} | {p['accion']} | {_RESPONSABLE.get(p['responsable'], p['responsable'])} | "
            f"{_PLAZO.get(p['plazo'], p['plazo'])} | {limpiar_texto(p['motivo'])} | {', '.join(p['discrepancias'])} |"
        )
    l += ["", "## Discrepancias detectadas", "", "| Id | Severidad | Cama | Regla | Detalle | Impacto |", "|---|---|---|---|---|---|"]
    for d in inf.conciliacion.discrepancias:
        l.append(
            f"| {d.id} | {d.severidad.value} | {d.cama or 'Unidad'} | {d.regla} · {d.titulo} | "
            f"{limpiar_texto(d.detalle)} | {_pesos(d.impacto_cop)} |"
        )
    if inf.verificaciones_datos or inf.observaciones_verificacion:
        l += ["", "## Calidad de los datos y verificación", ""]
        l += [f"- {v}" for v in inf.verificaciones_datos]
        l += [f"- Verificación del orquestador: {o}" for o in inf.observaciones_verificacion]
    l.append("")
    return "\n".join(l)


def a_json(inf: InformeDiario) -> str:
    def convertir(o):
        if isinstance(o, Enum):
            return o.value
        if hasattr(o, "isoformat"):
            return o.isoformat()
        raise TypeError(type(o))

    datos = {
        "fecha": inf.fecha.isoformat(),
        "generado_con_ia": inf.generado_con_ia,
        "resumen": inf.resumen,
        "indicadores": inf.indicadores,
        "alertas": inf.alertas,
        "prioridades": inf.prioridades,
        "discrepancias": [asdict(d) for d in inf.conciliacion.discrepancias],
        "verificaciones_datos": inf.verificaciones_datos,
        "observaciones_verificacion": inf.observaciones_verificacion,
    }
    return json.dumps(datos, ensure_ascii=False, indent=2, default=convertir)
