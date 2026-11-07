"""Aprende del estado diario de cada paciente: ¿sale hoy de la UCI?

El modelo del consolidado de egresos (prediccion/ocupacion.py) solo sabe lo que se sabía al
ingreso, los días que lleva el paciente y el día de la semana. Lo que decide el egreso es el
estado de hoy: si ya salió del vasoactivo o del ventilador, si cumple criterios de alta, qué
pendiente lo retiene. Eso solo está en el tablero de ORBIT y en las entregas de turno, y la
clínica no lo guarda día a día. Este módulo:

1. guarda cada mañana una fila por paciente con ese estado (cama, seudónimo y variables; nunca
   nombre, documento ni número de ingreso);
2. la etiqueta con lo que pasó: el paciente salió si al día siguiente ya no aparece en ninguna
   fuente y su estación sí tiene datos ese día. Si de la estación no llegó nada, o llegó mucho
   menos que el día anterior, la fila queda sin etiqueta en vez de contarse como egreso;
3. con 300 días-paciente etiquetados y 30 egresos entrena un modelo, lo valida en las últimas
   semanas contra tres referencias (días en UCI y día de la semana, criterios de alta por
   reglas calibrados, y el modelo del consolidado) y lo reentrena cada semana. El plan de camas
   lo usa solo si en esa validación supera a las tres.

La fecha de un estado es la del tablero de la mañana que describe. Una entrega enviada antes de
la 1 p. m. (la del turno de la noche) describe a los pacientes de esa mañana; una enviada
después, a los de la mañana siguiente. Si llegan varias de la misma estación para la misma
mañana se usa la más reciente.

Un paciente se reconoce entre días por el seudónimo del documento que trae el tablero. En las
entregas se usa un hash con sal del número de ingreso; cuando una misma cama trae los dos el
mismo día, quedan enlazados. Sin ninguno de los dos se usa la cama.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from ..conectores.comun import sin_tildes
from ..conectores.orbit_tablero import PacienteTablero, seudonimo
from ..modelos import EntregaCama
from .alta import _O2_BAJO, evaluar_alta
from .barreras import CATALOGO

log = logging.getLogger(__name__)

MINIMO_DIAS_PACIENTE = 300
MINIMO_EGRESOS = 30
REENTRENAR_CADA = 7  # días
HORA_CORTE = 13  # una entrega enviada antes de esta hora describe la mañana del mismo día
COBERTURA_MINIMA = 0.5  # la estación debe traer al menos la mitad de las camas del día anterior
NOMBRE_MODELO = "modelo_egreso_estado.joblib"

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS estado (
    fecha TEXT NOT NULL,
    paciente TEXT NOT NULL,
    cama TEXT NOT NULL,
    estacion TEXT NOT NULL,
    fuente TEXT NOT NULL,
    variables TEXT NOT NULL,
    PRIMARY KEY (fecha, paciente)
);
CREATE TABLE IF NOT EXISTS cobertura (
    fecha TEXT NOT NULL,
    estacion TEXT NOT NULL,
    camas INTEGER NOT NULL,
    PRIMARY KEY (fecha, estacion)
);
CREATE TABLE IF NOT EXISTS alias (alias TEXT PRIMARY KEY, paciente TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cama_estacion (cama TEXT PRIMARY KEY, estacion TEXT NOT NULL, fecha TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS entrenamiento (
    fecha TEXT NOT NULL,
    dias_paciente INTEGER NOT NULL,
    egresos INTEGER NOT NULL,
    auc REAL,
    supera_referencias INTEGER,
    ruta TEXT
);
"""

BARRERAS = [f"b0{i}" for i in range(1, 9)]
NUMERICAS = ["dias_en_uci", "dia_semana_egreso", "vmi_total", "vaso_total", "alto_flujo", "o2_bajo", "news", "news_cambio",
             "sofa_mortalidad", "rass", "cpot", "eva", "barthel", "ims", "edad", "n_antimicrobianos", "n_restringidos",
             "dias_vmi", "n_pendientes", "n_dispositivos", "n_antibioticos", "trr", "aislamiento", "oncologico",
             "dias_sin_soporte", "retiro_soporte", "dias_listo", "p_reglas", "p_historica"] + BARRERAS
CATEGORICAS = {"criterio": ["UCI", "UCIN", "OTROS"], "estado_alta": ["listo", "casi", "no"],
               "condicion": ["UCI", "UCIN", "PISO", "DESCONOCIDA"]}


# ---------------------------------------------------------------- variables de cada fuente

def codigos_barrera(textos: Iterable[str]) -> list[str]:
    """Códigos de barrera (catálogo de barreras.py) que aparecen en los pendientes."""
    t = sin_tildes(" \n".join(textos)).upper()
    return [b.codigo for patron, b in CATALOGO if re.search(patron, t)]


