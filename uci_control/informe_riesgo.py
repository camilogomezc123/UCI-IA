"""Informe de riesgo y sugerencias por paciente, en Markdown y JSON.

Identifica a cada paciente solo por cama. No incluye nombre, documento ni seudónimo.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict

from .conectores.orbit_tablero import PacienteTablero
from .prediccion import evaluar, sugerir


def _pesos(v: float) -> str:
    return f"${v:,.0f}".replace(",", ".")


def construir(pacientes: list[PacienteTablero]) -> list[dict]:
    filas = []
    for p in pacientes:
        ev = evaluar(p)
        filas.append({"cama": p.cama, "criterio": p.criterio, "evaluacion": asdict(ev),
                      "sugerencias": [asdict(s) for s in sugerir(p, ev)]})
    return filas


def a_json(pacientes: list[PacienteTablero]) -> str:
    return json.dumps(construir(pacientes), ensure_ascii=False, indent=1, default=str)


def a_markdown(pacientes: list[PacienteTablero]) -> str:
    filas = construir(pacientes)
    fecha = pacientes[0].fecha.isoformat() if pacientes else ""
    sugerencias = [s for f in filas for s in f["sugerencias"]]
    lineas = [f"# Riesgo y gestión por paciente, {fecha}", "",
              f"{len(filas)} pacientes, {len(sugerencias)} sugerencias. "
              f"Valor estimado de los días que faltan: {_pesos(sum(f['evaluacion']['valor_en_juego'] for f in filas))}.", "",
              "Las sugerencias apoyan la gestión; la decisión clínica es del médico tratante. Los puntajes no están "
              "calibrados todavía con desenlaces de la clínica.", "", "## Acciones por servicio", ""]
    for servicio, n in Counter(s["servicio"] for s in sugerencias).most_common():
        lineas.append(f"- {servicio}: {n}")
    lineas += ["", "## Acciones inmediatas y de hoy", "", "| Cama | Servicio | Acción | Motivo |", "|---|---|---|---|"]
    for s in sugerencias:
        if s["prioridad"] in ("inmediata", "hoy"):
            lineas.append(f"| {s['cama']} | {s['servicio']} | {s['accion']} | {s['motivo']} |")
    lineas += ["", "## Riesgo por paciente", "",
               "| Cama | Criterio | Mortalidad | Deterioro | Estancia prolongada | Infección | Egreso próximo | Alertas |",
               "|---|---|---|---|---|---|---|---|"]
    for f in filas:
        e = f["evaluacion"]
        lineas.append(f"| {f['cama']} | {f['criterio']} | {e['mortalidad']['nivel']} | {e['deterioro']['nivel']} | "
                      f"{e['estancia_prolongada']['nivel']} | {e['infeccion']['nivel']} | {e['egreso_proximo']['nivel']} | "
                      f"{'; '.join(e['alertas'])} |")
    lineas += ["", "## Acciones de 48 horas y de la semana", ""]
    for s in sugerencias:
        if s["prioridad"] in ("48h", "semana"):
            lineas.append(f"- {s['cama']}, {s['servicio']}: {s['accion']} ({s['motivo']})")
    return "\n".join(lineas) + "\n"
