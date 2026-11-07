from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("sklearn")

from datos_prueba.egresos import generar_egresos, guardar_egresos
from datos_prueba.tablero import generar_tablero
from uci_control.__main__ import main
from uci_control.camas import gestionar_camas
from uci_control.conectores import leer_tablero
from uci_control.prediccion import ocupacion as o


@pytest.fixture(scope="module")
def estancias():
    return o.estancias_uci(generar_egresos(n=1500, semilla=11))


@pytest.fixture(scope="module")
def modelo(estancias):
    m = o.ajustar_egreso_diario(estancias, "2024-10-01", "2026-03-31")
    m.llegadas = o.ajustar_llegadas(estancias, "2026-03-31")
    return m


def _estancia(inicio: str, dias: int, **extra) -> dict:
    return {"inicio_uci": pd.Timestamp(inicio), "dias_en_uci_total": dias, "edad": 60, "sofa_mortalidad": 0.1,
            "barthel": 100, "news": 4, "sexo": "M", "asegurador": "SURA", "agrupador": "OTROS", "dx3": "I21", **extra}


def test_ubicacion_del_tramo_uci():
    crudo = generar_egresos(n=3, semilla=1).assign(
        fecha_ingreso_min=pd.to_datetime(["2026-01-01"] * 3),
        fecha_salida_max=pd.to_datetime(["2026-01-05", "2026-01-10", "2026-01-10"]),
        secuencia_servicios=["UCI", "UCI → HOSPITALIZACION", "HOSPITALIZACION → UCI"],
        dias_uci=[4, 3, 2], dias_hospitalizacion=[0, 6, 7], dias_total=[4, 9, 9])
    e = o.estancias_uci(crudo)
    assert list(e["inicio_uci"].dt.day) == [1, 1, 8]  # el que viene de piso entra a UCI 2 días antes de salir
    assert e["ubicacion_exacta"].all() and list(e["dias_en_uci_total"]) == [4, 3, 2]
    assert not {"paciente_id", "nombre", "ingreso"} & set(e.columns)


def test_censo_y_dias_paciente():
    e = pd.DataFrame([_estancia("2026-01-01", 3), _estancia("2026-01-02", 1), _estancia("2026-01-02", 0)])
    censo = o.censo_diario(e, "2025-12-31", "2026-01-05")
    assert list(censo) == [0, 1, 2, 1, 0, 0]  # la estancia de 0 días no cuenta en ningún censo
    dp = o.dias_paciente(e, "2026-01-01", "2026-01-05")
    assert len(dp) == 4 and dp["sale"].sum() == 2  # cada estancia sale una vez, en su último día
    ultimo = dp[(dp["sale"] == 1) & (dp["dias_en_uci"] == 2)].iloc[0]
    assert ultimo["fecha"] == pd.Timestamp("2026-01-03")
    assert ultimo["dia_semana_egreso"] == pd.Timestamp("2026-01-04").dayofweek  # el egreso es al día siguiente


def test_modelo_permanencia_y_recalibracion(modelo, estancias):
    dp = o.dias_paciente(estancias, "2026-04-01", "2026-06-30")
    p = modelo.probabilidad(dp)
    assert ((p > 0) & (p < 1)).all()
    ajuste = modelo.recalibrar(dp)
    assert -2 <= ajuste <= 2
    assert modelo.probabilidad(dp).sum() == pytest.approx(dp["sale"].sum(), rel=0.02)
    modelo.ajuste = 0.0
    hoy = dp[dp["fecha"] == dp["fecha"].max()].reset_index(drop=True)
    perm = modelo.permanencia(hoy, date(2026, 6, 30), 7)
    assert perm.shape == (len(hoy), 8) and (perm[:, 0] == 1).all()
    assert (np.diff(perm, axis=1) <= 1e-12).all()  # seguir es cada día menos probable
    seguro = modelo.permanencia(hoy, date(2026, 6, 30), 7, primer_paso=np.ones(len(hoy)))
    assert (seguro[:, 1:] == 0).all()  # si todos salen hoy, nadie sigue


def test_distribucion_exacta():
    d = o._distribucion(np.array([0.5, 0.2, 0.9]), 1.5, 40)
    assert d.sum() == pytest.approx(1, abs=1e-9)
    assert (np.arange(41) * d).sum() == pytest.approx(0.5 + 0.2 + 0.9 + 1.5, abs=1e-6)


