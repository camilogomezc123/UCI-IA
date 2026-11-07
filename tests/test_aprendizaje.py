from datetime import date, timedelta

import pandas as pd
import pytest

pytest.importorskip("sklearn")

from datos_prueba.historial import escribir_correos, simular
from uci_control.__main__ import main
from uci_control.camas import aprendizaje as a
from uci_control.camas import gestionar_camas
from uci_control.conectores.orbit_tablero import seudonimo

INICIO = date(2026, 3, 2)


def _registrar(dias, ruta):
    est = a.EstadosDiarios(ruta)
    for d in dias:
        est.registrar(d.fecha, d.tablero, [c for cs in d.entregas.values() for c in cs])
    return est


def _verdad(dias) -> dict:
    """(fecha, seudónimo) -> 1 si salió ese día, para todos los presentes (haya o no tablero)."""
    return {(pd.Timestamp(d.fecha), seudonimo(doc)): int(seudonimo(doc) in d.egresan) for d in dias for doc in d.documentos.values()}


@pytest.fixture(scope="module")
def entrenado(tmp_path_factory):
    """160 días simulados guardados y un modelo entrenado junto al historial."""
    ruta = tmp_path_factory.mktemp("h") / "historial_camas.sqlite"
    dias = simular(161)
    _registrar(dias[:-1], ruta).cerrar()
    mensaje = a.actualizar(ruta, dias[-2].fecha, forzar=True)
    return ruta, dias, mensaje


def test_fecha_de_estado():
    assert a.fecha_de_estado("Mon, 09 Nov 2026 07:05:00 -0500") == date(2026, 11, 9)  # entrega de la noche
    assert a.fecha_de_estado("Mon, 09 Nov 2026 19:10:00 -0500") == date(2026, 11, 10)  # entrega del día
    assert a.fecha_de_estado("sin fecha", date(2026, 11, 1)) == date(2026, 11, 1)
    assert a.fecha_de_estado(None) is None


def test_etiquetas_iguales_a_la_verdad_y_cobertura(tmp_path):
    hueco = INICIO + timedelta(days=10)  # ese día no llegó nada de la torre C (ni tablero)
    dias = simular(25, sin_tablero={hueco}, sin_entrega={(hueco, "UCI Torre C")})
    x = _registrar(dias, tmp_path / "h.sqlite").tabla()
    verdad = _verdad(dias)
    x["verdad"] = [verdad.get((f, p)) for f, p in zip(x["fecha"], x["paciente"])]
    et = x[x["sale"].notna()]
    assert len(et) > 400 and (et["sale"] == et["verdad"]).all()
    vispera = x[x["fecha"] == pd.Timestamp(hueco - timedelta(days=1))]
    assert vispera[vispera["estacion"] == "UCI TORRE C"]["sale"].isna().all()  # sin datos al día siguiente: sin etiqueta
    assert vispera[vispera["estacion"] == "UCI TORRE B"]["sale"].notna().all()
    assert x[x["fecha"] == x["fecha"].max()]["sale"].isna().all()  # el último día aún no tiene desenlace
    assert not x["paciente"].str.startswith("cama:").any()
    columnas = " ".join(x.columns)
    assert "documento" not in columnas and "episodio" not in columnas


def test_mismo_paciente_entre_entregas_y_tablero(tmp_path):
    """Solo entregas el día 1 y 3, tablero y entregas el día 2: el ingreso enlaza al paciente."""
    dias = simular(3, semilla=9)
    est = a.EstadosDiarios(tmp_path / "h.sqlite")
    for k, d in enumerate(dias):
        est.registrar(d.fecha, d.tablero if k == 1 else [], [c for cs in d.entregas.values() for c in cs])
    x = est.tabla()
    sigue = x.groupby("paciente")["fecha"].nunique()
    presentes_3_dias = {p.seudonimo for p in dias[0].tablero} - dias[0].egresan - dias[1].egresan
    assert {p for p, n in sigue.items() if n == 3} >= presentes_3_dias
    assert x[x["fecha"] == pd.Timestamp(dias[0].fecha)]["sale"].sum() == len(dias[0].egresan)