def _o2(vent: str) -> tuple[int, int]:
    alto = int("ALTO FLUJO" in vent or "VMNI" in vent or "NO INVASIVA" in vent)
    bajo = int(bool(vent) and not alto and "VMI" not in vent and any(k in vent for k in _O2_BAJO))
    return alto, bajo


def variables_tablero(p: PacienteTablero) -> dict:
    from ..prediccion.trayectoria import barthel_a_numero

    vent = sin_tildes(p.soporte_ventilatorio or "").upper()
    alto, bajo = _o2(vent)
    ev = evaluar_alta(p)
    return {
        "criterio": p.criterio, "vmi": int(p.vmi), "vasoactivo": int(p.vasoactivo), "alto_flujo": alto, "o2_bajo": bajo,
        "news": p.news, "sofa_mortalidad": p.sofa_mortalidad, "rass": p.rass, "cpot": p.cpot, "eva": p.eva,
        "barthel": barthel_a_numero(p.barthel), "ims": p.ims, "edad": p.edad, "oncologico": int(p.oncologico),
        "n_antimicrobianos": len(p.antimicrobianos), "n_restringidos": sum(a.restringido for a in p.antimicrobianos),
        "dias_vmi": p.dias_vmi(), "n_pendientes": len(p.pendientes), "dias_hospitalizacion": p.dias_hospitalizacion,
        "estado_alta": ev.estado, "p_reglas": ev.probabilidad_egreso_hoy, "barreras": codigos_barrera(p.pendientes),
    }


def variables_entrega(c: EntregaCama) -> dict:
    vent = sin_tildes(c.ventilacion or "").upper()
    alto, bajo = _o2(vent)
    return {
        "condicion": c.condicion.value, "vmi_entrega": None if c.vmi is None else int(c.vmi),
        "n_vasoactivos": len(c.vasoactivos), "alto_flujo_entrega": alto if vent else None, "o2_bajo_entrega": bajo if vent else None,
        "trr": None if c.trr is None else int(c.trr), "n_dispositivos": len(c.dispositivos), "n_antibioticos": len(c.antibioticos),
        "aislamiento": int(bool(c.aislamiento)), "n_pendientes_entrega": len(c.pendientes),
        "barreras_entrega": codigos_barrera(f"{p.tipo} {p.descripcion}" for p in c.pendientes),
    }


def fecha_de_estado(fecha_correo: str | None, respaldo: date | None = None) -> date | None:
    """Mañana del tablero que describe una entrega según la hora de envío del correo."""
    try:
        enviado = parsedate_to_datetime(fecha_correo) if fecha_correo else None
    except (TypeError, ValueError, IndexError):
        enviado = None
    if enviado is None:
        return respaldo
    return enviado.date() if enviado.hour < HORA_CORTE else enviado.date() + timedelta(days=1)


def _estacion(texto: str | None) -> str:
    return re.sub(r"\s+", " ", sin_tildes(texto or "")).strip().upper()


def clave_episodio(episodio: str | None) -> str | None:
    """Hash con sal del número de ingreso de la entrega (el número no se guarda)."""
    if not episodio or not str(episodio).strip():
        return None
    return "e" + seudonimo("EP|" + str(episodio).strip().upper())


# ---------------------------------------------------------------- almacenamiento