def test_pronostico_desde_el_tablero(modelo, estancias):
    e1 = estancias[estancias["dias_en_uci_total"] >= 1]
    o_ = pd.Timestamp("2026-05-10")
    presentes = e1[(e1["inicio_uci"] <= o_) & (e1["inicio_uci"] + pd.to_timedelta(e1["dias_en_uci_total"], "D") > o_)].copy()
    presentes["dias_en_uci"] = (o_ - presentes["inicio_uci"]).dt.days.astype(float)
    dias = o.pronosticar(modelo, presentes, o_.date(), camas=len(presentes))
    assert [d.fecha for d in dias] == [o_.date() + timedelta(days=h) for h in range(1, 8)]
    for d in dias:
        assert d.esperados == pytest.approx(d.siguen_de_hoy + d.nuevos, abs=0.11)
        assert d.p10 <= d.esperados <= d.p90 + 1 and 0 <= d.probabilidad_llena <= 1
    assert dias[-1].siguen_de_hoy < dias[0].siguen_de_hoy
    # El tablero de la mañana del 11 es el censo del 10: mismo pronóstico, rotulado un día después.
    desde_tablero = o.pronosticar_tablero(modelo, presentes, o_.date() + timedelta(days=1), camas=len(presentes))
    assert [d.fecha for d in desde_tablero] == [d.fecha + timedelta(days=1) for d in dias]
    assert [d.esperados for d in desde_tablero] == [d.esperados for d in dias]


def test_filas_del_tablero(tmp_path):
    pacientes = leer_tablero(generar_tablero(tmp_path / "t.xlsx"))
    hoy = pacientes[0].fecha
    f = o.filas_tablero(pacientes, [0, 1, 2, 3], hoy)
    assert list(f["dia_semana_egreso"].unique()) == [hoy.weekday()]  # "sale" es hoy
    assert list(f["dias_en_uci"]) == [0, 1, 2, 3]


def test_cli_y_plan_de_camas(tmp_path, monkeypatch):
    ruta = guardar_egresos(tmp_path / "egresos.xlsx", n=1500, semilla=11)
    assert main(["modelo-ocupacion", "--egresos", str(ruta), "--salida", str(tmp_path / "m"), "--camas-totales", "30"]) == 0
    md = (tmp_path / "m" / "modelo_ocupacion.md").read_text(encoding="utf-8")
    assert "¿Quién sale hoy de la UCI?" in md and "Ocupación de los próximos 7 días" in md
    monkeypatch.setenv("UCI_MODELO_OCUPACION", str(tmp_path / "m" / "modelo_ocupacion.joblib"))
    o._CACHE.clear()
    pacientes = leer_tablero(generar_tablero(tmp_path / "t.xlsx"))
    plan = gestionar_camas(pacientes[0].fecha, pacientes, tmp_path / "h.sqlite", camas_totales=10, demanda_esperada=2)
    assert len(plan.ocupacion_7d) == 7 and plan.ocupacion_7d[0]["fecha"] == pacientes[0].fecha + timedelta(days=1)
    assert all(p.probabilidad_egreso_historica is not None for p in plan.pacientes)
    assert plan.egresos_esperados_historico == pytest.approx(sum(p.probabilidad_egreso_historica for p in plan.pacientes), abs=0.1)
    assert plan.fuente_egresos == "criterios de alta"  # sin modelo del estado diario
    o._CACHE.clear()


def test_curva_de_la_calculadora_igual_al_modelo(modelo):
    """La calculadora web evalúa el JSON exportado: debe dar lo mismo que el modelo."""
    js = o.exportar_json(modelo)
    v = {"edad": 70, "sexo": "F", "asegurador": "SURA", "dx3": "A41", "agrupador": "INFECCIOSO", "sofa_mortalidad": 0.33,
         "barthel": 40, "news": 7}
    ingreso = date(2026, 11, 6)
    for lleva in (0, 4):
        curva = o.curva_json(js, v, ingreso, lleva, 10)
        k0, primero = max(lleva - 1, 0), lleva if lleva > 0 else 1
        filas = pd.DataFrame([{**v, "dias_en_uci": float(k0 + j), "dia_semana_egreso": float((ingreso + timedelta(days=primero + j)).weekday())}
                              for j in range(10)])
        sigue = np.cumprod(1 - modelo.probabilidad(filas))
        assert curva[0]["fecha"] == ingreso + timedelta(days=primero)
        assert max(abs(c["sigue"] - s) for c, s in zip(curva, sigue)) < 1e-4
        assert sum(c["sale"] for c in curva) == pytest.approx(1 - curva[-1]["sigue"])
