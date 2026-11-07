"""Consolidado de egresos UCI sintético (mismas columnas que el archivo real, sin pacientes reales)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

_DX = [("I219 - INFARTO AGUDO DEL MIOCARDIO", 2, 0.45), ("A419 - SEPSIS, NO ESPECIFICADA", 7, 0.25),
       ("J960 - INSUFICIENCIA RESPIRATORIA AGUDA", 6, 0.35), ("C61X - TUMOR MALIGNO DE LA PROSTATA", 3, 0.15),
       ("O141 - PREECLAMPSIA SEVERA", 2, 0.1), ("E440 - DESNUTRICION PROTEICOCALORICA MODERADA | I500 - INSUFICIENCIA CARDIACA", 4, 0.3)]
_ASEG = ["NUEVA EMPRESA PROMOTORA DE SALUD SA", "EPS SURAMERICANA S A", "ENTIDAD PROMOTORA DE SALUD SANITAS SAS",
         "NAGICO INSURE COMPANY LIMITED"]
_BANDAS = [(0, "0%"), (2, "6-8%"), (5, "22-25%"), (8, "32-35%"), (10, "50%"), (12, "80%")]


def generar_egresos(n: int = 900, semilla: int = 3, inicio: str = "2024-10-01", fin: str = "2026-09-30") -> pd.DataFrame:
    r = np.random.default_rng(semilla)
    ingreso = pd.to_datetime(inicio) + pd.to_timedelta(r.integers(0, (pd.to_datetime(fin) - pd.to_datetime(inicio)).days - 20, n), "D")
    k = r.integers(0, len(_DX), n)
    sofa = np.clip(r.poisson(np.array([_DX[i][1] for i in k]) * 0.6), 0, 14)
    edad = np.clip(r.normal(63, 17, n), 18, 98).round()
    barthel = np.where(r.random(n) < 0.7, 100, r.choice([0, 20, 40, 60, 80], n))
    log_uci = 0.6 + 0.12 * sofa + 0.004 * (edad - 60) + r.normal(0, 0.55, n)
    dias_uci = np.maximum(np.expm1(log_uci).round(), 0).astype(int)
    p_directa = 1 / (1 + np.exp(-(np.log(np.array([_DX[i][2] for i in k]) / (1 - np.array([_DX[i][2] for i in k]))) - 0.01 * (100 - barthel))))
    directa = r.random(n) < p_directa
    dias_hosp = np.where(directa, 0, np.maximum(np.expm1(1.2 + 0.1 * sofa + r.normal(0, 0.6, n)).round(), 1)).astype(int)
    rebote = (~directa) & (r.random(n) < 0.04 + 0.01 * sofa)
    desde_piso = r.random(n) < 0.22
    seq = []
    for d, rb, piso in zip(directa, rebote, desde_piso):
        s = (["HOSPITALIZACION"] if piso else []) + ["UCI"] + ([] if d else ["HOSPITALIZACION"])
        if rb:
            s += ["UCI", "HOSPITALIZACION"]
        seq.append(" → ".join(s))
    banda = [next(t for lim, t in reversed(_BANDAS) if s >= lim) for s in sofa]
    pid = [f"SINT{i:05d}" for i in r.integers(0, int(n * 0.92), n)]
    salida = ingreso + pd.to_timedelta(dias_uci + dias_hosp, "D")
    return pd.DataFrame({
        "ingreso": np.arange(n) + 9_000_000, "fecha_ingreso_min": ingreso, "fecha_salida_max": salida,
        "paciente_id": pid, "nombre": "PACIENTE SINTETICO", "tercero": r.choice(_ASEG, n, p=[.4, .3, .25, .05]),
        "diagnostico": [_DX[i][0] for i in k], "dias_uci": dias_uci, "dias_hospitalizacion": dias_hosp,
        "dias_total": dias_uci + dias_hosp, "sexo_id": r.choice(["M", "F"], n), "edad": edad,
        "puntuacion_sofa_inicial": sofa, "puntuacion_sofa_final": sofa, "mortalidad_sofa_inicial": banda,
        "mortalidad_sofa_final": banda, "barthel_inicial": barthel, "barthel_final": barthel,
        "score_inicial": np.clip(r.poisson(2 + sofa * 0.4), 0, 20), "score_final": 2, "peso": r.normal(68, 12, n).round(),
        "talla": 0, "fc": r.normal(80, 12, n).round(), "ta_alta": r.normal(115, 15, n).round(),
        "ta_baja": r.normal(67, 10, n).round(), "temp_piel": r.normal(36, 0.4, n).round(1),
        "servicio_inicial": np.where(desde_piso, "HOSPITALIZACION", "UCI"),
        "servicio_final": np.where(directa, "UCI", "HOSPITALIZACION"), "numero_traslados": 1,
        "secuencia_servicios": seq, "observacion": 0,
    })


def guardar_egresos(ruta: Path, **kw) -> Path:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    generar_egresos(**kw).to_excel(ruta, index=False)
    return ruta