def test_variables_derivadas(tmp_path):
    x = _registrar(simular(40), tmp_path / "h.sqlite").tabla().sort_values(["paciente", "fecha"])
    previo = x.groupby("paciente").shift()
    retiro = x[x["retiro_soporte"] == 1]
    assert len(retiro) > 0
    assert ((previo.loc[retiro.index, "vmi_total"] + previo.loc[retiro.index, "vaso_total"]) > 0).all()
    assert ((retiro["vmi_total"] + retiro["vaso_total"]) == 0).all()
    assert (x.loc[(x["vmi_total"] + x["vaso_total"]) > 0, "dias_sin_soporte"] == 0).all()
    assert x["dias_en_uci"].min() == 0 and (x.groupby("paciente")["dias_en_uci"].diff().dropna() >= 1).all()
    assert set(x["b01"].unique()) <= {0.0, 1.0} and x["b01"].sum() > 0  # espera de cama, de los pendientes


def test_modelo_aprende_y_supera_referencias(entrenado):
    ruta, _, mensaje = entrenado
    modelo = a.modelo_configurado(ruta)
    v = modelo.metricas["validacion"]
    assert "reentrenado" in mensaje and modelo.supera_referencias
    assert v["modelo"]["auc"] > v["criterios_de_alta"]["auc"] + 0.03
    assert v["modelo"]["auc"] > v["dias_y_dia_semana"]["auc"] + 0.08
    assert ruta.with_name("modelo_egreso_estado.md").read_text(encoding="utf-8").startswith("# Modelo del estado diario")
    # Reentrena solo cada semana.
    assert "próximo reentrenamiento" in a.actualizar(ruta, date.fromisoformat(modelo.metricas["fecha_entrenamiento"]) + timedelta(days=3))


def test_pocos_datos(tmp_path):
    _registrar(simular(5), tmp_path / "h.sqlite").cerrar()
    assert "faltan" in a.actualizar(tmp_path / "h.sqlite", INICIO + timedelta(days=5))
    assert not (tmp_path / a.NOMBRE_MODELO).exists()


def test_plan_de_camas_usa_el_modelo_aprendido(entrenado, monkeypatch):
    monkeypatch.delenv("UCI_MODELO_EGRESO_ESTADO", raising=False)
    monkeypatch.delenv("UCI_MODELO_OCUPACION", raising=False)
    ruta, dias, _ = entrenado
    hoy = dias[-1]
    plan = gestionar_camas(hoy.fecha, hoy.tablero, ruta, camas_totales=24, demanda_esperada=2,
                           entregas=[c for cs in hoy.entregas.values() for c in cs])
    assert plan.fuente_egresos == "modelo aprendido del estado diario"
    assert all(p.probabilidad_egreso_aprendida is not None for p in plan.pacientes)
    assert plan.egresos_esperados == pytest.approx(sum(p.probabilidad_egreso_aprendida for p in plan.pacientes), abs=0.1)
    assert plan.egresos_esperados_reglas is not None and plan.egresos_esperados_aprendido == plan.egresos_esperados


def test_carga_del_historico_desde_correos(tmp_path):
    dias = simular(32, sin_tablero={INICIO + timedelta(days=k) for k in range(0, 32, 3)})
    escribir_correos(dias, tmp_path / "correos")
    h = tmp_path / "historial_camas.sqlite"
    assert main(["historial-entregas", "--correos", str(tmp_path / "correos"), "--desde", str(INICIO),
                 "--hasta", str(dias[-1].fecha), "--historial", str(h)]) == 0
    est = a.EstadosDiarios(h)
    x = est.tabla()
    est.cerrar()
    verdad = _verdad(dias)
    x["verdad"] = [verdad.get((f, p)) for f, p in zip(x["fecha"], x["paciente"])]
    # Quien estuvo solo en días sin tablero queda con la clave del ingreso (no hay documento para
    # enlazarlo); su etiqueta es igual de válida, pero no se puede cruzar con la verdad por documento.
    sin_enlace = x["verdad"].isna()
    assert (x.loc[sin_enlace, "fuente"] == "entrega").all() and sin_enlace.mean() < 0.1
    et = x[x["sale"].notna() & ~sin_enlace]
    assert x["fecha"].nunique() == 32 and len(et) > 500 and (et["sale"] == et["verdad"]).all()
    assert set(x["fuente"]) == {"ambos", "entrega"}
    assert main(["modelo-estado", "--historial", str(h)]) == 0
    assert (tmp_path / a.NOMBRE_MODELO).exists()