class EstadosDiarios:
    """Estados de cada mañana en la misma base SQLite del historial de camas."""

    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)
        self.con = sqlite3.connect(ruta)
        self.con.executescript(_ESQUEMA)

    def cerrar(self) -> None:
        self.con.close()

    def registrar(self, fecha: date, tablero: Iterable[PacienteTablero] = (), entregas: Iterable[EntregaCama] = (),
                  extras: dict[str, dict] | None = None) -> int:
        """Guarda el estado de la mañana de `fecha` (reemplaza lo que hubiera de esa fecha).
        `entregas`: camas de las entregas que describen esa mañana; si una cama viene dos veces
        queda la última. `extras`: variables adicionales por cama, por ejemplo p_historica."""
        f = fecha.isoformat()
        por_cama: dict[str, dict] = {}
        for c in entregas:
            if c.cama:
                por_cama.setdefault(c.cama, {})["entrega"] = c
        for p in tablero:
            por_cama.setdefault(p.cama, {})["tablero"] = p
        for cama, d in por_cama.items():
            e = d.get("entrega")
            if e is not None and _estacion(e.estacion):
                self.con.execute("INSERT OR REPLACE INTO cama_estacion VALUES (?, ?, ?)", (cama, _estacion(e.estacion), f))
        mapa = dict(self.con.execute("SELECT cama, estacion FROM cama_estacion"))
        filas: dict[str, tuple] = {}
        for cama, d in sorted(por_cama.items()):
            p, e = d.get("tablero"), d.get("entrega")
            doc = p.seudonimo if p is not None else None
            ep = clave_episodio(e.episodio) if e is not None else None
            if doc and ep:
                self.con.execute("INSERT OR REPLACE INTO alias VALUES (?, ?)", (ep, doc))
            clave = doc or ep or f"cama:{cama}"
            variables: dict = {}
            if p is not None:
                variables.update(variables_tablero(p))
            if e is not None:
                variables.update(variables_entrega(e))
            variables.update((extras or {}).get(cama, {}))
            estacion = _estacion(e.estacion) if e is not None and _estacion(e.estacion) else mapa.get(cama, "TABLERO")
            fuente = "ambos" if p is not None and e is not None else "tablero" if p is not None else "entrega"
            if clave in filas and filas[clave][4] != "entrega":
                continue  # el mismo paciente en dos camas el mismo día: queda la fila con tablero
            filas[clave] = (f, clave, cama, estacion, fuente, json.dumps(variables, ensure_ascii=False, default=float))
        self.con.execute("DELETE FROM estado WHERE fecha = ?", (f,))
        self.con.execute("DELETE FROM cobertura WHERE fecha = ?", (f,))
        self.con.executemany("INSERT INTO estado VALUES (?, ?, ?, ?, ?, ?)", list(filas.values()))
        conteo: dict[str, int] = {}
        for fila in filas.values():
            conteo[fila[3]] = conteo.get(fila[3], 0) + 1
        self.con.executemany("INSERT INTO cobertura VALUES (?, ?, ?)", [(f, k, n) for k, n in conteo.items()])
        self.con.commit()
        return len(filas)

    def fechas(self) -> list[date]:
        return [date.fromisoformat(f) for (f,) in self.con.execute("SELECT DISTINCT fecha FROM estado ORDER BY fecha")]

    def tabla(self) -> pd.DataFrame:
        """Todas las filas con las variables, las derivadas del día anterior y la etiqueta
        `sale` (1 salió, 0 siguió, vacía si no se puede saber)."""
        crudo = pd.read_sql_query("SELECT fecha, paciente, cama, estacion, fuente, variables FROM estado", self.con)
        if crudo.empty:
            return pd.DataFrame(columns=["fecha", "paciente", "cama", "estacion", "fuente", "sale"] + NUMERICAS + list(CATEGORICAS))
        alias = dict(self.con.execute("SELECT alias, paciente FROM alias"))
        crudo["paciente"] = crudo["paciente"].map(lambda k: alias.get(k, k))
        crudo["fecha"] = pd.to_datetime(crudo["fecha"])
        crudo["_orden"] = crudo["fuente"].map({"ambos": 0, "tablero": 1, "entrega": 2})
        crudo = crudo.sort_values(["fecha", "paciente", "_orden"]).drop_duplicates(["fecha", "paciente"])
        variables = pd.DataFrame(list(crudo["variables"].map(json.loads)), index=crudo.index)
        x = pd.concat([crudo.drop(columns=["variables", "_orden"]), variables], axis=1)
        cob = pd.read_sql_query("SELECT fecha, estacion, camas FROM cobertura", self.con)
        cob["fecha"] = pd.to_datetime(cob["fecha"])
        return _etiquetar(_derivadas(x, cob), cob)

    def resumen(self) -> dict:
        x = self.tabla()
        et = x[x["sale"].notna()] if len(x) else x
        return {"dias": int(x["fecha"].nunique()) if len(x) else 0, "dias_paciente": len(x),
                "etiquetados": len(et), "egresos": int(et["sale"].sum()) if len(et) else 0}

    def ultimo_entrenamiento(self) -> date | None:
        r = self.con.execute("SELECT MAX(fecha) FROM entrenamiento WHERE ruta IS NOT NULL").fetchone()[0]
        return date.fromisoformat(r) if r else None

    def anotar_entrenamiento(self, fecha: date, resumen: dict, ruta: Path | None) -> None:
        self.con.execute("INSERT INTO entrenamiento VALUES (?, ?, ?, ?, ?, ?)",
                         (fecha.isoformat(), resumen.get("etiquetados", 0), resumen.get("egresos", 0),
                          resumen.get("validacion", {}).get("modelo", {}).get("auc"),
                          int(bool(resumen.get("supera_referencias"))), str(ruta) if ruta else None))
        self.con.commit()


def _numero(serie: pd.Series) -> pd.Series:
    return pd.to_numeric(serie, errors="coerce")


