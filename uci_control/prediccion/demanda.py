"""Demanda de camas UCI: solicitudes, rechazo y capacidad.

Trabaja sobre la trazabilidad de asignación de camas (RCRFM024) con una fila por solicitud y
las columnas servicio_origen, f_solicitud, servicio_destino, asignada (Sí/No), cie10,
asegurador, f_asignacion, f_entrega y f_llegada. El paciente llega ya seudonimizado.

Tres herramientas:
- metricas_solicitudes: tasa de asignación por origen, destino, día y franja, y tiempos.
- modelo_rechazo: regresión logística que estima la probabilidad de que una solicitud no
  reciba cama, con AUC por validación cruzada.
- erlang_b: probabilidad de que una solicitud encuentre la unidad llena con c camas y una
  carga ofrecida dada (llegadas por día por estancia media). Sirve para simular escenarios:
  liberar camas ocupadas por pacientes con criterio de piso o reducir la estancia.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


def _franja(hora: int) -> str:
    return "00-06" if hora < 6 else "06-12" if hora < 12 else "12-18" if hora < 18 else "18-24"


def preparar(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["asignada_bin"] = d["asignada"].astype(str).str.upper().str.startswith("S").astype(int)
    d["dia_semana"] = d["f_solicitud"].dt.dayofweek
    d["franja"] = d["f_solicitud"].dt.hour.map(_franja)
    d["capitulo"] = d["cie10"].astype(str).str[:1]
    d["horas_asignacion"] = (d["f_asignacion"] - d["f_solicitud"]).dt.total_seconds() / 3600
    d["horas_llegada"] = (d["f_llegada"] - d["f_entrega"]).dt.total_seconds() / 3600
    return d


def metricas_solicitudes(df: pd.DataFrame) -> dict:
    d = preparar(df)
    por_dia = d.groupby(d["f_solicitud"].dt.date).agg(solicitudes=("asignada_bin", "size"), asignadas=("asignada_bin", "sum"))
    asig = d[d["asignada_bin"] == 1]

    def tasa(col):
        g = d.groupby(col)["asignada_bin"].agg(["size", "mean"])
        return {str(k): {"solicitudes": int(r["size"]), "asignacion": round(float(r["mean"]), 3)} for k, r in g.iterrows()}

    def cuantiles(s):
        s = s[(s >= 0) & (s < 24 * 7)].dropna()
        return {q: round(float(s.quantile(v)), 1) for q, v in (("p50", .5), ("p75", .75), ("p90", .9))} if len(s) else {}

    return {
        "solicitudes": int(len(d)),
        "tasa_asignacion": round(float(d["asignada_bin"].mean()), 3),
        "solicitudes_dia": {"media": round(float(por_dia["solicitudes"].mean()), 1),
                            "desv": round(float(por_dia["solicitudes"].std()), 1),
                            "max": int(por_dia["solicitudes"].max())},
        "asignadas_dia": round(float(por_dia["asignadas"].mean()), 1),
        "horas_hasta_asignacion": cuantiles(asig["horas_asignacion"]),
        "horas_entrega_a_llegada": cuantiles(asig["horas_llegada"]),
        "por_origen": tasa("servicio_origen"),
        "por_destino": tasa("servicio_destino"),
        "por_dia_semana": tasa("dia_semana"),
        "por_franja": tasa("franja"),
    }


@dataclass
class ModeloRechazo:
    auc: float
    auc_desv: float
    tasa_base: float
    coeficientes: dict[str, float]  # odds ratio por categoría frente a la de referencia
    modelo: object
    columnas: list[str]

    def probabilidad(self, solicitud: pd.DataFrame) -> pd.Series:
        X = _matriz(preparar(solicitud)).reindex(columns=self.columnas, fill_value=0)
        return pd.Series(self.modelo.predict_proba(X)[:, 1], index=solicitud.index)


_VARIABLES = ["servicio_origen", "servicio_destino", "dia_semana", "franja", "capitulo", "asegurador_top"]


def _matriz(d: pd.DataFrame, top_aseguradores: list[str] | None = None) -> pd.DataFrame:
    d = d.copy()
    top = top_aseguradores or d["asegurador"].value_counts().head(5).index.tolist()
    d["asegurador_top"] = d["asegurador"].where(d["asegurador"].isin(top), "OTRO")
    cap = d["capitulo"].value_counts()
    d["capitulo"] = d["capitulo"].where(d["capitulo"].map(cap) >= 10, "OTRO")
    d["dia_semana"] = d["dia_semana"].astype(str)
    return pd.get_dummies(d[_VARIABLES].astype(str), drop_first=True, dtype=float)


def modelo_rechazo(df: pd.DataFrame, pliegues: int = 5, semilla: int = 7) -> ModeloRechazo:
    """Regresión logística regularizada para P(no asignada). AUC por validación cruzada estratificada."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold, cross_val_score

    import numpy as np

    d = preparar(df)
    X = _matriz(d)
    y = 1 - d["asignada_bin"]
    modelo = LogisticRegression(C=0.5, max_iter=2000)
    cv = StratifiedKFold(pliegues, shuffle=True, random_state=semilla)
    aucs = cross_val_score(modelo, X, y, cv=cv, scoring="roc_auc")
    modelo.fit(X, y)
    coef = {c: round(float(np.exp(b)), 2) for c, b in zip(X.columns, modelo.coef_[0])}
    return ModeloRechazo(round(float(aucs.mean()), 3), round(float(aucs.std()), 3), round(float(y.mean()), 3),
                         dict(sorted(coef.items(), key=lambda kv: -abs(np.log(kv[1])))), modelo, list(X.columns))


def erlang_b(camas: int, carga: float) -> float:
    """Probabilidad de bloqueo de un sistema M/G/c/c (fórmula recursiva, estable)."""
    b = 1.0
    for k in range(1, camas + 1):
        b = carga * b / (k + carga * b)
    return b


@dataclass
class Escenario:
    nombre: str
    camas_efectivas: float
    estancia: float
    carga: float
    bloqueo: float
    admisiones_dia: float  # admisiones atendidas por día = llegadas * (1 - bloqueo)


def escenarios_capacidad(camas: int, llegadas_dia: float, estancia: float, camas_bloqueadas: float = 0.0,
                         reduccion_estancia: float = 0.5) -> list[Escenario]:
    """Compara la situación actual con liberar camas bloqueadas y con reducir la estancia.

    llegadas_dia son las solicitudes (demanda ofrecida), no las admisiones; estancia en días.
    camas_bloqueadas: camas ocupadas por pacientes con criterio de piso, que no rotan.
    """
    def esc(nombre, c, e):
        c_int = int(round(c))
        a = llegadas_dia * e
        pb = erlang_b(c_int, a)
        return Escenario(nombre, c, e, round(a, 1), round(pb, 3), round(llegadas_dia * (1 - pb), 2))

    efectivas = camas - camas_bloqueadas
    return [
        esc("Actual", efectivas, estancia),
        esc("Liberar camas con criterio de piso", camas, estancia),
        esc(f"Reducir estancia {reduccion_estancia} días", efectivas, estancia - reduccion_estancia),
        esc("Ambas", camas, estancia - reduccion_estancia),
    ]
