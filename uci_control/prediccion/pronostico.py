"""Pronóstico mensual de indicadores con suavizamiento exponencial de Holt.

Series cortas (8 a 12 meses) no sostienen modelos con estacionalidad; Holt con tendencia
amortiguada da un pronóstico conservador y un intervalo a partir del error de un paso.
Los parámetros se eligen por búsqueda en rejilla minimizando ese error.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from math import sqrt


@dataclass
class Pronostico:
    serie: str
    historia: list[float]
    valores: list[float]
    inferior: list[float]
    superior: list[float]
    alfa: float
    beta: float
    phi: float
    rmse: float


def _holt(y: list[float], alfa: float, beta: float, phi: float) -> tuple[float, float, list[float]]:
    nivel, tendencia = y[0], (y[1] - y[0]) if len(y) > 1 else 0.0
    errores = []
    for v in y[1:]:
        previsto = nivel + phi * tendencia
        errores.append(v - previsto)
        nuevo = alfa * v + (1 - alfa) * previsto
        tendencia = beta * (nuevo - nivel) + (1 - beta) * phi * tendencia
        nivel = nuevo
    return nivel, tendencia, errores


def holt(serie: str, y: list[float], horizonte: int = 3, z: float = 1.645) -> Pronostico:
    """Pronóstico a `horizonte` pasos con intervalo aproximado del 90%."""
    y = [float(v) for v in y if v is not None]
    if len(y) < 3:
        raise ValueError("Se necesitan al menos 3 puntos")
    mejor = None
    rejilla = [0.1, 0.2, 0.3, 0.5, 0.7, 0.9]
    for a, b, f in product(rejilla, [0.05, 0.1, 0.2, 0.3], [0.8, 0.9, 0.98]):
        nivel, tend, err = _holt(y, a, b, f)
        sse = sum(e * e for e in err[1:])  # el primer error depende de la inicialización
        if mejor is None or sse < mejor[0]:
            mejor = (sse, a, b, f, nivel, tend, err)
    sse, a, b, f, nivel, tend, err = mejor
    rmse = sqrt(sse / max(len(err) - 1, 1))
    valores, inf, sup, acum = [], [], [], 0.0
    for h in range(1, horizonte + 1):
        acum += f ** h
        v = nivel + acum * tend
        ancho = z * rmse * sqrt(1 + (h - 1) * a * a)
        valores.append(round(v, 3))
        inf.append(round(v - ancho, 3))
        sup.append(round(v + ancho, 3))
    return Pronostico(serie, y, valores, inf, sup, a, b, f, round(rmse, 3))