def _derivadas(x: pd.DataFrame, cob: pd.DataFrame) -> pd.DataFrame:
    x = x.copy()
    for c in ["vmi", "vasoactivo", "vmi_entrega", "n_vasoactivos", "alto_flujo", "alto_flujo_entrega", "o2_bajo",
              "o2_bajo_entrega", "news", "dias_hospitalizacion"] + NUMERICAS:
        if c not in x:
            x[c] = np.nan
    for c in ("barreras", "barreras_entrega", *CATEGORICAS):
        if c not in x:
            x[c] = None
    x["vmi_total"] = np.fmax(_numero(x["vmi"]), _numero(x["vmi_entrega"]))
    x["vaso_total"] = np.fmax(_numero(x["vasoactivo"]), (_numero(x["n_vasoactivos"]) > 0).where(x["n_vasoactivos"].notna()).astype(float))
    x["alto_flujo"] = np.fmax(_numero(x["alto_flujo"]), _numero(x["alto_flujo_entrega"]))
    x["o2_bajo"] = np.fmax(_numero(x["o2_bajo"]), _numero(x["o2_bajo_entrega"]))
    codigos = [set(a if isinstance(a, list) else []) | set(b if isinstance(b, list) else [])
               for a, b in zip(x["barreras"], x["barreras_entrega"])]
    for b in BARRERAS:
        x[b] = [float(b.upper() in c) for c in codigos]
    x["dia_semana_egreso"] = x["fecha"].dt.dayofweek.astype(float)

    cubiertos = set(zip(cob["fecha"], cob["estacion"]))
    con_datos = set(cob["fecha"])
    x = x.sort_values(["paciente", "fecha"]).reset_index(drop=True)
    anterior = x.groupby("paciente")["fecha"].shift()
    brecha = (x["fecha"] - anterior).dt.days
    # Nueva estancia si entre dos apariciones hubo un día con datos en que el paciente no estaba.
    hueco_con_datos = np.array([any(a + pd.Timedelta(days=k) in con_datos for k in range(1, int(g))) if pd.notna(g) and g > 1
                                else False for a, g in zip(anterior, brecha)], dtype=bool)
    nueva = anterior.isna() | ((brecha > 1) & hueco_con_datos)
    x["estancia"] = nueva.groupby(x["paciente"]).cumsum()
    grupo = [x["paciente"], x["estancia"]]
    inicio = x.groupby(grupo)["fecha"].transform("min")
    # Si el día anterior al inicio no hubo datos de la estación, no se sabe desde cuándo estaba:
    # se suma la estancia hospitalaria que reporta el tablero el primer día.
    primera = x.groupby(grupo).cumcount() == 0
    censurado = primera & np.array([(f - pd.Timedelta(days=1), e) not in cubiertos for f, e in zip(x["fecha"], x["estacion"])], dtype=bool)
    extra = np.floor(_numero(x["dias_hospitalizacion"]).where(censurado)).fillna(0)
    x["_extra"] = extra
    x["dias_en_uci"] = (x["fecha"] - inicio).dt.days + x.groupby(grupo)["_extra"].transform("max")

    previo = x.groupby(grupo).shift()
    consecutivo = (x["fecha"] - previo["fecha"]).dt.days == 1
    soporte = (x["vmi_total"].fillna(0) + x["vaso_total"].fillna(0)) > 0
    soporte_previo = ((_numero(previo["vmi_total"]).fillna(0) + _numero(previo["vaso_total"]).fillna(0)) > 0)
    x["retiro_soporte"] = (consecutivo & soporte_previo & ~soporte).astype(float)
    x["news_cambio"] = (_numero(x["news"]) - _numero(previo["news"])).where(consecutivo)

    def racha(serie: pd.Series) -> pd.Series:
        bloque = (~serie).cumsum()
        return serie.groupby(bloque).cumsum()

    x["_sin_soporte"] = ~soporte
    x["dias_sin_soporte"] = x.groupby(grupo)["_sin_soporte"].transform(racha).astype(float)
    # Días previos seguidos en estado "listo" (hoy no cuenta: todavía puede salir).
    listo = (x["estado_alta"] == "listo") if "estado_alta" in x else pd.Series(False, index=x.index)
    x["_listo"] = listo
    x["dias_listo"] = (x.groupby(grupo)["_listo"].transform(racha) - listo.astype(int)).clip(lower=0).astype(float)
    return x.drop(columns=["_extra", "_sin_soporte", "_listo"])


