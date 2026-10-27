"""Une criterios de alta, barreras, historial y plan de capacidad en una sola corrida."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from pathlib import Path

from .. import config
from ..conectores.orbit_tablero import PacienteTablero
from ..prediccion.riesgo import agrupador_diagnostico
from .alta import evaluar_alta
from .barreras import barreras
from .capacidad import PacientePlan, PlanCamas, plan_del_dia
from .historial import Historial


def _acciones(estado: str, destino: str, bs: list, dias_listo: int, alertas: list[str]) -> list[str]:
    if estado == "no":
        return []
    acciones = []
    if estado == "listo":
        acciones.append(f"Egreso a {destino} hoy antes del mediodía; no después de las {config.HORA_LIMITE_EGRESO}:00")
        if dias_listo >= 1:
            acciones.append(f"Lleva {dias_listo} día(s) listo sin salir: escalar en el comité de camas")
    else:
        acciones.append(f"Dejar listo el egreso a {destino} para mañana temprano (órdenes, conciliación de medicamentos, cama solicitada)")
    acciones += [f"{b.responsable}: {b.escalamiento}" for b in bs]
    acciones += alertas
    return acciones


def gestionar_camas(fecha: date, pacientes: list[PacienteTablero], historial: str | Path,
                    camas_totales: int | None = None, demanda_esperada: float | None = None,
                    laboratorios: dict[str, dict] | None = None) -> PlanCamas:
    """`laboratorios` opcional por cama con pafi, glasgow, paco2 y desde_piso para el SWIFT."""
    h = Historial(historial)
    try:
        plan_pacientes, fotos = [], []
        for p in pacientes:
            clave = p.seudonimo or p.cama
            previa = h.trayectoria(clave, fecha)
            dias_uci = max(previa.dias_uci, p.dias_hospitalizacion or 0)
            ev = evaluar_alta(p, dias_uci, (laboratorios or {}).get(p.cama), fecha)
            bs = barreras(p) if ev.estado != "no" else []
            if previa.fecha_esperada_original and ev.estado != "no" and previa.fecha_esperada_original < fecha:
                ev.alertas.append(f"Fecha esperada de egreso vencida ({previa.fecha_esperada_original:%d/%m})")
            fotos.append({"paciente": clave, "cama": p.cama, "criterio": p.criterio, "estado_alta": ev.estado,
                          "fecha_esperada": ev.fecha_esperada_egreso, "barreras": ",".join(b.codigo for b in bs)})
            valor = config.VALOR_DIA_POR_AGRUPADOR.get(agrupador_diagnostico(p.cie10), config.VALOR_DIA_UCI_PROMEDIO)
            plan_pacientes.append((clave, ev, bs, valor, p))
        h.guardar(fecha, fotos)
        resultado = []
        for clave, ev, bs, valor, p in plan_pacientes:
            t = h.trayectoria(clave, fecha)
            # El historial no sabe cuándo entraron los que ya estaban el primer día: se toma la
            # estancia que reporta el tablero como piso.
            dias_uci = max(t.dias_uci, int(p.dias_hospitalizacion or 0))
            resultado.append(PacientePlan(
                cama=p.cama, estado=ev.estado, destino=ev.destino, probabilidad_egreso_hoy=ev.probabilidad_egreso_hoy,
                fecha_esperada_egreso=ev.fecha_esperada_egreso, dias_uci=dias_uci, dias_listo_sin_salir=t.dias_listo_sin_salir,
                barreras=[asdict(b) for b in bs], acciones=_acciones(ev.estado, ev.destino, bs, t.dias_listo_sin_salir, ev.alertas),
                valor_dia=valor))
        flujo = h.indicadores_flujo(fecha)
        flujo["egresos_desde_el_tablero_anterior"] = len(h.egresos(fecha))
        return plan_del_dia(fecha, resultado, camas_totales, demanda_esperada, flujo)
    finally:
        h.cerrar()
