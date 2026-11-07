import json
from dataclasses import replace

import pytest

pytest.importorskip("sklearn")

from datos_prueba.egresos import generar_egresos, guardar_egresos
from datos_prueba.tablero import generar_tablero
from uci_control.__main__ import main
from uci_control.conectores import leer_tablero
from uci_control.prediccion import evaluar, trayectoria as t


@pytest.fixture(scope="module")
def x():
    return t.preparar(generar_egresos())


def test_preparar_sin_identificadores_ni_fuga(x):
    assert not {"paciente_id", "nombre", "ingreso"} & set(x.columns)
    fuga = {"dias_uci", "dias_total", "barthel_final", "fc", "tas", "tad", "temperatura", "numero_traslados"}
    for num, cat in t.JUEGOS.values():
        assert not fuga & set(num + cat)
    assert set(x["alta_directa"].unique()) <= {0, 1}
    assert x["rebote_uci"].mean() > 0
    assert x["ingresos_previos"].max() >= 1  # hay pacientes repetidos


def test_utilidades():
    assert t.diagnostico_principal(["E440", "I10X", "I219"]) == "I219"
    assert t.diagnostico_principal(["E440"]) == "E440"
    assert t.grupo_asegurador("NAGICO INSURE COMPANY LIMITED") == "INTERNACIONAL"
    assert t.grupo_asegurador("EPS SURAMERICANA S A") == "SURA"
    assert t.barthel_a_numero("DEPENDENCIA SEVERA") == 40 and t.barthel_a_numero("INDEPENDIENTE") == 100
    assert t.barthel_a_numero(None) is None


def test_restante_condicional():
    antes = t.restante_condicional(2, 4, 12, 0)
    assert 3 <= antes <= 5
    # quien ya superó lo esperado sigue teniendo días por delante, no cero
    assert t.restante_condicional(2, 4, 12, 10) >= 2
    assert t.restante_condicional(2, 4, 12, 3) < antes + 1


def test_modelo_y_tablero(x, tmp_path, monkeypatch):
    ventana = t.ventana_completa(x, "2024-10-01", "2026-06-30")
    v = t.validar(ventana, "2026-01-01", "2026-06-30", "tablero")
    assert v["binarios"]["uci_prolongada"]["auc"] > 0.6  # el SOFA sintético predice la estancia
    assert v["continuos"]["dias_uci"]["mae_mediana"] <= v["continuos"]["dias_uci"]["mae_sin_modelo"]
    modelo = t.ajustar(ventana, "tablero")
    ruta = tmp_path / "m.joblib"
    modelo.guardar(ruta)

    pacientes = leer_tablero(generar_tablero(tmp_path / "tablero.xlsx"))
    pred = t.predecir_paciente(pacientes[0], t.ModeloTrayectoria.cargar(ruta), dias_uci=2)
    assert 0 <= pred.p_alta_directa <= 1
    assert pred.dias_uci[0] <= pred.dias_uci[1] <= pred.dias_uci[2]
    assert pred.dias_uci_restantes >= 0.5

    assert abs(sum(pred.trayectoria.values()) - 1) < 0.01
    assert abs(sum(pred.rangos_uci.values()) - 1) < 0.01 and set(pred.rangos_uci) == set(t.BANDAS["dias_uci"][1])
    assert "bandas" in v and v["bandas"]["dias_uci"]["rps"] > 0
    assert pred.destino in ("sale desde uci", "piso y alta", "vuelve a uci")

    # el JSON para la calculadora web da lo mismo que scikit-learn
    js = t.exportar_json(modelo)
    muestra = ventana.sample(60, random_state=1)
    esperado = modelo.predecir(muestra)
    for idx, fila in muestra.iterrows():
        valores = {c: (None if fila[c] != fila[c] else fila[c]) for c in js["numericas"] + list(js["categoricas"])}
        obtenido = t.predecir_json(js, valores)
        for k, v in obtenido.items():
            assert v == pytest.approx(esperado.loc[idx, k], abs=1e-3), k

    monkeypatch.setenv("UCI_MODELO_TRAYECTORIA", str(ruta))
    ev = evaluar(pacientes[0])
    assert ev.trayectoria is not None
    assert ev.dias_restantes_estimados == ev.trayectoria.dias_uci_restantes


def test_cli(tmp_path):
    archivo = guardar_egresos(tmp_path / "egresos.xlsx", n=700)
    assert main(["modelo-trayectoria", "--egresos", str(archivo), "--salida", str(tmp_path / "out")]) == 0
    inf = json.loads((tmp_path / "out" / "modelo_trayectoria.json").read_text())
    assert set(inf["validacion"]) == {"tablero", "ingreso"}
    assert "trayectoria" in inf["validacion"]["tablero"] and inf["validacion_movil"]
    md = (tmp_path / "out" / "modelo_trayectoria.md").read_text()
    assert "SINT" not in md and "SINTETICO" not in md
    assert (tmp_path / "out" / "modelo_trayectoria_tablero.joblib").exists()
    html = (tmp_path / "out" / "calculadora_trayectoria.html").read_text()
    assert "__MODELO__" not in html and "__EGRESO__" not in html and "SINT" not in html and '"binarios"' in html
