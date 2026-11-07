"""Gestión de camas centrada en el egreso oportuno.

Modelos de referencia (ver docs/GESTION_CAMAS.md):
- Criterios de alta de UCI y prioridad de egreso: guía SCCM 2016 de admisión, egreso y triage.
- SAFER y Red2Green (NHS): fecha esperada de egreso, criterios clínicos de alta, egreso temprano,
  revisión de pacientes estancados y días sin valor ("rojos").
- Real-Time Demand Capacity (IHI): cada mañana se predice capacidad (egresos) y demanda
  (ingresos) y se arma un plan para cerrar la brecha.
- OPEL (NHS England): niveles de escalamiento según la presión sobre la capacidad.
- SWIFT (Gajic 2008): riesgo de reingreso a UCI al momento del egreso.
"""

from .alta import EvaluacionAlta, evaluar_alta, swift
from .barreras import Barrera, barreras
from .capacidad import PlanCamas, plan_del_dia
from .gestion import gestionar_camas
from .historial import Historial

__all__ = ["EvaluacionAlta", "evaluar_alta", "swift", "Barrera", "barreras", "PlanCamas", "plan_del_dia", "Historial", "gestionar_camas"]
