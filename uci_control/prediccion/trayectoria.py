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
    d = d.sort_values("fecha_ingreso_min").reset_index(drop=True)
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


def _clasificador():
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_iter=300, learning_rate=0.04, max_leaf_nodes=15, min_samples_leaf=40,
                                          l2_regularization=1.0, categorical_features="from_dtype", random_state=7)


def _regresor(q: float):
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(loss="quantile", quantile=q, max_iter=300, learning_rate=0.04,
                                         max_leaf_nodes=15, min_samples_leaf=40, categorical_features="from_dtype",
                                         random_state=7)


BINARIOS = ("alta_directa", "rebote_uci", "uci_prolongada")
CONTINUOS = ("dias_uci", "dias_total")


@dataclass
class ModeloTrayectoria:
    juego: str
    niveles: dict[str, list[str]]
    clasificadores: dict[str, object]
    regresores: dict[str, dict[float, object]]
    entrenado_hasta: str
    n_entrenamiento: int
    metricas: dict = field(default_factory=dict)

    def predecir(self, x: pd.DataFrame | dict | list[dict]) -> pd.DataFrame:
        if isinstance(x, dict):
            x = [x]
        x = pd.DataFrame(x)
        m = _matriz(x, self.juego, self.niveles)
        out = pd.DataFrame(index=x.index)
        for y, clf in self.clasificadores.items():
            out[f"p_{y}"] = clf.predict_proba(m)[:, 1]
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
    return ModeloTrayectoria(juego, niveles, clfs, regs, str(x["fecha_ingreso"].max().date()), len(x))


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
    r["importancia"] = _importancia(modelo, pru)
    return r


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

    @property
    def destino(self) -> str:
        return "casa desde UCI" if self.p_alta_directa >= 0.5 else "hospitalización antes del alta"


def predecir_paciente(p: PacienteTablero, modelo: ModeloTrayectoria | None = None,
                      dias_uci: float | None = None) -> PrediccionTrayectoria | None:
    modelo = modelo or modelo_configurado()
    if modelo is None:
        return None
    f = modelo.predecir(desde_tablero(p)).iloc[0]
    uci = tuple(round(float(f[f"dias_uci_p{q}"]), 1) for q in (10, 50, 90))
    tot = tuple(round(float(f[f"dias_total_p{q}"]), 1) for q in (10, 50, 90))
    rest = restante_condicional(*uci, dias_uci) if dias_uci is not None else None
    return PrediccionTrayectoria(round(float(f["p_alta_directa"]), 3), round(float(f["p_rebote_uci"]), 3),
                                 round(float(f["p_uci_prolongada"]), 3), uci, tot, rest)


# ---------------------------------------------------------------- entrenamiento completo

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
         "| Desenlace | Juego | Prevalencia entren. | Prevalencia prueba | AUC | AUC solo SOFA | Brier | Brier sin modelo |",
         "|---|---|---|---|---|---|---|---|"]
    for y in BINARIOS:
        for j in JUEGOS:
            b = v[j]["binarios"][y]
            l.append(f"| {nombres[y]} | {j} | {b['prevalencia_entrenamiento']:.1%} | {b['prevalencia_prueba']:.1%} | "
                     f"{b['auc']:.2f} | {b['auc_solo_sofa']:.2f} | {b['brier']:.3f} | {b['brier_sin_modelo']:.3f} |")
    l += ["", "## Estancia (prueba)", "",
          "| Desenlace | Juego | Mediana observada | Error absoluto medio | Sin modelo | Cobertura p10-p90 | Acierto de banda | Banda sin modelo | Spearman |",
          "|---|---|---|---|---|---|---|---|---|"]
    for y in CONTINUOS:
        for j in JUEGOS:
            c = v[j]["continuos"][y]
            l.append(f"| {nombres[y]} | {j} | {c['mediana_observada']:.0f} | {c['mae_mediana']:.1f} | {c['mae_sin_modelo']:.1f} | "
                     f"{c['cobertura_p10_p90']:.0%} | {c['acierto_banda']:.0%} | {c['acierto_banda_sin_modelo']:.0%} | {c['spearman']:.2f} |")
    l += ["", "Bandas: 0-3, 4-7, 8-14 y 15 o más días.", "", "## Calibración del destino (juego ingreso, quintiles)", "",
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