def _etiquetar(x: pd.DataFrame, cob: pd.DataFrame) -> pd.DataFrame:
    """sale = 1 si al día siguiente el paciente ya no aparece (y tampoco al otro día, cuando hay
    datos), con la estación cubierta ese día; 0 si sigue; vacía si no se puede saber."""
    camas = {(f, e): n for f, e, n in cob[["fecha", "estacion", "camas"]].itertuples(index=False)}
    presentes = set(zip(x["fecha"], x["paciente"]))
    con_datos = set(cob["fecha"])
    sale = []
    for f, p, e in zip(x["fecha"], x["paciente"], x["estacion"]):
        manana, pasado = f + pd.Timedelta(days=1), f + pd.Timedelta(days=2)
        if (manana, p) in presentes:
            sale.append(0.0)
            continue
        hoy_n, manana_n = camas.get((f, e), 0), camas.get((manana, e), 0)
        if manana_n == 0 or manana_n < COBERTURA_MINIMA * hoy_n:
            sale.append(np.nan)  # de la estación no llegó (casi) nada: no se sabe
        elif pasado in con_datos and (pasado, p) in presentes:
            sale.append(np.nan)  # faltó un día y volvió a aparecer: dato incompleto, no egreso
        else:
            sale.append(1.0)
    x = x.copy()
    x["sale"] = sale
    return x.sort_values(["fecha", "cama"]).reset_index(drop=True)


# ---------------------------------------------------------------- modelo

def _matriz(x: pd.DataFrame) -> pd.DataFrame:
    m = pd.DataFrame(index=x.index)
    for c in NUMERICAS:
        m[c] = _numero(x[c]) if c in x else np.nan
    for c, niveles in CATEGORICAS.items():
        v = x[c].astype(object) if c in x else pd.Series(None, index=x.index, dtype=object)
        m[c] = pd.Categorical(v.where(v.isin(niveles)), categories=niveles)
    return m.astype({c: float for c in NUMERICAS})


def _matriz_entrenamiento(x: pd.DataFrame) -> pd.DataFrame:
    """Como _matriz, pero las columnas sin ningún dato quedan en 0 (el binning de scikit-learn
    no acepta columnas vacías; constantes no se usan en ningún corte)."""
    m = _matriz(x)
    for c in NUMERICAS:
        if m[c].isna().all():
            m[c] = 0.0
    return m


def _ajustar(x: pd.DataFrame):
    from sklearn.ensemble import HistGradientBoostingClassifier

    # Pocos datos al comienzo: árboles pequeños con hojas grandes.
    clf = HistGradientBoostingClassifier(max_iter=150, learning_rate=0.05, max_leaf_nodes=7,
                                         min_samples_leaf=max(15, len(x) // 40), l2_regularization=1.0,
                                         categorical_features="from_dtype", random_state=7)
    return clf.fit(_matriz_entrenamiento(x), x["sale"].astype(int))


@dataclass
class ModeloEgresoEstado:
    clasificador: object
    entrenado_hasta: str
    n_dias_paciente: int
    n_egresos: int
    supera_referencias: bool
    metricas: dict = field(default_factory=dict)

    def probabilidad(self, x: pd.DataFrame) -> np.ndarray:
        return self.clasificador.predict_proba(_matriz(x))[:, 1]

    def guardar(self, ruta: Path) -> None:
        import joblib
        joblib.dump(self, ruta)

    @staticmethod
    def cargar(ruta: Path) -> "ModeloEgresoEstado":
        """Solo cargar archivos generados por este mismo agente: un .joblib puede ejecutar código."""
        import joblib
        return joblib.load(ruta)


def _tramo_dias(d: pd.Series) -> pd.Series:
    return pd.cut(_numero(d).fillna(0), [-1, 0, 1, 2, 4, 7, 14, 10_000], labels=False)


def _tasa(ent: pd.DataFrame, pru: pd.DataFrame, columnas: list[pd.Series], columnas_pru: list[pd.Series]) -> np.ndarray:
    """Tasa de egreso observada en el entrenamiento por grupo, suavizada hacia la media."""
    media = ent["sale"].mean()
    g = ent.groupby(columnas)["sale"].agg(["sum", "count"])
    suave = (g["sum"] + 5 * media) / (g["count"] + 5)
    clave = pd.MultiIndex.from_arrays(columnas_pru) if len(columnas_pru) > 1 else pd.Index(columnas_pru[0])
    return suave.reindex(clave).fillna(media).to_numpy()


def _medidas(y: np.ndarray, p: np.ndarray) -> dict:
    from sklearn.metrics import brier_score_loss, roc_auc_score

    p = np.clip(np.asarray(p, dtype=float), 1e-4, 1 - 1e-4)
    return {"auc": round(float(roc_auc_score(y, p)), 3) if len(set(y)) > 1 else None,
            "brier": round(float(brier_score_loss(y, p)), 4),
            "log_loss": round(float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), 4)}


