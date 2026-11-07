"""Predicción al ingreso: destino al salir de la UCI y estancia.

Se entrena con el consolidado de egresos UCI (una fila por ingreso: fechas, días en UCI y en
hospitalización, escalas iniciales y finales, signos vitales, asegurador, diagnósticos y
secuencia de servicios). El archivo no trae el estado ni el destino al egreso (casa, remisión,
fallecido), así que el "destino" que se puede aprender es el camino de salida del hospital.

Desenlaces:
- alta_directa: sale del hospital directamente desde la UCI (último servicio UCI), frente a
  pasar por hospitalización antes del alta. Es la pregunta de camas: ¿este paciente va a
  necesitar cama de piso al salir de la UCI? Si el archivo incluye fallecidos en UCI, quedan
  en este grupo; con una columna de estado al egreso se separan.
- rebote_uci: vuelve a la UCI dentro del mismo ingreso (UCI → piso → UCI).
- uci_prolongada: más de 7 días en UCI (paciente "estancado" en la definición del NHS).
- dias_uci y dias_total: cuantiles 10, 50 y 90 de la estancia.

Solo se usan variables disponibles al ingreso: edad, sexo, asegurador, diagnóstico principal,
SOFA, Barthel y NEWS iniciales, signos vitales, si llega desde piso y los ingresos previos del
mismo paciente. Nada de lo que se sabe al final (escalas finales, días, traslados, número de
diagnósticos acumulados) entra al modelo.

Hay dos juegos de variables: "ingreso" (todas las anteriores) y "tablero" (solo lo que trae el
tablero de ORBIT: edad, sexo, pagador, diagnóstico, SOFA, Barthel y NEWS). El segundo es el
que usa el agente cada mañana.

La validación es temporal: se entrena con los ingresos más antiguos y se mide con los más
recientes, que es como se va a usar. El archivo solo contiene egresos de una ventana de
fechas, por lo que se descartan los ingresos anteriores a la ventana (solo estarían los de
estancia larga) y los de los últimos meses (solo estarían los de estancia corta).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

from ..conectores.orbit_tablero import PacienteTablero, sofa_a_mortalidad
from .riesgo import _SECUNDARIOS, agrupador_diagnostico

UMBRAL_UCI_PROLONGADA = 7
CUANTILES = (0.1, 0.5, 0.9)

NUMERICAS_TABLERO = ["edad", "sofa_mortalidad", "barthel", "news"]
CATEGORICAS_TABLERO = ["sexo", "asegurador", "agrupador", "dx3"]
NUMERICAS_INGRESO = NUMERICAS_TABLERO + ["desde_piso", "ingresos_previos", "peso", "dia_semana"]
# Los signos vitales del consolidado no entran: el 17% de los que terminan en UCI tiene TAS de 60
# o menos (mediana 42/26 mmHg), lo que corresponde al último registro de la estancia y no al
# ingreso. Usarlos sería fuga de información. Se conservan en `preparar` para auditoría.
CATEGORICAS_INGRESO = CATEGORICAS_TABLERO
JUEGOS = {"tablero": (NUMERICAS_TABLERO, CATEGORICAS_TABLERO), "ingreso": (NUMERICAS_INGRESO, CATEGORICAS_INGRESO)}

_CODIGO = re.compile(r"\b([A-Z]\d{2}[0-9X]?)\b")
_RANGOS = {"fc": (20, 250), "tas": (50, 260), "tad": (20, 160), "temperatura": (30, 43), "peso": (25, 250)}
_INTERNACIONAL = ("INSUR", "GOVERNMENT", "REINSUR", "SZV", "NAGICO", "ASSIST", "SEGUROS INTERNACIONAL")


def codigos_cie10(texto: str | None) -> list[str]:
    return _CODIGO.findall(str(texto or "").upper())


def diagnostico_principal(codigos: list[str]) -> str | None:
    """Primer código que no es secundario (desnutrición, HTA, DM y síntomas acompañan)."""
    principales = [c for c in codigos if not c.startswith(_SECUNDARIOS)]
    return (principales or codigos or [None])[0]


def grupo_asegurador(nombre: str | None) -> str:
    n = str(nombre or "").upper()
    if any(k in n for k in _INTERNACIONAL):
        return "INTERNACIONAL"
    for clave, grupo in (("NUEVA EMPRESA", "NUEVA EPS"), ("SURA", "SURA"), ("SANITAS", "SANITAS"),
                         ("ANGIOGRAFIA", "ANGIOGRAFIA"), ("SALUD TOTAL", "SALUD TOTAL"), ("COOSALUD", "COOSALUD"),
                         ("PREPAGADA", "PREPAGADA"), ("COLMEDICA", "PREPAGADA")):
        if clave in n:
            return grupo
    return "OTRO"


def barthel_a_numero(valor) -> float | None:
    """El consolidado trae el puntaje; el tablero trae texto ("DEPENDENCIA SEVERA")."""
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return None
    t = str(valor).upper()
    m = re.search(r"\d+(\.\d+)?", t)
    if m:
        return float(m.group())
    if "INDEPENDIENTE" in t:
        return 100.0
    for clave, puntos in (("LEVE", 90.0), ("MODERAD", 70.0), ("SEVER", 40.0), ("GRAVE", 40.0), ("TOTAL", 10.0)):
        if clave in t:
            return puntos
    return None


def _segmentos_uci(secuencia: str) -> int:
    return sum(s.strip().upper() == "UCI" for s in str(secuencia or "").split("→"))


def preparar(df: pd.DataFrame) -> pd.DataFrame:
    """Variables al ingreso y desenlaces desde el consolidado de egresos. Devuelve una fila por
    ingreso sin nombre ni documento; el documento solo se usa para contar ingresos previos."""
    d = df.copy()
    d["fecha_ingreso_min"] = pd.to_datetime(d["fecha_ingreso_min"])
    d = d.sort_values("fecha_ingreso_min", kind="mergesort").reset_index(drop=True)
    x = pd.DataFrame(index=d.index)
    x["fecha_ingreso"] = d["fecha_ingreso_min"]
    x["edad"] = pd.to_numeric(d["edad"], errors="coerce")
    x["sexo"] = d["sexo_id"].where(d["sexo_id"].isin(["M", "F"]))
    x["asegurador"] = d["tercero"].map(grupo_asegurador)
    cods = d["diagnostico"].map(codigos_cie10)
    x["dx_principal"] = cods.map(diagnostico_principal)
    x["dx3"] = x["dx_principal"].str[:3]
    x["agrupador"] = x["dx_principal"].map(lambda c: agrupador_diagnostico([c]) if isinstance(c, str) else "OTROS")
    x["sofa_mortalidad"] = d["mortalidad_sofa_inicial"].map(sofa_a_mortalidad)
    x["barthel"] = pd.to_numeric(d["barthel_inicial"], errors="coerce")
    x["news"] = pd.to_numeric(d["score_inicial"], errors="coerce")
    for destino, origen in (("fc", "fc"), ("tas", "ta_alta"), ("tad", "ta_baja"), ("temperatura", "temp_piel"),
                            ("peso", "peso")):
        v = pd.to_numeric(d[origen], errors="coerce")
        lo, hi = _RANGOS[destino]
        x[destino] = v.where(v.between(lo, hi))
    x["desde_piso"] = (d["servicio_inicial"].str.upper() != "UCI").astype(float)
    x["ingresos_previos"] = d.groupby("paciente_id").cumcount().astype(float)
    x["dia_semana"] = d["fecha_ingreso_min"].dt.dayofweek.astype(float)
    # Desenlaces
    x["alta_directa"] = (d["servicio_final"].str.upper() == "UCI").astype(int)
    x["rebote_uci"] = (d["secuencia_servicios"].map(_segmentos_uci) >= 2).astype(int)
    x["dias_uci"] = d["dias_uci"].astype(float)
    x["dias_total"] = d["dias_total"].astype(float)
    x["uci_prolongada"] = (x["dias_uci"] > UMBRAL_UCI_PROLONGADA).astype(int)
    for y, (cortes, _) in BANDAS.items():
        x[f"banda_{y}"] = np.digitize(x[y], cortes, right=True)
    x["trayectoria"] = np.where(x["rebote_uci"] == 1, 2, np.where(x["alta_directa"] == 1, 0, 1))
    return x


def desde_tablero(p: PacienteTablero) -> dict:
    """Fila de variables (juego "tablero") para un paciente del tablero de ORBIT."""
    dx = diagnostico_principal(p.cie10)
    return {"edad": p.edad, "sexo": p.sexo if p.sexo in ("M", "F") else None,
            "asegurador": grupo_asegurador(p.pagador), "dx3": dx[:3] if dx else None,
            "agrupador": agrupador_diagnostico([dx]) if dx else "OTROS",
            "sofa_mortalidad": p.sofa_mortalidad, "barthel": barthel_a_numero(p.barthel), "news": p.news}


# ---------------------------------------------------------------- modelos

def _matriz(x: pd.DataFrame, juego: str, niveles: dict[str, list[str]]) -> pd.DataFrame:
    num, cat = JUEGOS[juego]
    m = x.reindex(columns=num + cat).copy()
    for c in num:
        m[c] = pd.to_numeric(m[c], errors="coerce").astype(float)
    for c in cat:
        v = m[c].astype(object).where(m[c].isin(niveles[c]), "OTRO")
        m[c] = pd.Categorical(v, categories=niveles[c] + ["OTRO"])
    return m


def _niveles(x: pd.DataFrame, minimo: int = 25) -> dict[str, list[str]]:
    _, cat = JUEGOS["ingreso"]
    return {c: sorted(k for k, n in x[c].value_counts().items() if n >= minimo and k != "OTRO") for c in cat}


# Árboles pequeños (7 hojas, 60 pacientes por hoja) y 200 rondas: en la validación temporal
# superan a los de 15 hojas y 300 rondas en destino, estancia prolongada y reingreso.
HIPERPARAMETROS = dict(max_iter=200, learning_rate=0.05, max_leaf_nodes=7, min_samples_leaf=60, l2_regularization=1.0,
                       categorical_features="from_dtype", random_state=7)


def _clasificador():
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(**HIPERPARAMETROS)


def _regresor(q: float):
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(loss="quantile", quantile=q, **HIPERPARAMETROS)


BINARIOS = ("alta_directa", "rebote_uci", "uci_prolongada")
# Rangos de días: la fecha puntual tiene un techo con datos de ingreso (decir siempre "3 días"
# acierta ±2 días en 62% de los egresos de UCI), así que se da la probabilidad de cada rango.
BANDAS = {"dias_uci": ([2, 4, 7, 14], ["0-2", "3-4", "5-7", "8-14", "15+"]),
          "dias_total": ([3, 7, 14, 30], ["0-3", "4-7", "8-14", "15-30", "31+"])}
# Camino completo, excluyentes: sale del hospital desde UCI, pasa a piso y sale, o vuelve a UCI.
TRAYECTORIAS = ("sale_desde_uci", "piso_y_alta", "vuelve_a_uci")
CONTINUOS = ("dias_uci", "dias_total")


@dataclass
class ModeloTrayectoria:
    juego: str
    niveles: dict[str, list[str]]
    clasificadores: dict[str, object]
    regresores: dict[str, dict[float, object]]
    multiclase: object | None = None
    bandas: dict[str, object] = field(default_factory=dict)
    entrenado_hasta: str = ""
    n_entrenamiento: int = 0
    metricas: dict = field(default_factory=dict)

    def predecir(self, x: pd.DataFrame | dict | list[dict]) -> pd.DataFrame:
        if isinstance(x, dict):
            x = [x]
        x = pd.DataFrame(x)
        m = _matriz(x, self.juego, self.niveles)
        out = pd.DataFrame(index=x.index)
        for y, clf in self.clasificadores.items():
            out[f"p_{y}"] = clf.predict_proba(m)[:, 1]
        if self.multiclase is not None:
            pm = self.multiclase.predict_proba(m)
            for i, nombre in enumerate(TRAYECTORIAS):
                out[f"p_{nombre}"] = pm[:, i]
        for y, clf in (getattr(self, "bandas", None) or {}).items():
            pb = np.zeros((len(m), len(BANDAS[y][1])))
            pb[:, clf.classes_] = clf.predict_proba(m)
            for i, etiqueta in enumerate(BANDAS[y][1]):
                out[f"p_{y}_{etiqueta}"] = pb[:, i]
        for y, regs in self.regresores.items():
            # Se modela log(1 + días) y se ordenan los cuantiles para que no se crucen.
            qs = np.sort(np.column_stack([regs[q].predict(m) for q in CUANTILES]), axis=1)
            for i, q in enumerate(CUANTILES):
                out[f"{y}_p{int(q * 100)}"] = np.expm1(qs[:, i]).clip(0)
        return out

    def guardar(self, ruta: Path) -> None:
        import joblib
        joblib.dump(self, ruta)

    @staticmethod
    def cargar(ruta: Path) -> "ModeloTrayectoria":
        """Solo cargar archivos generados por este mismo agente: un .joblib puede ejecutar código."""
        import joblib
        return joblib.load(ruta)


def ajustar(x: pd.DataFrame, juego: str = "tablero") -> ModeloTrayectoria:
    niveles = _niveles(x)
    m = _matriz(x, juego, niveles)
    clfs = {y: _clasificador().fit(m, x[y]) for y in BINARIOS}
    regs = {y: {q: _regresor(q).fit(m, np.log1p(x[y])) for q in CUANTILES} for y in CONTINUOS}
    multi = _clasificador().fit(m, x["trayectoria"])
    bandas = {y: _clasificador().fit(m, x[f"banda_{y}"]) for y in BANDAS}
    return ModeloTrayectoria(juego, niveles, clfs, regs, multi, bandas, str(x["fecha_ingreso"].max().date()), len(x))


def ventana_completa(x: pd.DataFrame, inicio: str, fin: str) -> pd.DataFrame:
    """Ingresos con desenlace completo dentro de la ventana de egresos del archivo."""
    return x[(x["fecha_ingreso"] >= inicio) & (x["fecha_ingreso"] <= fin)]


# ---------------------------------------------------------------- validación

def _auc(y, p) -> float:
    from sklearn.metrics import roc_auc_score
    return round(float(roc_auc_score(y, p)), 3)


def _calibracion(y: np.ndarray, p: np.ndarray, grupos: int = 5) -> list[dict]:
    orden = pd.qcut(p, grupos, labels=False, duplicates="drop")
    return [{"predicho": round(float(p[orden == g].mean()), 3), "observado": round(float(y[orden == g].mean()), 3),
             "n": int((orden == g).sum())} for g in sorted(set(orden))]


def _bandas(v: np.ndarray) -> np.ndarray:
    return np.digitize(v, [3.5, 7.5, 14.5])  # 0-3, 4-7, 8-14, 15+ días


def validar(x: pd.DataFrame, corte: str, fin_prueba: str, juego: str) -> dict:
    """Entrena con ingresos antes de `corte` y mide en los ingresos de `corte` a `fin_prueba`."""
    from sklearn.metrics import brier_score_loss

    ent = x[x["fecha_ingreso"] < corte]
    pru = x[(x["fecha_ingreso"] >= corte) & (x["fecha_ingreso"] <= fin_prueba)]
    modelo = ajustar(ent, juego)
    pred = modelo.predecir(pru)
    r = {"juego": juego, "n_entrenamiento": len(ent), "n_prueba": len(pru),
         "entrenamiento": f"{ent['fecha_ingreso'].min().date()} a {ent['fecha_ingreso'].max().date()}",
         "prueba": f"{pru['fecha_ingreso'].min().date()} a {pru['fecha_ingreso'].max().date()}", "binarios": {},
         "continuos": {}}
    for y in BINARIOS:
        yt, p = pru[y].to_numpy(), pred[f"p_{y}"].to_numpy()
        ref = pru["sofa_mortalidad"].fillna(pru["sofa_mortalidad"].median()).to_numpy()
        r["binarios"][y] = {"prevalencia_entrenamiento": round(float(ent[y].mean()), 3),
                            "prevalencia_prueba": round(float(yt.mean()), 3), "auc": _auc(yt, p),
                            "auc_solo_sofa": _auc(yt, ref), "brier": round(float(brier_score_loss(yt, p)), 4),
                            "brier_sin_modelo": round(float(brier_score_loss(yt, np.full_like(p, ent[y].mean()))), 4),
                            "probabilidad_media": round(float(p.mean()), 3), "calibracion": _calibracion(yt, p)}
    for y in CONTINUOS:
        yt = pru[y].to_numpy()
        p10, p50, p90 = (pred[f"{y}_p{q}"].to_numpy() for q in (10, 50, 90))
        base = float(ent[y].median())
        r["continuos"][y] = {"mediana_observada": float(np.median(yt)), "mae_mediana": round(float(np.mean(np.abs(yt - p50))), 2),
                             "mae_sin_modelo": round(float(np.mean(np.abs(yt - base))), 2),
                             "cobertura_p10_p90": round(float(np.mean((yt >= np.floor(p10)) & (yt <= np.ceil(p90)))), 3),
                             "acierto_banda": round(float(np.mean(_bandas(yt) == _bandas(np.round(p50)))), 3),
                             "acierto_banda_sin_modelo": round(float(np.mean(_bandas(yt) == _bandas(np.full_like(yt, round(base))))), 3),
                             "spearman": round(float(pd.Series(yt).corr(pd.Series(p50), method="spearman")), 3),
                             "dias_cama_observados": int(yt.sum()), "dias_cama_predichos_mediana": int(p50.sum())}
    from sklearn.metrics import log_loss

    yt = pru["trayectoria"].to_numpy()
    pm = pred[[f"p_{n}" for n in TRAYECTORIAS]].to_numpy()
    previa = np.bincount(ent["trayectoria"], minlength=3) / len(ent)
    r["trayectoria"] = {"clases": {n: {"prevalencia_prueba": round(float((yt == i).mean()), 3), "auc": _auc(yt == i, pm[:, i]),
                                       "probabilidad_media": round(float(pm[:, i].mean()), 3)} for i, n in enumerate(TRAYECTORIAS)},
                        "log_loss": round(float(log_loss(yt, pm, labels=[0, 1, 2])), 3),
                        "log_loss_sin_modelo": round(float(log_loss(yt, np.tile(previa, (len(yt), 1)), labels=[0, 1, 2])), 3),
                        "acierto_clase_mas_probable": round(float((pm.argmax(1) == yt).mean()), 3),
                        "acierto_sin_modelo": round(float((yt == previa.argmax()).mean()), 3)}
    r["bandas"] = {}
    for y, (_, etiquetas) in BANDAS.items():
        pb = pred[[f"p_{y}_{e}" for e in etiquetas]].to_numpy()
        yb = pru[f"banda_{y}"].to_numpy()
        base = np.bincount(ent[f"banda_{y}"], minlength=len(etiquetas)) / len(ent)
        rps, rps0 = _rps(pb, yb), _rps(np.tile(base, (len(yb), 1)), yb)
        r["bandas"][y] = {"rps": round(rps, 4), "rps_sin_modelo": round(rps0, 4), "mejora": round(1 - rps / rps0, 3),
                          "observado": [round(float((yb == k).mean()), 3) for k in range(len(etiquetas))],
                          "predicho": [round(float(v), 3) for v in pb.mean(0)],
                          "calibracion": {e: _calibracion((yb == k).astype(int), pb[:, k]) for k, e in enumerate(etiquetas)},
                          "acierto_puntual_2d": round(float(np.mean(np.abs(pru[y] - np.round(pred[f"{y}_p50"])) <= 2)), 3),
                          "acierto_puntual_2d_constante": round(float(max(np.mean(np.abs(pru[y] - d) <= 2) for d in range(15))), 3)}
    r["ic95"] = _bootstrap(pru, pred)
    r["importancia"] = _importancia(modelo, pru)
    return r


def _rps(p: np.ndarray, y: np.ndarray) -> float:
    """Ranked probability score: error de una predicción por rangos ordenados (0 es perfecto)."""
    c = np.cumsum(p, 1)
    o = np.cumsum(np.eye(p.shape[1])[y], 1)
    return float(np.mean(np.sum((c - o) ** 2, 1) / (p.shape[1] - 1)))


def _bootstrap(pru: pd.DataFrame, pred: pd.DataFrame, veces: int = 300, semilla: int = 7) -> dict[str, list[float]]:
    """Intervalo del 95% del AUC por remuestreo de los pacientes de prueba."""
    from sklearn.metrics import roc_auc_score

    r = np.random.default_rng(semilla)
    n = len(pru)
    out = {}
    for y in BINARIOS:
        yt, p = pru[y].to_numpy(), pred[f"p_{y}"].to_numpy()
        aucs = []
        for _ in range(veces):
            i = r.integers(0, n, n)
            if yt[i].min() != yt[i].max():
                aucs.append(roc_auc_score(yt[i], p[i]))
        out[y] = [round(float(np.percentile(aucs, 2.5)), 3), round(float(np.percentile(aucs, 97.5)), 3)]
    return out


def validacion_movil(x: pd.DataFrame, juego: str, cortes: list[str], meses: int = 3) -> list[dict]:
    """Origen móvil: para cada corte entrena con lo anterior y prueba en los `meses` siguientes.
    Muestra si el desempeño se sostiene en el tiempo y no depende de un solo corte."""
    filas = []
    for corte in cortes:
        fin = str((pd.Timestamp(corte) + pd.DateOffset(months=meses) - pd.Timedelta(days=1)).date())
        ent = x[x["fecha_ingreso"] < corte]
        pru = x[(x["fecha_ingreso"] >= corte) & (x["fecha_ingreso"] <= fin)]
        if len(pru) < 60 or len(ent) < 300:
            continue
        modelo = ajustar(ent, juego)
        pred = modelo.predecir(pru)
        fila = {"corte": corte, "n_entrenamiento": len(ent), "n_prueba": len(pru)}
        for y in BINARIOS:
            fila[f"auc_{y}"] = _auc(pru[y], pred[f"p_{y}"])
        for y in CONTINUOS:
            fila[f"mae_{y}"] = round(float(np.mean(np.abs(pru[y] - pred[f"{y}_p50"]))), 2)
            fila[f"mae_{y}_sin_modelo"] = round(float(np.mean(np.abs(pru[y] - ent[y].median()))), 2)
        filas.append(fila)
    return filas


def _importancia(modelo: ModeloTrayectoria, pru: pd.DataFrame) -> dict[str, list[tuple[str, float]]]:
    """Importancia por permutación (caída del AUC) para destino y estancia prolongada."""
    from sklearn.inspection import permutation_importance

    m = _matriz(pru, modelo.juego, modelo.niveles)
    out = {}
    for y in ("alta_directa", "uci_prolongada"):
        imp = permutation_importance(modelo.clasificadores[y], m, pru[y], scoring="roc_auc", n_repeats=5, random_state=7)
        orden = np.argsort(-imp.importances_mean)
        out[y] = [(m.columns[i], round(float(imp.importances_mean[i]), 3)) for i in orden[:8]]
    return out


def perfiles(x: pd.DataFrame) -> dict:
    """Descriptivo por agrupador diagnóstico y SOFA, para el informe."""
    g = x.groupby("agrupador").agg(n=("dias_uci", "size"), uci_mediana=("dias_uci", "median"),
                                   total_mediana=("dias_total", "median"), alta_directa=("alta_directa", "mean"),
                                   uci_prolongada=("uci_prolongada", "mean")).sort_values("n", ascending=False)
    s = x.assign(sofa=pd.cut(x["sofa_mortalidad"], [-.01, .01, .1, .3, 1], labels=["0%", "6-8%", "22-25%", "32%+"]))
    s = s.groupby("sofa", observed=False).agg(n=("dias_uci", "size"), uci_mediana=("dias_uci", "median"),
                                              total_mediana=("dias_total", "median"), alta_directa=("alta_directa", "mean"))
    return {"agrupador": g.round(3).reset_index().to_dict("records"), "sofa": s.round(3).reset_index().astype({"sofa": str}).to_dict("records")}


# ---------------------------------------------------------------- exportación a JSON

def _traductor(est, columnas: list[str], niveles: dict[str, list[str]]):
    """scikit-learn reordena las variables (categóricas primero) y recodifica cada categoría
    según las vistas en el entrenamiento. Se devuelve cómo volver al orden y a los códigos de
    `exportar_json`, y qué códigos conoce el modelo (los demás se tratan como faltantes)."""
    pre = est._preprocessor
    if pre is None:
        return list(range(len(columnas))), {}, {}
    _, enc, es_cat = pre.transformers_[0]
    cat_idx = [i for i, c in enumerate(es_cat) if c]
    num_idx = [i for i, c in enumerate(es_cat) if not c]
    orden = cat_idx + num_idx  # índice transformado -> índice original
    codigos, conocidos = {}, {}
    for k, i in enumerate(cat_idx):
        nombre = columnas[i]
        mapa = [niveles[nombre].index(str(v)) for v in enc.categories_[k]]
        codigos[k] = mapa
        conocidos[nombre] = sorted(mapa)
    return orden, codigos, conocidos


def _arbol_json(pred, orden: list[int], codigos: dict[int, list[int]]) -> list:
    """Nodos compactos: hoja [valor]; numérico [variable, umbral, izq, der, faltante_izq];
    categórico [variable, [códigos a la izquierda], izq, der, faltante_izq]."""
    bits = pred.raw_left_cat_bitsets
    nodos = []
    for nd in pred.nodes:
        if nd["is_leaf"]:
            nodos.append([round(float(nd["value"]), 6)])
            continue
        f = int(nd["feature_idx"])
        if nd["is_categorical"]:
            b = bits[nd["bitset_idx"]]
            crudos = [c for c in range(32 * len(b)) if (int(b[c // 32]) >> (c % 32)) & 1]
            izq = sorted(codigos[f][c] for c in crudos if c < len(codigos[f]))
            nodos.append([orden[f], izq, int(nd["left"]), int(nd["right"]), int(nd["missing_go_to_left"])])
        else:
            nodos.append([orden[f], float(nd["num_threshold"]), int(nd["left"]), int(nd["right"]),
                          int(nd["missing_go_to_left"])])
    return nodos


def _estimador_json(est, columnas: list[str], niveles: dict[str, list[str]], conocidos: dict) -> dict:
    orden, codigos, con = _traductor(est, columnas, niveles)
    conocidos.update(con)
    return {"base": [float(v) for v in np.ravel(est._baseline_prediction)],
            "arboles": [[_arbol_json(p, orden, codigos) for p in iteracion] for iteracion in est._predictors]}


def exportar_json(modelo: ModeloTrayectoria) -> dict:
    """El modelo como datos (sin código ni pacientes) para evaluarlo en una página web.
    Las variables van en el orden numéricas + categóricas; cada categórica se codifica por su
    posición en `categoricas` y los códigos fuera de `conocidos` cuentan como faltantes."""
    num, cat = JUEGOS[modelo.juego]
    cols = num + cat
    niveles = {c: modelo.niveles[c] + ["OTRO"] for c in cat}
    con: dict = {}
    js = {"juego": modelo.juego, "entrenado_hasta": modelo.entrenado_hasta, "n_entrenamiento": modelo.n_entrenamiento,
          "numericas": num, "categoricas": niveles, "cuantiles": list(CUANTILES),
          "binarios": {y: _estimador_json(e, cols, niveles, con) for y, e in modelo.clasificadores.items()},
          "trayectoria": {"clases": list(TRAYECTORIAS), **_estimador_json(modelo.multiclase, cols, niveles, con)},
          "continuos": {y: [_estimador_json(regs[q], cols, niveles, con) for q in CUANTILES]
                        for y, regs in modelo.regresores.items()},
          "bandas": {y: {"etiquetas": BANDAS[y][1], "clases": [int(k) for k in e.classes_], **_estimador_json(e, cols, niveles, con)}
                     for y, e in (getattr(modelo, "bandas", None) or {}).items()}}
    js["conocidos"] = con
    return js


def _suma_arboles(est: dict, fila: list, k: int = 0) -> float:
    total = est["base"][k]
    for iteracion in est["arboles"]:
        nodos, i = iteracion[k], 0
        while len(nodos[i]) > 1:
            f, regla, izq, der, falt = nodos[i]
            v = fila[f]
            if v is None:
                i = izq if falt else der
            elif isinstance(regla, list):
                i = izq if v in regla else der
            else:
                i = izq if v <= regla else der
        total += nodos[i][0]
    return total


def predecir_json(js: dict, valores: dict) -> dict:
    """Evaluador de referencia del JSON exportado (la página web hace lo mismo en JavaScript)."""
    fila = []
    for c in js["numericas"]:
        v = valores.get(c)
        fila.append(None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v))
    for c, niveles in js["categoricas"].items():
        v = valores.get(c)
        codigo = niveles.index(v) if v in niveles else niveles.index("OTRO")  # como _matriz
        fila.append(codigo if codigo in js["conocidos"].get(c, range(len(niveles))) else None)
    out = {f"p_{y}": 1 / (1 + math.exp(-_suma_arboles(e, fila))) for y, e in js["binarios"].items()}
    t = js["trayectoria"]
    z = [_suma_arboles(t, fila, k) for k in range(len(t["clases"]))]
    ez = [math.exp(v - max(z)) for v in z]
    out.update({f"p_{c}": e / sum(ez) for c, e in zip(t["clases"], ez)})
    for y, b in js.get("bandas", {}).items():
        if len(b["clases"]) == 2:  # scikit-learn usa un solo árbol por ronda con dos clases
            z = [0.0, _suma_arboles(b, fila)]
        else:
            z = [_suma_arboles(b, fila, k) for k in range(len(b["clases"]))]
        ez = [math.exp(v - max(z)) for v in z]
        prob = dict(zip(b["clases"], (e / sum(ez) for e in ez)))
        out.update({f"p_{y}_{e}": prob.get(k, 0.0) for k, e in enumerate(b["etiquetas"])})
    for y, ests in js["continuos"].items():
        qs = sorted(_suma_arboles(e, fila) for e in ests)
        for q, v in zip(js["cuantiles"], qs):
            out[f"{y}_p{int(q * 100)}"] = max(math.expm1(v), 0.0)
    return out


# ---------------------------------------------------------------- uso diario

def restante_condicional(p10: float, p50: float, p90: float, transcurridos: float) -> float:
    """Días que faltan (mediana) dado que el paciente ya lleva `transcurridos` días.

    Supone estancia log-normal con mediana p50 y dispersión tomada de p10 y p90; la mediana
    condicional m cumple S(m) = S(t) / 2. Así un paciente que ya superó lo esperado no queda
    con cero días restantes.
    """
    z = NormalDist()
    mu = math.log1p(max(p50, 0))
    sigma = max((math.log1p(max(p90, 0)) - math.log1p(max(p10, 0))) / (2 * 1.2816), 0.15)
    s_t = 1 - z.cdf((math.log1p(max(transcurridos, 0)) - mu) / sigma)
    s_t = min(max(s_t, 1e-6), 1.0)
    m = math.expm1(mu + sigma * z.inv_cdf(1 - s_t / 2))
    return round(max(m - transcurridos, 0.5), 1)


_MODELO_CACHE: dict[str, ModeloTrayectoria | None] = {}


def modelo_configurado() -> ModeloTrayectoria | None:
    """Modelo entrenado en la ruta de UCI_MODELO_TRAYECTORIA, si existe."""
    import os
    ruta = os.environ.get("UCI_MODELO_TRAYECTORIA")
    if not ruta:
        return None
    if ruta not in _MODELO_CACHE:
        _MODELO_CACHE[ruta] = ModeloTrayectoria.cargar(Path(ruta)) if Path(ruta).exists() else None
    return _MODELO_CACHE[ruta]


@dataclass
class PrediccionTrayectoria:
    p_alta_directa: float  # sale a la casa desde la UCI, sin pasar por piso
    p_rebote_uci: float
    p_uci_prolongada: float
    dias_uci: tuple[float, float, float]  # p10, p50, p90 desde el ingreso a UCI
    dias_total: tuple[float, float, float]
    dias_uci_restantes: float | None = None
    trayectoria: dict[str, float] = field(default_factory=dict)  # sale_desde_uci, piso_y_alta, vuelve_a_uci
    rangos_uci: dict[str, float] = field(default_factory=dict)  # probabilidad de cada rango de días en UCI

    @property
    def destino(self) -> str:
        if self.trayectoria:
            return max(self.trayectoria, key=self.trayectoria.get).replace("_", " ")
        return "sale desde UCI" if self.p_alta_directa >= 0.5 else "piso y alta"


def predecir_paciente(p: PacienteTablero, modelo: ModeloTrayectoria | None = None,
                      dias_uci: float | None = None) -> PrediccionTrayectoria | None:
    modelo = modelo or modelo_configurado()
    if modelo is None:
        return None
    f = modelo.predecir(desde_tablero(p)).iloc[0]
    uci = tuple(round(float(f[f"dias_uci_p{q}"]), 1) for q in (10, 50, 90))
    tot = tuple(round(float(f[f"dias_total_p{q}"]), 1) for q in (10, 50, 90))
    rest = restante_condicional(*uci, dias_uci) if dias_uci is not None else None
    tray = {n: round(float(f[f"p_{n}"]), 3) for n in TRAYECTORIAS if f"p_{n}" in f}
    return PrediccionTrayectoria(round(float(f["p_alta_directa"]), 3), round(float(f["p_rebote_uci"]), 3),
                                 round(float(f["p_uci_prolongada"]), 3), uci, tot, rest, tray,
                                 {e: round(float(f[f"p_dias_uci_{e}"]), 3) for e in BANDAS["dias_uci"][1] if f"p_dias_uci_{e}" in f})


# ---------------------------------------------------------------- entrenamiento completo

def etiquetas_diagnostico(crudo: pd.DataFrame, codigos: list[str]) -> dict[str, str]:
    """Nombre CIE-10 más frecuente de cada categoría de 3 caracteres (texto público, sin pacientes)."""
    from collections import Counter, defaultdict

    nombres: dict[str, Counter] = defaultdict(Counter)
    for texto in crudo["diagnostico"].fillna(""):
        for parte in str(texto).split(" | "):
            if " - " in parte:
                cod, nombre = parte.split(" - ", 1)
                nombres[cod.strip()[:3]][nombre.strip()] += 1
    return {c: nombres[c].most_common(1)[0][0].capitalize() for c in codigos if c != "OTRO" and nombres[c]}


def calculadora_html(modelo: ModeloTrayectoria, informe: dict, etiquetas: dict[str, str], egreso_diario=None) -> str:
    """Página autónoma (sin servidor) que evalúa el modelo en el navegador: el JSON exportado,
    los nombres de los diagnósticos y las métricas de validación. No contiene datos de pacientes.
    Con `egreso_diario` (ModeloEgresoDiario) suma la probabilidad de salir de la UCI día a día."""
    import json

    from .ocupacion import exportar_json as exportar_egreso

    plantilla = (Path(__file__).resolve().parent.parent / "plantillas" / "calculadora_trayectoria.html").read_text(encoding="utf-8")
    v = informe["validacion"][modelo.juego]
    meta = {"validacion": {"binarios": {y: {k: b[k] for k in ("auc", "auc_solo_sofa")} for y, b in v["binarios"].items()},
                           "continuos": v["continuos"], "trayectoria": v["trayectoria"]},
            "prueba": v["prueba"], "movil": informe.get("validacion_movil", [])}
    egreso = json.dumps(exportar_egreso(egreso_diario), separators=(",", ":")) if egreso_diario is not None else "null"
    return (plantilla.replace("__MODELO__", json.dumps(exportar_json(modelo), separators=(",", ":")))
            .replace("__EGRESO__", egreso)
            .replace("__DX__", json.dumps(etiquetas, ensure_ascii=False))
            .replace("__META__", json.dumps(meta, ensure_ascii=False)))


def leer_egresos(ruta: Path) -> pd.DataFrame:
    if ruta.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(ruta, sheet_name=0)
    return pd.read_csv(ruta)


def entrenar_desde_archivo(ruta: Path, corte: str | None = None, fin: str | None = None,
                           inicio: str | None = None) -> tuple[dict[str, ModeloTrayectoria], dict]:
    """Valida temporalmente los dos juegos de variables y ajusta los modelos finales con todo
    el periodo completo. Por defecto: ventana desde el primer día del mes del egreso más
    antiguo, fin tres meses antes del último egreso, prueba = último 30% del periodo."""
    crudo = leer_egresos(ruta)
    x = preparar(crudo)
    salida = pd.to_datetime(crudo["fecha_salida_max"])
    inicio = inicio or str(salida.min().to_period("M").start_time.date())
    fin = fin or str((salida.max() - pd.DateOffset(months=3)).date())
    x = ventana_completa(x, inicio, fin)
    if corte is None:
        corte = str(x["fecha_ingreso"].quantile(0.7).to_period("M").start_time.date())
    informe = {"archivo_filas": len(crudo), "ingresos_modelados": len(x), "ventana": [inicio, fin], "corte": corte,
               "alta_viva": ("el archivo no trae el estado ni el destino al egreso (casa, remisión, fallecido). "
                             "\"Alta directa\" es salir del hospital con la UCI como último servicio; si el archivo "
                             "incluye fallecidos en UCI, quedan en ese grupo"),
               "validacion": {j: validar(x, corte, fin, j) for j in JUEGOS}, "perfiles": perfiles(x)}
    primero = x["fecha_ingreso"].quantile(0.4).to_period("M").start_time
    ultimo = pd.Timestamp(fin) - pd.DateOffset(months=3)
    cortes = [str(c.date()) for c in pd.date_range(primero, ultimo, freq="2MS")]
    informe["validacion_movil"] = validacion_movil(x, "tablero", cortes)
    modelos = {j: ajustar(x, j) for j in JUEGOS}
    for j, m in modelos.items():
        m.metricas = informe["validacion"][j]
    return modelos, informe


def a_markdown(inf: dict) -> str:
    """Informe sin identificadores: solo conteos y métricas agregadas."""
    v = inf["validacion"]
    nombres = {"alta_directa": "Alta del hospital directamente desde UCI", "rebote_uci": "Reingreso a UCI en el mismo ingreso",
               "uci_prolongada": f"UCI más de {UMBRAL_UCI_PROLONGADA} días", "dias_uci": "Días en UCI",
               "dias_total": "Días totales de hospitalización"}
    l = ["# Predicción al ingreso: destino y estancia", "",
         f"Ingresos modelados: {inf['ingresos_modelados']} de {inf['archivo_filas']} (ventana {inf['ventana'][0]} a "
         f"{inf['ventana'][1]}). Validación temporal: entrenamiento antes de {inf['corte']}, prueba desde esa fecha.", "",
         f"Nota: {inf['alta_viva']}.", "", "## Desenlaces binarios (prueba)", "",
         "| Desenlace | Juego | Prevalencia entren. | Prevalencia prueba | AUC (IC 95%) | AUC solo SOFA | Brier | Brier sin modelo |",
         "|---|---|---|---|---|---|---|---|"]
    for y in BINARIOS:
        for j in JUEGOS:
            b = v[j]["binarios"][y]
            l.append(f"| {nombres[y]} | {j} | {b['prevalencia_entrenamiento']:.1%} | {b['prevalencia_prueba']:.1%} | "
                     f"{b['auc']:.2f} ({v[j]['ic95'][y][0]:.2f}-{v[j]['ic95'][y][1]:.2f}) | {b['auc_solo_sofa']:.2f} | {b['brier']:.3f} | {b['brier_sin_modelo']:.3f} |")
    l += ["", "## Camino completo (prueba)", "",
          "| Juego | Sale desde UCI | Piso y alta | Vuelve a UCI | Acierto clase más probable | Sin modelo | Log-loss | Sin modelo |",
          "|---|---|---|---|---|---|---|---|"]
    for j in JUEGOS:
        tr = v[j]["trayectoria"]
        aucs = " | ".join(f"AUC {tr['clases'][n]['auc']:.2f} ({tr['clases'][n]['prevalencia_prueba']:.0%})" for n in TRAYECTORIAS)
        l.append(f"| {j} | {aucs} | {tr['acierto_clase_mas_probable']:.0%} | {tr['acierto_sin_modelo']:.0%} | "
                 f"{tr['log_loss']:.3f} | {tr['log_loss_sin_modelo']:.3f} |")
    l += ["", "## Estancia (prueba)", "",
          "| Desenlace | Juego | Mediana observada | Error absoluto medio | Sin modelo | Cobertura p10-p90 | Acierto de banda | Banda sin modelo | Spearman |",
          "|---|---|---|---|---|---|---|---|---|"]
    for y in CONTINUOS:
        for j in JUEGOS:
            c = v[j]["continuos"][y]
            l.append(f"| {nombres[y]} | {j} | {c['mediana_observada']:.0f} | {c['mae_mediana']:.1f} | {c['mae_sin_modelo']:.1f} | "
                     f"{c['cobertura_p10_p90']:.0%} | {c['acierto_banda']:.0%} | {c['acierto_banda_sin_modelo']:.0%} | {c['spearman']:.2f} |")
    l += ["", "Bandas: 0-3, 4-7, 8-14 y 15 o más días.", "",
          "## Probabilidad por rango de días (prueba, juego tablero)", "",
          "Una sola fecha tiene un techo con estos datos: decir a todos el mismo número de días acierta con ±2 días "
          "casi tanto como el modelo. Por eso el modelo da la probabilidad de cada rango. La mejora es la reducción "
          "del ranked probability score frente a usar la distribución histórica para todos.", "",
          "| Estancia | Acierto ±2 días del modelo | De la mejor constante | Mejora por rangos | Rangos | Observado | Predicho |",
          "|---|---|---|---|---|---|---|"]
    for y, b in v["tablero"].get("bandas", {}).items():
        l.append(f"| {nombres[y]} | {b['acierto_puntual_2d']:.0%} | {b['acierto_puntual_2d_constante']:.0%} | {b['mejora']:.0%} | "
                 f"{', '.join(BANDAS[y][1])} | {', '.join(f'{o:.0%}' for o in b['observado'])} | "
                 f"{', '.join(f'{q:.0%}' for q in b['predicho'])} |")
    l += [""]
    if inf.get("validacion_movil"):
        l += ["## Estabilidad en el tiempo (origen móvil, juego tablero, 3 meses de prueba por corte)", "",
              "| Corte | Prueba | AUC destino | AUC UCI > 7 días | AUC reingreso UCI | Error días UCI | Sin modelo |", "|---|---|---|---|---|---|---|"]
        for f in inf["validacion_movil"]:
            l.append(f"| {f['corte']} | {f['n_prueba']} | {f['auc_alta_directa']:.2f} | {f['auc_uci_prolongada']:.2f} | "
                     f"{f['auc_rebote_uci']:.2f} | {f['mae_dias_uci']:.1f} | {f['mae_dias_uci_sin_modelo']:.1f} |")
        l.append("")
    l += [ "## Calibración del destino (juego ingreso, quintiles)", "",
          "| Predicho | Observado | n |", "|---|---|---|"]
    for c in v["ingreso"]["binarios"]["alta_directa"]["calibracion"]:
        l.append(f"| {c['predicho']:.0%} | {c['observado']:.0%} | {c['n']} |")
    l += ["", "## Variables que más pesan (juego ingreso, caída del AUC al permutar)", ""]
    for y, imp in v["ingreso"]["importancia"].items():
        l.append(f"- {nombres[y]}: " + ", ".join(f"{k} ({i:+.3f})" for k, i in imp))
    l += ["", "## Perfil por agrupador diagnóstico", "",
          "| Agrupador | Ingresos | UCI mediana | Total mediana | Alta directa | UCI > 7 días |", "|---|---|---|---|---|---|"]
    for r in inf["perfiles"]["agrupador"]:
        l.append(f"| {r['agrupador']} | {r['n']} | {r['uci_mediana']:.0f} | {r['total_mediana']:.0f} | "
                 f"{r['alta_directa']:.0%} | {r['uci_prolongada']:.0%} |")
    l += ["", "## Perfil por SOFA inicial (mortalidad esperada)", "", "| SOFA | Ingresos | UCI mediana | Total mediana | Alta directa |",
          "|---|---|---|---|---|"]
    for r in inf["perfiles"]["sofa"]:
        l.append(f"| {r['sofa']} | {r['n']} | {r['uci_mediana']:.0f} | {r['total_mediana']:.0f} | {r['alta_directa']:.0%} |")
    return "\n".join(l) + "\n"
