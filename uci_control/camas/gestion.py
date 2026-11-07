"""Une criterios de alta, barreras, historial y plan de capacidad en una sola corrida."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from pathlib import Path

import numpy as np

from .. import config
from ..conectores.orbit_tablero import PacienteTablero
from ..modelos import EntregaCama
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


def _pronostico(fecha: date, pacientes: list[PacienteTablero], dias_uci: list[int], plan: list[PacientePlan],
                h: Historial, historial: str | Path, camas_totales: int | None,
                entregas: list[EntregaCama] | None) -> list[dict]:
    """1. Probabilidad de egreso de hoy según los egresos de la clínica (UCI_MODELO_OCUPACION).
    2. Guarda el estado de la mañana para que el modelo del estado diario aprenda.
    3. Si ese modelo ya superó a las referencias en su validación, su probabilidad de egreso.
    4. Ocupación esperada de las próximas 7 mañanas. Las llegadas salen del historial del tablero
       cuando tiene 4 semanas o más; si no, del modelo. El primer día usa la probabilidad del
       modelo del estado diario cuando la hay."""
    from ..prediccion.ocupacion import filas_tablero, modelo_configurado, pronosticar_tablero
    from . import aprendizaje

    modelo = modelo_configurado()
    filas = filas_tablero(pacientes, dias_uci, fecha) if modelo is not None and pacientes else None
    if filas is not None:
        for pp, prob in zip(plan, modelo.probabilidad(filas)):
            pp.probabilidad_egreso_historica = round(float(prob), 3)
    estados = aprendizaje.EstadosDiarios(historial)
    try:
        extras = {pp.cama: {"p_historica": pp.probabilidad_egreso_historica} for pp in plan
                  if pp.probabilidad_egreso_historica is not None}
        estados.registrar(fecha, pacientes, entregas or [], extras)
        aprendido = aprendizaje.modelo_configurado(historial)
        if aprendido is not None and aprendido.supera_referencias:
            probabilidades = aprendizaje.probabilidades_de_hoy(estados, fecha, aprendido)
            for pp in plan:
                pp.probabilidad_egreso_aprendida = probabilidades.get(pp.cama)
    finally:
        estados.cerrar()
    if filas is None:
        return []
    hoy = [pp.probabilidad_egreso_aprendida for pp in plan]
    egreso_hoy = np.array(hoy, dtype=float) if all(v is not None for v in hoy) else None
    llegadas = h.llegadas(fecha, modelo.llegadas)
    return [asdict(d) for d in pronosticar_tablero(modelo, filas, fecha, llegadas, camas_totales or config.CAMAS_UCI_TOTAL,
                                                   egreso_hoy=egreso_hoy)]


def gestionar_camas(fecha: date, pacientes: list[PacienteTablero], historial: str | Path,
                    camas_totales: int | None = None, demanda_esperada: float | None = None,
                    laboratorios: dict[str, dict] | None = None, entregas: list[EntregaCama] | None = None) -> PlanCamas:
    """`laboratorios` opcional por cama con pafi, glasgow, paco2 y desde_piso para el SWIFT.
    `entregas`: camas de las entregas de turno de esta mañana, para el estado diario."""
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
        resultado, dias_lista = [], []
        for clave, ev, bs, valor, p in plan_pacientes:
            t = h.trayectoria(clave, fecha)
            # El historial no sabe cuándo entraron los que ya estaban el primer día: se toma la
            # estancia que reporta el tablero como piso.
            dias_uci = max(t.dias_uci, int(p.dias_hospitalizacion or 0))
            dias_lista.append(dias_uci)
            resultado.append(PacientePlan(
                cama=p.cama, estado=ev.estado, destino=ev.destino, probabilidad_egreso_hoy=ev.probabilidad_egreso_hoy,
                fecha_esperada_egreso=ev.fecha_esperada_egreso, dias_uci=dias_uci, dias_listo_sin_salir=t.dias_listo_sin_salir,
                barreras=[asdict(b) for b in bs], acciones=_acciones(ev.estado, ev.destino, bs, t.dias_listo_sin_salir, ev.alertas),
                valor_dia=valor))
        flujo = h.indicadores_flujo(fecha)
        flujo["egresos_desde_el_tablero_anterior"] = len(h.egresos(fecha))
        ocupacion = _pronostico(fecha, [p for *_, p in plan_pacientes], dias_lista, resultado, h, historial, camas_totales, entregas)
        return plan_del_dia(fecha, resultado, camas_totales, demanda_esperada, flujo, ocupacion)
    finally:
        h.cerrar()