def validar(x: pd.DataFrame, fraccion_prueba: float = 0.25, minimo_dias_prueba: int = 14) -> dict:
    """Entrena con los días más antiguos y mide en las últimas semanas (al menos 14 días)."""
    et = x[x["sale"].notna()].copy()
    fechas = sorted(et["fecha"].unique())
    n_prueba = max(minimo_dias_prueba, int(len(fechas) * fraccion_prueba))
    if len(fechas) < n_prueba + 14:
        return {"estado": "pocos días para validar", "dias": len(fechas)}
    corte = fechas[-n_prueba]
    ent, pru = et[et["fecha"] < corte], et[et["fecha"] >= corte]
    if pru["sale"].sum() < 10 or ent["sale"].sum() < 20:
        return {"estado": "pocos egresos para validar", "egresos_entrenamiento": int(ent["sale"].sum()),
                "egresos_prueba": int(pru["sale"].sum())}
    clf = _ajustar(ent)
    y = pru["sale"].astype(int).to_numpy()
    p = clf.predict_proba(_matriz(pru))[:, 1]
    finde = lambda d: (d["dia_semana_egreso"] >= 5).astype(int)
    refs = {
        "dias_y_dia_semana": _tasa(ent, pru, [_tramo_dias(ent["dias_en_uci"]), finde(ent)],
                                   [_tramo_dias(pru["dias_en_uci"]), finde(pru)]),
        "criterios_de_alta": _tasa(ent, pru, [ent["estado_alta"].fillna("sin dato"), ent["criterio"].fillna("sin dato")],
                                   [pru["estado_alta"].fillna("sin dato"), pru["criterio"].fillna("sin dato")]),
    }
    if _numero(pru["p_historica"]).notna().mean() > 0.9:
        refs["modelo_del_consolidado"] = _numero(pru["p_historica"]).fillna(et["sale"].mean()).to_numpy()
    medidas = {"modelo": _medidas(y, p), **{k: _medidas(y, v) for k, v in refs.items()}}
    diario = pd.DataFrame({"fecha": pru["fecha"], "y": y, "p": p}).groupby("fecha").sum()
    k = max(1, round(float(diario["y"].mean())))
    top = pru.assign(p=p).sort_values("p", ascending=False).groupby("fecha").head(k)
    supera = all(medidas["modelo"]["brier"] < m["brier"] for c, m in medidas.items() if c != "modelo")
    return {"estado": "validado", "entrenamiento": f"{pd.Timestamp(fechas[0]).date()} a {(pd.Timestamp(corte) - pd.Timedelta(days=1)).date()}",
            "prueba": f"{pd.Timestamp(corte).date()} a {pd.Timestamp(fechas[-1]).date()}",
            "dias_paciente_prueba": len(pru), "egresos_prueba": int(y.sum()), **medidas,
            "egresos_dia_observados": round(float(diario["y"].mean()), 2), "egresos_dia_predichos": round(float(diario["p"].mean()), 2),
            "error_egresos_dia": round(float((diario["y"] - diario["p"]).abs().mean()), 2),
            "k_mas_probables": k, "salen_entre_los_k_mas_probables": round(float(top["sale"].mean()), 3),
            "salen_en_promedio": round(float(y.mean()), 3), "supera_referencias": supera}


def entrenar(estados: EstadosDiarios, hoy: date | None = None) -> tuple[ModeloEgresoEstado | None, dict]:
    x = estados.tabla()
    et = x[x["sale"].notna()]
    resumen = {"dias": int(x["fecha"].nunique()) if len(x) else 0, "dias_paciente": len(x), "etiquetados": len(et),
               "egresos": int(et["sale"].sum()) if len(et) else 0}
    if resumen["etiquetados"] < MINIMO_DIAS_PACIENTE or resumen["egresos"] < MINIMO_EGRESOS:
        return None, {**resumen, "estado": "insuficiente",
                      "faltan_dias_paciente": max(0, MINIMO_DIAS_PACIENTE - resumen["etiquetados"]),
                      "faltan_egresos": max(0, MINIMO_EGRESOS - resumen["egresos"])}
    val = validar(x)
    clf = _ajustar(et)
    hasta = str(pd.Timestamp(et["fecha"].max()).date())
    importancia = _importancia(clf, et)
    resumen = {**resumen, "estado": "entrenado", "entrenado_hasta": hasta, "validacion": val,
               "supera_referencias": bool(val.get("supera_referencias")), "importancia": importancia,
               "fecha_entrenamiento": str(hoy or date.today())}
    return ModeloEgresoEstado(clf, hasta, len(et), resumen["egresos"], resumen["supera_referencias"], resumen), resumen


def _importancia(clf, et: pd.DataFrame, n: int = 10) -> list[dict]:
    from sklearn.inspection import permutation_importance

    muestra = et.sample(min(len(et), 3000), random_state=7)
    r = permutation_importance(clf, _matriz_entrenamiento(muestra), muestra["sale"].astype(int), scoring="neg_brier_score",
                               n_repeats=3, random_state=7)
    orden = np.argsort(-r.importances_mean)[:n]
    columnas = NUMERICAS + list(CATEGORICAS)
    return [{"variable": columnas[i], "importancia": round(float(r.importances_mean[i]), 5)} for i in orden
            if r.importances_mean[i] > 0]


# ---------------------------------------------------------------- uso diario

