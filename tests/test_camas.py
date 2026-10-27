from datetime import date, timedelta

import pytest

from datos_prueba.tablero import generar_tablero
from uci_control.__main__ import main
from uci_control.camas import Historial, barreras, evaluar_alta, gestionar_camas, swift
from uci_control.camas.capacidad import nivel_escalamiento
from uci_control.conectores import leer_tablero


@pytest.fixture(scope="module")
def pacientes(tmp_path_factory):
    return {p.cama: p for p in leer_tablero(generar_tablero(tmp_path_factory.mktemp("t") / "t.xlsx"))}


def test_swift():
    # Gajic 2008: origen piso 8, estancia >10 d 14, PaFi 120 10, Glasgow 13 6, PaCO2 50 5.
    s = swift(12, True, 120, 13, 50)
    assert s.puntaje == 43 and s.completo and s.alto
    parcial = swift(1)
    assert parcial.puntaje == 0 and not parcial.completo and "Glasgow" in parcial.faltantes


def test_criterios_de_alta(pacientes):
    assert evaluar_alta(pacientes["U63"]).estado == "no"  # VMI y vasopresor
    listo = evaluar_alta(pacientes["U74"])
    assert listo.estado == "listo" and listo.destino == "hospitalización" and listo.fecha_esperada_egreso == date(2026, 10, 27)
    casi = evaluar_alta(pacientes["U73"])  # EVA 6: dolor no controlado
    assert casi.estado == "casi" and casi.destino == "intermedio" and any("Dolor" in c for c in casi.criterios_faltantes)
    assert evaluar_alta(pacientes["U75"]).estado == "no"  # criterio de piso pero con VMI


def test_barreras(pacientes):
    assert [b.codigo for b in barreras(pacientes["U73"])] == ["B05"]  # valoración por nutrición
    assert [b.codigo for b in barreras(pacientes["U74"])] == ["B00"]


def test_nivel_escalamiento():
    assert nivel_escalamiento(80, 2, 0) == 1
    assert nivel_escalamiento(90, 1, 2) == 2
    assert nivel_escalamiento(97, 1, 2) == 3
    assert nivel_escalamiento(100, -4, 20) == 4


def test_historial_y_plan(pacientes, tmp_path):
    h = tmp_path / "h.sqlite"
    hoy = date(2026, 10, 27)
    lista = list(pacientes.values())
    p1 = gestionar_camas(hoy - timedelta(days=1), lista, h, camas_totales=10, demanda_esperada=3)
    assert p1.ocupadas == 4 and p1.libres == 6 and p1.listos == 1
    assert p1.pacientes[0].cama == "U74"  # el de mayor probabilidad de egreso va primero
    # Día siguiente: U74 sigue (un día rojo) y U73 salió.
    sin_u73 = [p for p in lista if p.cama != "U73"]
    p2 = gestionar_camas(hoy, sin_u73, h, camas_totales=10, demanda_esperada=3)
    u74 = next(p for p in p2.pacientes if p.cama == "U74")
    assert u74.dias_listo_sin_salir == 1 and any("sin salir" in a for a in u74.acciones)
    assert p2.flujo["egresos_desde_el_tablero_anterior"] == 1
    assert p2.flujo["dias_cama_rojos"] == 1 and p2.flujo["listos_que_salieron_al_dia_siguiente_pct"] == 0.0
    hist = Historial(h)
    assert [e["cama"] for e in hist.egresos(hoy)] == ["U73"]
    hist.cerrar()


def test_brecha_negativa_escala(pacientes, tmp_path):
    plan = gestionar_camas(date(2026, 10, 27), list(pacientes.values()), tmp_path / "h.sqlite", camas_totales=4, demanda_esperada=5)
    assert plan.libres == 0 and plan.brecha < 0 and plan.nivel >= 3 and plan.ingresos_perdidos_estimados > 0


def test_cli_camas(tmp_path):
    t = generar_tablero(tmp_path / "t.xlsx")
    assert main(["camas", "--tablero", str(t), "--historial", str(tmp_path / "h.sqlite"), "--camas-totales", "12",
                 "--salida", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "camas_2026-10-27.json").exists()
