"""Egreso diario de UCI y pronóstico de ocupación a siete días.

Dos preguntas del gestor de camas cada mañana:

1. ¿Quién sale hoy de la UCI? Un modelo de riesgo discreto en el tiempo (una fila por paciente
   y por día en UCI) estima la probabilidad de que el paciente ya no esté en el tablero de
   mañana, según lo que se sabía al ingreso, los días que lleva y el día de la semana. Se
   entrena con el consolidado de egresos: cada estancia de L días aporta L filas, la última con
   egreso.
2. ¿Cuántas camas vamos a tener ocupadas en los próximos siete días? Para cada paciente que hoy
   está en la UCI se multiplica la probabilidad de seguir día a día; a eso se suman las
   llegadas esperadas (tasa por día de la semana de las últimas semanas) por la probabilidad de
   que sigan. El resultado es la ocupación esperada, su intervalo del 80% y la probabilidad de
   llenar la unidad.

Con el consolidado se reconstruye el censo diario para medir el pronóstico contra lo que pasó.
La ubicación del tramo UCI en el calendario es exacta cuando la secuencia es UCI, UCI →
hospitalización o hospitalización → UCI (75% de los ingresos). En las demás se estima: los días
de piso previos a la UCI se reparten con la proporción observada en los casos exactos, y los
reingresos a UCI se tratan como un solo tramo.

Este modelo no conoce el estado diario (soportes, criterios de alta). Ese lo aprende
`camas/aprendizaje.py` con el historial del tablero; hasta que haya suficiente historial, el de
aquí es la referencia calibrada con los egresos de la clínica.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .trayectoria import HIPERPARAMETROS, JUEGOS, _niveles, preparar

NUMERICAS = JUEGOS["tablero"][0] + ["dias_en_uci", "dia_semana_egreso"]
CATEGORICAS = JUEGOS["tablero"][1]
DIAS_MAX = 60  # más allá, el riesgo diario se trata igual que el del día 60
HORIZONTE = 7


# ---------------------------------------------------------------- estancias y censo

def _tramos(secuencia: str) -> list[str]:
    return [s.strip().upper() for s in str(secuencia or "").split("→") if s.strip()]


def estancias_uci(crudo: pd.DataFrame) -> pd.DataFrame:
    """Una fila por ingreso con las variables de `preparar` más el tramo UCI en el calendario:
    inicio_uci, dias_en_uci_total y ubicacion_exacta."""
    c = crudo.sort_values("fecha_ingreso_min", kind="mergesort").reset_index(drop=True)
    x = preparar(c)
    tramos = c["secuencia_servicios"].map(_tramos)
    ingreso = pd.to_datetime(c["fecha_ingreso_min"]).dt.normalize()
    salida = pd.to_datetime(c["fecha_salida_max"]).dt.normalize()
    uci = c["dias_uci"].astype(int)
    piso = c["dias_hospitalizacion"].astype(int)
    tipo = tramos.map(lambda t: "U" if t == ["UCI"] else "UH" if t == ["UCI", "HOSPITALIZACION"]
                      else "HU" if t == ["HOSPITALIZACION", "UCI"] else "H*" if t and t[0] != "UCI" else "U*")
    previos = piso[tipo == "HU"].median()
    posteriores = piso[tipo == "UH"].median()
    fraccion = float(previos / (previos + posteriores)) if pd.notna(previos) and pd.notna(posteriores) and previos + posteriores > 0 else 0.5
    inicio = ingreso.copy()
    inicio[tipo == "HU"] = salida[tipo == "HU"] - pd.to_timedelta(uci[tipo == "HU"], "D")
    inicio[tipo == "H*"] = ingreso[tipo == "H*"] + pd.to_timedelta((piso[tipo == "H*"] * fraccion).round(), "D")
    x["inicio_uci"] = inicio
    x["dias_en_uci_total"] = uci
    x["ubicacion_exacta"] = tipo.isin(["U", "UH", "HU"])
    x.attrs["fraccion_piso_previo"] = round(fraccion, 3)
    return x


def censo_diario(e: pd.DataFrame, desde: str | date, hasta: str | date) -> pd.Series:
    """Pacientes en UCI cada día (los que entraron ese día y no salieron el mismo día cuentan)."""
    dias = pd.date_range(desde, hasta, freq="D")
    e = e[e["dias_en_uci_total"] >= 1]
    cambios = pd.concat([pd.Series(1, index=e["inicio_uci"]),
                         pd.Series(-1, index=e["inicio_uci"] + pd.to_timedelta(e["dias_en_uci_total"], "D"))])
    neto = cambios.groupby(level=0).sum().sort_index().cumsum()
    return neto.reindex(neto.index.union(dias)).ffill().fillna(0).reindex(dias).astype(int)


def dias_paciente(e: pd.DataFrame, desde: str | date, hasta: str | date) -> pd.DataFrame:
    """Una fila por paciente y por día en UCI entre `desde` y `hasta`. `sale` = 1 si ese es su
    último día (mañana ya no está en el tablero)."""
    e = e[e["dias_en_uci_total"] >= 1].reset_index(drop=True)
    largo = e["dias_en_uci_total"].to_numpy()
    fila = np.repeat(np.arange(len(e)), largo)
    k = np.concatenate([np.arange(n) for n in largo]) if len(largo) else np.array([], dtype=int)
    p = e.iloc[fila].reset_index(drop=True)
    p["fecha"] = p["inicio_uci"] + pd.to_timedelta(k, "D")
    p["dias_en_uci"] = np.minimum(k, DIAS_MAX).astype(float)
    p["sale"] = (k == largo[fila] - 1).astype(int)
    p["dia_semana_egreso"] = (p["fecha"] + pd.Timedelta(days=1)).dt.dayofweek.astype(float)
    return p[(p["fecha"] >= pd.Timestamp(desde)) & (p["fecha"] <= pd.Timestamp(hasta))].reset_index(drop=True)


# ---------------------------------------------------------------- modelo de egreso diario

def _matriz(x: pd.DataFrame, niveles: dict[str, list[str]]) -> pd.DataFrame:
    m = x.reindex(columns=NUMERICAS + CATEGORICAS).copy()
    for c in NUMERICAS:
        m[c] = pd.to_numeric(m[c], errors="coerce").astype(float)
    for c in CATEGORICAS:
        v = m[c].astype(object).where(m[c].isin(niveles[c]), "OTRO")
        m[c] = pd.Categorical(v, categories=niveles[c] + ["OTRO"])
    return m


@dataclass
class ModeloEgresoDiario:
    niveles: dict[str, list[str]]
    clasificador: object
    entrenado_hasta: str = ""
    n_dias_paciente: int = 0
    llegadas: "Llegadas | None" = None
    metricas: dict = field(default_factory=dict)
    ajuste: float = 0.0  # corrimiento del logit para la deriva reciente (ver recalibrar)

    def probabilidad(self, x: pd.DataFrame, ajuste: float | None = None) -> np.ndarray:
        """P(sale hoy) por fila; x trae las variables del tablero, dias_en_uci y dia_semana_egreso."""
        x = x.copy()
        x["dias_en_uci"] = np.minimum(pd.to_numeric(x["dias_en_uci"], errors="coerce"), DIAS_MAX)
        p = self.clasificador.predict_proba(_matriz(x, self.niveles))[:, 1]
        a = self.ajuste if ajuste is None else ajuste
        if a:
            p = np.clip(p, 1e-6, 1 - 1e-6)
            p = 1 / (1 + np.exp(-(np.log(p / (1 - p)) + a)))
        return p

    def recalibrar(self, recientes: pd.DataFrame) -> float:
        """Ajusta el corrimiento del logit para que los egresos predichos en `recientes`
        (días-paciente con desenlace conocido, por ejemplo las últimas 8 semanas) sumen los
        observados. Corrige la deriva entre reentrenamientos sin cambiar el orden de riesgo."""
        if recientes.empty or recientes["sale"].sum() == 0:
            self.ajuste = 0.0
            return 0.0
        p = np.clip(self.probabilidad(recientes, ajuste=0.0), 1e-6, 1 - 1e-6)
        z = np.log(p / (1 - p))
        objetivo = float(recientes["sale"].sum())
        lo, hi = -2.0, 2.0
        for _ in range(50):
            mid = (lo + hi) / 2
            if (1 / (1 + np.exp(-(z + mid)))).sum() < objetivo:
                lo = mid
            else:
                hi = mid
        self.ajuste = round((lo + hi) / 2, 4)
        return self.ajuste

    def permanencia(self, x: pd.DataFrame, fecha: date, horizonte: int = HORIZONTE,
                    primer_paso: np.ndarray | None = None) -> np.ndarray:
        """Matriz (pacientes × horizonte+1): probabilidad de seguir en UCI el día fecha + h.
        La columna 0 vale 1 (hoy están). `primer_paso` reemplaza la probabilidad de egreso del
        primer día (por ejemplo, la del modelo que conoce el estado de hoy)."""
        n = len(x)
        if n == 0 or horizonte == 0:
            return np.ones((n, horizonte + 1))
        filas = x.loc[x.index.repeat(horizonte)].reset_index(drop=True)
        paso = np.tile(np.arange(horizonte), n)
        filas["dias_en_uci"] = np.repeat(pd.to_numeric(x["dias_en_uci"], errors="coerce").fillna(0).to_numpy(), horizonte) + paso
        filas["dia_semana_egreso"] = [(fecha + timedelta(days=int(s) + 1)).weekday() for s in paso]
        h = self.probabilidad(filas).reshape(n, horizonte)
        if primer_paso is not None:
            h[:, 0] = np.asarray(primer_paso, dtype=float)
        return np.column_stack([np.ones(n), np.cumprod(1 - h, axis=1)])

    def guardar(self, ruta: Path) -> None:
        import joblib
        joblib.dump(self, ruta)

    @staticmethod
    def cargar(ruta: Path) -> "ModeloEgresoDiario":
        """Solo cargar archivos generados por este mismo agente: un .joblib puede ejecutar código."""
        import joblib
        return joblib.load(ruta)


def ajustar_egreso_diario(e: pd.DataFrame, desde: str, hasta: str) -> ModeloEgresoDiario:
    """Entrena con los días-paciente cuya fecha está entre `desde` y `hasta` (el desenlace de cada
    fila se conoce al día siguiente)."""
    from sklearn.ensemble import HistGradientBoostingClassifier

    dp = dias_paciente(e, desde, hasta)
    niveles = _niveles(e[e["inicio_uci"] <= pd.Timestamp(hasta)])
    clf = HistGradientBoostingClassifier(**{**HIPERPARAMETROS, "max_leaf_nodes": 15, "min_samples_leaf": 100})
    clf.fit(_matriz(dp, niveles), dp["sale"])
    return ModeloEgresoDiario(niveles, clf, str(pd.Timestamp(hasta).date()), len(dp))


# ---------------------------------------------------------------- llegadas

@dataclass
class Llegadas:
    """Ingresos a UCI por día de la semana (lunes = 0) y una muestra de los ingresos recientes
    para calcular cuánto suelen quedarse los que lleguen."""
    tasa: list[float]
    mezcla: pd.DataFrame
    semanas: int

    def esperadas(self, dia: date) -> float:
        return self.tasa[dia.weekday()]


def ajustar_llegadas(e: pd.DataFrame, hasta: str | date, semanas: int = 12, muestra: int = 200,
                     semilla: int = 7) -> Llegadas:
    fin = pd.Timestamp(hasta)
    inicio = fin - pd.Timedelta(weeks=semanas) + pd.Timedelta(days=1)
    e = e[e["dias_en_uci_total"] >= 1]
    recientes = e[(e["inicio_uci"] >= inicio) & (e["inicio_uci"] <= fin)]
    por_dia = recientes.groupby("inicio_uci").size().reindex(pd.date_range(inicio, fin, freq="D"), fill_value=0)
    tasa = por_dia.groupby(por_dia.index.dayofweek).mean().reindex(range(7), fill_value=float(por_dia.mean()))
    cols = JUEGOS["tablero"][0] + JUEGOS["tablero"][1]
    mezcla = recientes[cols].sample(min(muestra, len(recientes)), random_state=semilla) if len(recientes) else recientes[cols]
    return Llegadas([round(float(v), 3) for v in tasa], mezcla.reset_index(drop=True), semanas)


# ---------------------------------------------------------------- pronóstico de ocupación

def _distribucion(probabilidades: np.ndarray, media_poisson: float, maximo: int) -> np.ndarray:
    """Distribución exacta de (suma de Bernoulli independientes) + Poisson, truncada en `maximo`."""
    d = np.zeros(maximo + 1)
    d[0] = 1.0
    for p in probabilidades:
        d[1:] = d[1:] * (1 - p) + d[:-1] * p
        d[0] *= 1 - p
    k = np.arange(maximo + 1)
    pois = np.exp(-media_poisson + k * math.log(media_poisson) - np.array([math.lgamma(i + 1) for i in k])) if media_poisson > 0 \
        else (k == 0).astype(float)
    return np.convolve(d, pois)[: maximo + 1]


@dataclass
class DiaPronostico:
    fecha: date
    esperados: float
    p10: int
    p90: int
    siguen_de_hoy: float
    nuevos: float
    probabilidad_llena: float | None  # P(ocupación >= camas)


def pronosticar(modelo: ModeloEgresoDiario, presentes: pd.DataFrame, fecha: date, llegadas: Llegadas | None = None,
                camas: int | None = None, horizonte: int = HORIZONTE, primer_paso: np.ndarray | None = None) -> list[DiaPronostico]:
    """`presentes`: los pacientes en el censo del día `fecha` (los que siguen en la UCI al final
    del día) con las variables del tablero y dias_en_uci. El día h es el censo de fecha + h."""
    llegadas = llegadas or modelo.llegadas
    perm = modelo.permanencia(presentes, fecha, horizonte, primer_paso)
    # Los que lleguen el día j: hoy se calcula su permanencia con la mezcla de ingresos recientes.
    curvas = {}
    if llegadas is not None and len(llegadas.mezcla):
        m = llegadas.mezcla.copy()
        m["dias_en_uci"] = 0.0
        for j in range(1, horizonte + 1):
            dia = fecha + timedelta(days=j)
            curvas[j] = modelo.permanencia(m, dia, horizonte - j).mean(axis=0)
    maximo = int(len(presentes) + sum(llegadas.esperadas(fecha + timedelta(days=j)) for j in range(1, horizonte + 1)) * 3 + 20) \
        if llegadas else len(presentes) + 1
    salida = []
    for h in range(1, horizonte + 1):
        p = perm[:, h]
        nuevos = sum(llegadas.esperadas(fecha + timedelta(days=j)) * curvas[j][h - j] for j in range(1, h + 1)) if curvas else 0.0
        dist = _distribucion(p, nuevos, maximo)
        acumulada = np.cumsum(dist) / dist.sum()
        lleno = float(1 - acumulada[camas - 1]) if camas and camas - 1 < len(acumulada) else (0.0 if camas else None)
        salida.append(DiaPronostico(fecha + timedelta(days=h), round(float(p.sum() + nuevos), 1),
                                    int(np.searchsorted(acumulada, 0.1)), int(np.searchsorted(acumulada, 0.9)),
                                    round(float(p.sum()), 1), round(float(nuevos), 1),
                                    round(lleno, 3) if lleno is not None else None))
    return salida


# ---------------------------------------------------------------- validación

def _calibracion(y: np.ndarray, p: np.ndarray, grupos: int = 10) -> list[dict]:
    orden = pd.qcut(p, grupos, labels=False, duplicates="drop")
    return [{"predicho": round(float(p[orden == g].mean()), 3), "observado": round(float(y[orden == g].mean()), 3),
             "n": int((orden == g).sum())} for g in sorted(set(orden))]


def validar_egreso_diario(e: pd.DataFrame, inicio: str, corte: str, fin: str) -> tuple[ModeloEgresoDiario, dict]:
    """Entrena con los días-paciente antes de `corte` y mide en los de `corte` a `fin`."""
    from sklearn.metrics import brier_score_loss, roc_auc_score

    ultimo_ent = str((pd.Timestamp(corte) - pd.Timedelta(days=1)).date())
    modelo = ajustar_egreso_diario(e, inicio, ultimo_ent)
    ent = dias_paciente(e, inicio, ultimo_ent)
    pru = dias_paciente(e, corte, fin)
    p = modelo.probabilidad(pru)
    y = pru["sale"].to_numpy()
    # Referencia: el riesgo histórico según los días que lleva y el día de la semana.
    base = ent.groupby(["dias_en_uci", "dia_semana_egreso"])["sale"].mean()
    ref = base.reindex(pd.MultiIndex.from_frame(pru[["dias_en_uci", "dia_semana_egreso"]])).fillna(ent["sale"].mean()).to_numpy()
    diario = pd.DataFrame({"fecha": pru["fecha"], "y": y, "p": p, "ref": ref}).groupby("fecha").sum()
    por_dow = ent.groupby("fecha")["sale"].sum()
    por_dow = por_dow.groupby(por_dow.index.dayofweek).mean()
    ingenuo = diario.index.map(lambda f: por_dow.get(f.dayofweek, por_dow.mean())).to_numpy()
    r = {"n_entrenamiento": len(ent), "n_prueba": len(pru), "egresos_prueba": int(y.sum()),
         "entrenamiento": f"{inicio} a {ultimo_ent}", "prueba": f"{corte} a {fin}",
         "auc": round(float(roc_auc_score(y, p)), 3), "auc_dias_y_dia_semana": round(float(roc_auc_score(y, ref)), 3),
         "brier": round(float(brier_score_loss(y, p)), 4), "brier_dias_y_dia_semana": round(float(brier_score_loss(y, ref)), 4),
         "calibracion": _calibracion(y, p),
         "egresos_dia_observados": round(float(diario["y"].mean()), 2),
         "egresos_dia_predichos": round(float(diario["p"].mean()), 2),
         "error_egresos_dia": round(float(np.mean(np.abs(diario["y"] - diario["p"]))), 2),
         "error_egresos_dia_promedio_dia_semana": round(float(np.mean(np.abs(diario["y"] - ingenuo))), 2),
         "error_egresos_dia_dias_y_dia_semana": round(float(np.mean(np.abs(diario["y"] - diario["ref"]))), 2)}
    # Ordenar a los pacientes del día por probabilidad: ¿cuántos de los 5 primeros salen?
    pru = pru.assign(p=p)
    top = pru.sort_values("p", ascending=False).groupby("fecha").head(5)
    r["salen_entre_los_5_mas_probables"] = round(float(top["sale"].mean()), 3)
    r["salen_en_promedio"] = round(float(y.mean()), 3)
    return modelo, r


def validar_ocupacion(e: pd.DataFrame, modelo: ModeloEgresoDiario, corte: str, fin: str, horizonte: int = HORIZONTE,
                      cada: int = 2, camas: int | None = None, semanas_recalibracion: int | None = 8) -> dict:
    """Pronostica desde cada día de origen (uno cada `cada` días) y compara con el censo
    reconstruido. La referencia ingenua es suponer que la ocupación de hoy se mantiene."""
    fin_origen = pd.Timestamp(fin) - pd.Timedelta(days=horizonte)
    origenes = pd.date_range(corte, fin_origen, freq=f"{cada}D")
    censo = censo_diario(e, pd.Timestamp(corte) - pd.Timedelta(days=1), fin)
    e1 = e[e["dias_en_uci_total"] >= 1]
    filas = []
    for o in origenes:
        presentes = e1[(e1["inicio_uci"] <= o) & (e1["inicio_uci"] + pd.to_timedelta(e1["dias_en_uci_total"], "D") > o)].copy()
        presentes["dias_en_uci"] = (o - presentes["inicio_uci"]).dt.days.astype(float)
        llegadas = ajustar_llegadas(e, o - pd.Timedelta(days=1))
        if semanas_recalibracion:
            modelo.recalibrar(dias_paciente(e, o - pd.Timedelta(weeks=semanas_recalibracion), o - pd.Timedelta(days=1)))
        for d in pronosticar(modelo, presentes, o.date(), llegadas, camas, horizonte):
            real = int(censo.get(pd.Timestamp(d.fecha), np.nan))
            filas.append({"origen": o, "h": (pd.Timestamp(d.fecha) - o).days, "real": real, "esperado": d.esperados,
                          "p10": d.p10, "p90": d.p90, "hoy": int(censo[o]), "lleno": d.probabilidad_llena})
    modelo.ajuste = 0.0
    f = pd.DataFrame(filas)
    por_h = []
    for h, g in f.groupby("h"):
        por_h.append({"horizonte": int(h), "error_modelo": round(float((g["real"] - g["esperado"]).abs().mean()), 2),
                      "error_ingenuo": round(float((g["real"] - g["hoy"]).abs().mean()), 2),
                      "sesgo": round(float((g["esperado"] - g["real"]).mean()), 2),
                      "cobertura_80": round(float(((g["real"] >= g["p10"]) & (g["real"] <= g["p90"])).mean()), 3)})
    r = {"origenes": len(origenes), "censo_medio": round(float(censo.mean()), 1), "por_horizonte": por_h,
         "serie": [{"fecha": str(o.date()), "real_h1": int(g[g.h == 1]["real"].iloc[0]),
                    "esperado_h1": float(g[g.h == 1]["esperado"].iloc[0]),
                    "real_h7": int(g[g.h == horizonte]["real"].iloc[0]), "esperado_h7": float(g[g.h == horizonte]["esperado"].iloc[0]),
                    "p10_h7": int(g[g.h == horizonte]["p10"].iloc[0]), "p90_h7": int(g[g.h == horizonte]["p90"].iloc[0])}
                   for o, g in f.groupby("origen")]}
    if camas:
        g = f[f.h == 1]
        lleno_real = (g["real"] >= camas).astype(int)
        r["camas"] = camas
        r["dias_llenos_reales_h1"] = int(lleno_real.sum())
        if lleno_real.nunique() > 1:
            from sklearn.metrics import roc_auc_score
            r["auc_lleno_h1"] = round(float(roc_auc_score(lleno_real, g["lleno"])), 3)
    return r


# ---------------------------------------------------------------- entrenamiento desde el archivo

def entrenar_desde_archivo(ruta: Path, corte: str | None = None, fin: str | None = None,
                           camas: int | None = None) -> tuple[ModeloEgresoDiario, dict]:
    from .trayectoria import leer_egresos

    crudo = leer_egresos(ruta)
    e = estancias_uci(crudo)
    salida = pd.to_datetime(crudo["fecha_salida_max"])
    inicio = str(salida.min().to_period("M").start_time.date())
    fin = fin or str((salida.max() - pd.DateOffset(months=3)).date())
    if corte is None:
        dias = pd.date_range(inicio, fin, freq="D")
        corte = str(dias[int(len(dias) * 0.7)].to_period("M").start_time.date())
    modelo_val, val = validar_egreso_diario(e, inicio, corte, fin)
    ocup = validar_ocupacion(e, modelo_val, corte, fin, camas=camas)
    final = ajustar_egreso_diario(e, inicio, fin)
    final.llegadas = ajustar_llegadas(e, fin)
    # Igual que en la validación: el nivel de egresos se ajusta a las últimas 8 semanas.
    final.recalibrar(dias_paciente(e, pd.Timestamp(fin) - pd.Timedelta(weeks=8), fin))
    censo = censo_diario(e, inicio, fin)
    informe = {"ingresos": len(e), "ubicacion_exacta": round(float(e["ubicacion_exacta"].mean()), 3),
               "fraccion_piso_previo": e.attrs.get("fraccion_piso_previo"), "ventana": [inicio, fin], "corte": corte,
               "censo_medio": round(float(censo.mean()), 1), "censo_p90": int(censo.quantile(0.9)), "censo_max": int(censo.max()),
               "llegadas_por_dia_semana": final.llegadas.tasa, "ajuste_ultimas_8_semanas": final.ajuste,
               "egreso_diario": val, "ocupacion": ocup}
    final.metricas = informe
    return final, informe


_DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def a_markdown(inf: dict) -> str:
    v, o = inf["egreso_diario"], inf["ocupacion"]
    l = ["# Egreso diario de UCI y pronóstico de ocupación", "",
         f"{inf['ingresos']} ingresos; tramo UCI ubicado exactamente en el calendario en {inf['ubicacion_exacta']:.0%} "
         f"(en el resto, los días de piso previos a la UCI se estiman como {inf['fraccion_piso_previo']:.0%} de los días de piso). "
         f"Censo reconstruido {inf['ventana'][0]} a {inf['ventana'][1]}: media {inf['censo_medio']}, percentil 90 {inf['censo_p90']}, "
         f"máximo {inf['censo_max']} pacientes.", "",
         "## ¿Quién sale hoy de la UCI? (prueba " + v["prueba"] + ")", "",
         f"{v['n_prueba']} días-paciente y {v['egresos_prueba']} egresos de UCI que el modelo no vio.", "",
         "| Medida | Modelo | Solo días en UCI y día de la semana |", "|---|---|---|",
         f"| AUC por día-paciente | {v['auc']:.2f} | {v['auc_dias_y_dia_semana']:.2f} |",
         f"| Brier | {v['brier']:.4f} | {v['brier_dias_y_dia_semana']:.4f} |",
         f"| Error en egresos por día (pacientes) | {v['error_egresos_dia']:.2f} | {v['error_egresos_dia_dias_y_dia_semana']:.2f} |",
         "",
         f"Egresos de UCI por día: observados {v['egresos_dia_observados']}, predichos {v['egresos_dia_predichos']}. "
         f"Con el promedio por día de la semana el error diario es {v['error_egresos_dia_promedio_dia_semana']:.2f}.",
         f"De los cinco pacientes con mayor probabilidad cada día sale el {v['salen_entre_los_5_mas_probables']:.0%}, "
         f"frente al {v['salen_en_promedio']:.0%} de un paciente cualquiera.", "",
         "Calibración (deciles):", "", "| Predicho | Observado | Días-paciente |", "|---|---|---|"]
    for c in v["calibracion"]:
        l.append(f"| {c['predicho']:.1%} | {c['observado']:.1%} | {c['n']} |")
    l += ["", "## Ocupación de los próximos 7 días (prueba)", "",
          f"Pronóstico desde {o['origenes']} días de origen, contra el censo reconstruido (media {o['censo_medio']}). "
          "La referencia ingenua supone que la ocupación de hoy se mantiene.", "",
          "| Días adelante | Error del modelo | Error ingenuo | Sesgo | Real dentro del intervalo 80% |", "|---|---|---|---|---|"]
    for h in o["por_horizonte"]:
        l.append(f"| {h['horizonte']} | {h['error_modelo']:.1f} | {h['error_ingenuo']:.1f} | {h['sesgo']:+.1f} | {h['cobertura_80']:.0%} |")
    if "auc_lleno_h1" in o:
        l += ["", f"Con {o['camas']} camas, la probabilidad de llenar la unidad al día siguiente distingue los días llenos "
                  f"({o['dias_llenos_reales_h1']} en la prueba) con AUC {o['auc_lleno_h1']:.2f}."]
    l += ["", "Llegadas a UCI por día (últimas 12 semanas del periodo): " +
          ", ".join(f"{d} {t:.1f}" for d, t in zip(_DIAS, inf["llegadas_por_dia_semana"])) + "."]
    return "\n".join(l) + "\n"


# ---------------------------------------------------------------- calculadora

def exportar_json(modelo: ModeloEgresoDiario) -> dict:
    """El modelo de egreso diario como datos (sin código ni pacientes) para la calculadora web,
    en el mismo formato de árboles que trayectoria.exportar_json."""
    from .trayectoria import _estimador_json

    niveles = {c: modelo.niveles[c] + ["OTRO"] for c in CATEGORICAS}
    conocidos: dict = {}
    est = _estimador_json(modelo.clasificador, NUMERICAS + CATEGORICAS, niveles, conocidos)
    return {"numericas": NUMERICAS, "categoricas": niveles, "conocidos": conocidos, "ajuste": modelo.ajuste,
            "dias_max": DIAS_MAX, "entrenado_hasta": modelo.entrenado_hasta, **est}


def curva_json(js: dict, valores: dict, ingreso: date, lleva: int = 0, dias: int = 14) -> list[dict]:
    """Probabilidad de salir de la UCI cada uno de los próximos `dias` días para un paciente que
    ingresó a la UCI el día `ingreso` y ya lleva `lleva` días (referencia en Python de la
    calculadora). Con 0 días el primer egreso posible es mañana; con L días, hoy (la fila es el
    censo de anoche, día L - 1 de la estancia)."""
    from .trayectoria import _suma_arboles

    base = []
    for c in js["numericas"]:
        v = valores.get(c)
        base.append(None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v))
    cats = []
    for c, niveles in js["categoricas"].items():
        codigo = niveles.index(valores[c]) if valores.get(c) in niveles else niveles.index("OTRO")
        cats.append(codigo if codigo in js["conocidos"].get(c, range(len(niveles))) else None)
    i_dias, i_dow = js["numericas"].index("dias_en_uci"), js["numericas"].index("dia_semana_egreso")
    k0, primero = max(lleva - 1, 0), lleva if lleva > 0 else 1
    sigue, salida = 1.0, []
    for j in range(dias):
        dia = ingreso + timedelta(days=primero + j)
        fila = list(base)
        fila[i_dias], fila[i_dow] = float(min(k0 + j, js["dias_max"])), float(dia.weekday())
        h = 1 / (1 + math.exp(-(_suma_arboles(js, fila + cats) + js["ajuste"])))
        salida.append({"fecha": dia, "sale": sigue * h, "sigue": sigue * (1 - h)})
        sigue *= 1 - h
    return salida


# ---------------------------------------------------------------- uso diario

_CACHE: dict[str, ModeloEgresoDiario | None] = {}


def modelo_configurado() -> ModeloEgresoDiario | None:
    """Modelo entrenado en la ruta de UCI_MODELO_OCUPACION, si existe."""
    import os

    ruta = os.environ.get("UCI_MODELO_OCUPACION")
    if not ruta:
        return None
    if ruta not in _CACHE:
        _CACHE[ruta] = ModeloEgresoDiario.cargar(Path(ruta)) if Path(ruta).exists() else None
    return _CACHE[ruta]


def filas_tablero(pacientes: list, dias_uci: list[float], fecha: date) -> pd.DataFrame:
    """Variables del modelo para los pacientes del tablero de la mañana de `fecha`.

    El tablero de la mañana muestra el censo de la noche anterior, así que la fila de cada
    paciente es su día-paciente de ayer: "sale" quiere decir que egresa hoy, y el día de la
    semana del egreso es el de hoy. `dias_uci` cuenta desde el primer tablero en que apareció
    (0 el primer día), igual que dias_en_uci en el entrenamiento."""
    from .trayectoria import desde_tablero

    filas = [{**desde_tablero(p), "dias_en_uci": float(d), "dia_semana_egreso": float(fecha.weekday())}
             for p, d in zip(pacientes, dias_uci)]
    return pd.DataFrame(filas, columns=NUMERICAS + CATEGORICAS)


def pronosticar_tablero(modelo: ModeloEgresoDiario, filas: pd.DataFrame, fecha: date, llegadas: Llegadas | None = None,
                        camas: int | None = None, horizonte: int = HORIZONTE,
                        egreso_hoy: np.ndarray | None = None) -> list[DiaPronostico]:
    """Ocupación de las próximas `horizonte` mañanas a partir del tablero de la mañana de `fecha`.

    Los pacientes del tablero son el censo de anoche: se pronostica desde ese censo y cada día
    se rotula con la mañana siguiente (la mañana de fecha + 1 es el censo del final de hoy).
    `egreso_hoy` reemplaza la probabilidad de salir hoy de cada paciente."""
    dias = pronosticar(modelo, filas, fecha - timedelta(days=1), llegadas, camas, horizonte, egreso_hoy)
    for d in dias:
        d.fecha = d.fecha + timedelta(days=1)
    return dias