def ruta_modelo(historial: str | Path) -> Path:
    """UCI_MODELO_EGRESO_ESTADO o, por defecto, junto al historial de camas."""
    return Path(os.environ.get("UCI_MODELO_EGRESO_ESTADO") or Path(historial).with_name(NOMBRE_MODELO))


_CACHE: dict[tuple[str, float], ModeloEgresoEstado] = {}


def modelo_configurado(historial: str | Path) -> ModeloEgresoEstado | None:
    ruta = ruta_modelo(historial)
    if not ruta.exists():
        return None
    clave = (str(ruta), ruta.stat().st_mtime)
    if clave not in _CACHE:
        _CACHE.clear()
        _CACHE[clave] = ModeloEgresoEstado.cargar(ruta)
    return _CACHE[clave]


def probabilidades_de_hoy(estados: EstadosDiarios, fecha: date, modelo: ModeloEgresoEstado) -> dict[str, float]:
    """P(sale hoy) por cama para los pacientes registrados en la mañana de `fecha`."""
    x = estados.tabla()
    hoy = x[x["fecha"] == pd.Timestamp(fecha)]
    if hoy.empty:
        return {}
    return {c: round(float(p), 3) for c, p in zip(hoy["cama"], modelo.probabilidad(hoy))}


def actualizar(historial: str | Path, fecha: date, forzar: bool = False) -> str:
    """Reentrena el modelo del estado diario si pasaron REENTRENAR_CADA días desde el último
    (o nunca se entrenó) y hay datos suficientes. Devuelve una línea para el registro."""
    estados = EstadosDiarios(historial)
    try:
        ultimo = estados.ultimo_entrenamiento()
        if not forzar and ultimo and (fecha - ultimo).days < REENTRENAR_CADA:
            return f"Modelo del estado diario entrenado el {ultimo:%d/%m}; el próximo reentrenamiento es el {ultimo + timedelta(days=REENTRENAR_CADA):%d/%m}."
        modelo, resumen = entrenar(estados, fecha)
        if modelo is None:
            return (f"Estado diario: {resumen['etiquetados']} días-paciente con desenlace y {resumen['egresos']} egresos; "
                    f"para entrenar faltan {resumen['faltan_dias_paciente']} días-paciente y {resumen['faltan_egresos']} egresos.")
        ruta = ruta_modelo(historial)
        modelo.guardar(ruta)
        ruta.with_suffix(".md").write_text(a_markdown(resumen), encoding="utf-8")
        ruta.with_suffix(".json").write_text(json.dumps(resumen, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        estados.anotar_entrenamiento(fecha, resumen, ruta)
        v = resumen["validacion"]
        if v.get("estado") != "validado":
            return f"Modelo del estado diario entrenado con {resumen['etiquetados']} días-paciente; sin validación aún ({v.get('estado')}). El plan sigue con los criterios de alta."
        uso = "el plan de camas lo usa desde hoy" if modelo.supera_referencias else "todavía no supera a las referencias: el plan sigue con los criterios de alta"
        return (f"Modelo del estado diario reentrenado con {resumen['etiquetados']} días-paciente: AUC {v['modelo']['auc']:.2f} "
                f"(criterios de alta {v['criterios_de_alta']['auc']:.2f}, días y día de la semana {v['dias_y_dia_semana']['auc']:.2f}); {uso}.")
    finally:
        estados.cerrar()


# ---------------------------------------------------------------- carga del histórico de correos

def ultimas_por_estacion(items: list) -> list[EntregaCama]:
    """items: (correo, entrega leída). De cada estación quedan las camas de la entrega enviada
    más tarde. Sin estación en el encabezado se usa el buzón que la envió."""
    ultima = {}
    for c, e in sorted(items, key=lambda t: _orden_envio(t[0].fecha)):
        estacion = _estacion(e.estacion) or _estacion(c.remitente.split("<")[-1].split("@")[0]) or c.id
        for cama in e.camas:
            cama.estacion = cama.estacion or estacion
        ultima[estacion] = e
    return [cama for e in ultima.values() for cama in e.camas]


def cargar_historico(fuente, desde: date, hasta: date, historial: str | Path) -> list[dict]:
    """Lee las entregas (y los tableros adjuntos) de `desde` a `hasta` y guarda el estado de cada
    mañana. Cada correo se lee una vez aunque aparezca en dos días de búsqueda."""
    import hashlib

    from ..conectores import CarpetaCorreos, parsear_entrega

    vistos, firmas = set(), set()
    entregas: dict[date, list] = {}
    adjuntos: list[tuple[str, bytes]] = []
    dia = desde - timedelta(days=1)
    # Una carpeta de .eml no filtra por fecha: se recorre una sola vez.
    ultimo_dia = dia if isinstance(fuente, CarpetaCorreos) else hasta
    while dia <= ultimo_dia:
        for c in fuente.entregas(dia):
            if c.id in vistos:
                continue
            vistos.add(c.id)
            e = parsear_entrega(c.texto, fuente=c.id, fecha_referencia=dia)
            f = fecha_de_estado(c.fecha, e.fecha)
            if f is not None and e.camas:
                entregas.setdefault(f, []).append((c, e))
            for t in c.tableros:
                firma = hashlib.sha1(t[1]).hexdigest()
                if firma not in firmas:
                    firmas.add(firma)
                    adjuntos.append(t)
        for t in fuente.tableros(dia):
            firma = hashlib.sha1(t[1]).hexdigest()
            if firma not in firmas:
                firmas.add(firma)
                adjuntos.append(t)
        dia += timedelta(days=1)
    tableros = _tableros_por_fecha(adjuntos)
    estados = EstadosDiarios(historial)
    resumen = []
    try:
        for f in sorted(set(entregas) | set(tableros)):
            if not desde <= f <= hasta:
                continue
            camas = ultimas_por_estacion(entregas.get(f, []))
            n = estados.registrar(f, tableros.get(f, []), camas)
            resumen.append({"fecha": f, "pacientes": n, "camas_entrega": len(camas),
                            "estaciones": len({c.estacion for c in camas}), "camas_tablero": len(tableros.get(f, []))})
    finally:
        estados.cerrar()
    return resumen


def _orden_envio(fecha_correo: str) -> str:
    try:
        return parsedate_to_datetime(fecha_correo).isoformat()
    except (TypeError, ValueError, IndexError):
        return ""


def _tableros_por_fecha(adjuntos: list[tuple[str, bytes]]) -> dict[date, list[PacienteTablero]]:
    """Pacientes de cada tablero agrupados por la fecha del tablero; si dos tableros de la misma
    fecha traen la misma cama queda el último."""
    from ..diario import leer_tableros

    por_fecha: dict[date, dict[str, PacienteTablero]] = {}
    for nombre, datos in adjuntos:
        for p in leer_tableros([(nombre, datos)]):
            por_fecha.setdefault(p.fecha, {})[p.cama] = p
    return {f: sorted(d.values(), key=lambda p: p.cama) for f, d in por_fecha.items()}


# ---------------------------------------------------------------- informe

def a_markdown(r: dict) -> str:
    l = ["# Modelo del estado diario: ¿sale hoy de la UCI?", ""]
    if r.get("estado") == "insuficiente":
        l.append(f"Hay {r['etiquetados']} días-paciente con desenlace y {r['egresos']} egresos. Faltan {r['faltan_dias_paciente']} "
                 f"días-paciente y {r['faltan_egresos']} egresos para entrenar.")
        return "\n".join(l) + "\n"
    v = r["validacion"]
    l.append(f"{r['dias']} mañanas guardadas, {r['etiquetados']} días-paciente con desenlace y {r['egresos']} egresos de UCI "
             f"(entrenado hasta {r['entrenado_hasta']}).")
    if v.get("estado") != "validado":
        l += ["", f"Sin validación todavía: {v.get('estado')}. El plan de camas sigue con los criterios de alta."]
        return "\n".join(l) + "\n"
    nombres = {"modelo": "Modelo del estado diario", "criterios_de_alta": "Criterios de alta (reglas, calibrados)",
               "dias_y_dia_semana": "Días en UCI y día de la semana", "modelo_del_consolidado": "Modelo del consolidado de egresos"}
    l += ["", f"## Validación ({v['prueba']}, {v['dias_paciente_prueba']} días-paciente, {v['egresos_prueba']} egresos)", "",
          "| | AUC | Brier | Log-loss |", "|---|---|---|---|"]
    for clave, nombre in nombres.items():
        if clave in v:
            m = v[clave]
            l.append(f"| {nombre} | {m['auc'] if m['auc'] is not None else '—'} | {m['brier']} | {m['log_loss']} |")
    l += ["", f"Egresos por día: observados {v['egresos_dia_observados']}, predichos {v['egresos_dia_predichos']} "
              f"(error diario {v['error_egresos_dia']}). De los {v['k_mas_probables']} pacientes con mayor probabilidad cada día "
              f"salió el {v['salen_entre_los_k_mas_probables']:.0%}, frente al {v['salen_en_promedio']:.0%} de un paciente cualquiera.",
          "", ("El modelo supera a las referencias en la validación: el plan de camas lo usa para los egresos esperados."
               if r["supera_referencias"] else "El modelo todavía no supera a todas las referencias: el plan de camas sigue con los criterios de alta.")]
    if r.get("importancia"):
        l += ["", "Variables que más pesan: " + ", ".join(i["variable"] for i in r["importancia"]) + "."]
    return "\n".join(l) + "\n"
