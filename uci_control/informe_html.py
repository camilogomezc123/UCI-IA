"""Informe en HTML: una página interactiva (adjunto) y un resumen estático para el cuerpo del correo.

Los clientes de correo no ejecutan JavaScript, por eso el cuerpo lleva solo tablas con estilos
en línea y la página completa va adjunta.
"""

from __future__ import annotations

import json
from html import escape
from importlib import resources

from .orquestador import InformeDiario, pesos

_CABECERA = '<!doctype html>\n<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">\n'


def pagina(datos: dict) -> str:
    """`datos` = {"informe": dict, "riesgo": list, "adherencia": list, "demo": bool}."""
    plantilla = resources.files("uci_control").joinpath("plantillas/informe.html").read_text(encoding="utf-8")
    carga = json.dumps(datos, ensure_ascii=False, default=str).replace("</", "<\\/")
    return _CABECERA + plantilla.replace("/*DATOS*/null", carga) + "\n</html>\n"


_COLOR = {"inmediata": "#b3261e", "inmediato": "#b3261e", "hoy": "#c25e00", "48h": "#8a6d00", "semana": "#2e6b3a",
          "critica": "#b3261e", "alta": "#c25e00"}


def correo(inf: InformeDiario, riesgo: list[dict], adherencia: list[dict]) -> str:
    """Resumen para el cuerpo del correo: indicadores, alertas, prioridades inmediatas y sugerencias de hoy."""
    ind = inf.indicadores
    td = 'style="padding:6px 10px;border-bottom:1px solid #dfe5e4;vertical-align:top"'
    th = 'style="padding:6px 10px;border-bottom:2px solid #dfe5e4;text-align:left;font-size:12px;color:#5b6b69"'

    def tabla(encabezados, filas):
        cab = "".join(f"<th {th}>{escape(h)}</th>" for h in encabezados)
        cuerpo = "".join("<tr>" + "".join(f"<td {td}>{c}</td>" for c in f) + "</tr>" for f in filas)
        return f'<table style="border-collapse:collapse;font-size:14px;width:100%">{cab and "<tr>" + cab + "</tr>"}{cuerpo}</table>'

    def etiqueta(t):
        return f'<b style="color:{_COLOR.get(t, "#5b6b69")}">{escape(t)}</b>'

    partes = [
        '<div style="font-family:Arial,Helvetica,sans-serif;color:#14211f;max-width:760px">',
        f'<h2 style="margin:0 0 4px">UCI CONTROL AI · {inf.fecha:%d/%m/%Y}</h2>',
        f'<p style="margin:0 0 12px">{escape(inf.resumen)}</p>',
        tabla([], [[f"<b>{ind['camas_ocupadas']}/{ind['camas_en_censo']}</b> ocupadas",
                    f"<b>{ind['condicion']['PISO']}</b> en condición piso",
                    f"<b>{ind['ventilacion_invasiva']}</b> en VMI",
                    f"<b>{ind['soporte_vasoactivo']}</b> con vasoactivo",
                    f"<b>{escape(pesos(ind['financiero']['costo_estancia_evitable_dia_cop']))}</b> estancia evitable"]]),
    ]
    if inf.alertas:
        partes.append('<h3 style="margin:16px 0 6px;color:#b3261e">Alertas críticas</h3>')
        partes.append(tabla([], [[escape(a["mensaje"]) + (f" ({escape(', '.join(a['camas']))})" if a["camas"] else "")] for a in inf.alertas]))
    inmediatas = [p for p in inf.prioridades if p["plazo"] == "inmediato"]
    if inmediatas:
        partes.append('<h3 style="margin:16px 0 6px">Prioridades inmediatas</h3>')
        partes.append(tabla(["Cama", "Acción", "Responsable"], [[escape(p["cama"]), escape(p["accion"]), escape(p["responsable"].replace("_", " "))] for p in inmediatas]))
    hoy = [s for p in riesgo for s in p["sugerencias"] if s["prioridad"] in ("inmediata", "hoy")]
    if hoy:
        partes.append(f'<h3 style="margin:16px 0 6px">Sugerencias para hoy ({len(hoy)})</h3>')
        partes.append(tabla(["Cama", "Servicio", "Acción", "Código"], [
            [escape(s["cama"]), escape(s["servicio"]), f'{etiqueta(s["prioridad"])} {escape(s["accion"])}<br><span style="color:#5b6b69;font-size:13px">{escape(s["motivo"])}</span>',
             escape(s["seguimiento"]["id"]) + (f'<br><span style="color:#b3261e;font-size:12px">{s["seguimiento"]["dias_abierta"]} d abierta</span>' if s.get("seguimiento") and s["seguimiento"]["dias_abierta"] >= 2 else "") if s.get("seguimiento") else ""]
            for s in hoy]))
    if adherencia:
        partes.append('<h3 style="margin:16px 0 6px">Adherencia por servicio (acumulada)</h3>')
        partes.append(tabla(["Servicio", "Total", "Gestionadas", "Pendientes"], [[escape(d["servicio"]), d["total"], f'{d["gestionadas_pct"]}%', d["pendiente"]] for d in adherencia]))
    partes.append('<p style="color:#5b6b69;font-size:12px;margin-top:16px">El informe completo va adjunto. Las sugerencias apoyan la gestión; la decisión clínica es del médico tratante. Pacientes identificados solo por cama.</p></div>')
    return "\n".join(partes)
