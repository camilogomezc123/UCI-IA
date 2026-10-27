from datetime import date

import pytest

from datos_prueba.tablero import generar_tablero
from uci_control.__main__ import main
from uci_control.conectores import leer_tablero
from uci_control.conectores.orbit_tablero import leer_antimicrobianos, sofa_a_mortalidad, tiempo_a_dias
from uci_control.prediccion import erlang_b, escenarios_capacidad, evaluar, holt, sugerir


@pytest.fixture(scope="module")
def tablero(tmp_path_factory):
    ruta = generar_tablero(tmp_path_factory.mktemp("orbit") / "tablero.xlsx")
    return {p.cama: p for p in leer_tablero(ruta)}


def acciones(p):
    return {(s.servicio, s.accion) for s in sugerir(p)}


def test_lectura_tablero(tablero):
    p = tablero["U63"]
    assert p.fecha == date(2026, 10, 27)
    assert p.vmi and p.vasoactivo and p.dependiente
    assert p.sofa_mortalidad == pytest.approx(0.45)
    assert p.dias_vmi() == 17
    assert {a.nombre for a in p.antimicrobianos} == {"Meropenem", "Vancomicina"}  # la ceftriaxona es previa
    assert p.seudonimo and "SINT" not in p.seudonimo
    assert not tablero["U74"].dependiente  # "INDEPENDIENTE" no cuenta


def test_utilidades():
    assert sofa_a_mortalidad("32-35%") == pytest.approx(0.335)
    assert tiempo_a_dias("18dias 12hrs 0min") == 18.5
    ab = leer_antimicrobianos("MEROPENEM SUSPENDIDO\nLINEZOLID INICIO 20/10", date(2026, 10, 27))
    assert [a.nombre for a in ab] == ["Linezolid"] and ab[0].fecha_inicio == date(2026, 10, 20)


def test_riesgo(tablero):
    ev = evaluar(tablero["U63"])
    assert ev.mortalidad.nivel == "alto" and ev.deterioro.nivel == "alto" and ev.infeccion.nivel == "alto"
    assert ev.agrupador == "INFECCIOSO"
    assert evaluar(tablero["U74"]).egreso_proximo.nivel == "alto"
    assert any("no es coherente" in a for a in evaluar(tablero["U75"]).alertas)


def test_sugerencias(tablero):
    a1 = acciones(tablero["U63"])
    assert ("Cirugía de cabeza y cuello / cirugía general", "Valorar traqueostomía") in a1
    assert ("Infectología / PROA", "Revisión conjunta del esquema antimicrobiano") in a1
    assert ("Trabajo social", "Planear egreso con red de apoyo y cuidador") in a1
    assert ("Imágenes", "Destrabar pendiente") in a1
    assert ("Gestor de camas", "Gestionar traslado a hospitalización hoy") in acciones(tablero["U74"])
    a3 = acciones(tablero["U73"])
    assert ("Fonoaudiología", "Valoración de deglución para iniciar vía oral") in a3
    assert ("Autorizaciones / cartera", "Verificar garantía de pago y autorización vigente") in a3
    assert ("Cuidado paliativo", "Conferencia familiar y definición de metas de cuidado") in a3  # oncológico, SOFA 30-35%
    assert ("Médico UCI", "Documentar cultivos y su resultado en ORBIT") in a3
    assert ("Médico UCI", "Corregir el criterio de atención en ORBIT") in acciones(tablero["U75"])
    assert not any(s.servicio == "Trabajo social" for s in sugerir(tablero["U74"]))


def test_erlang_y_escenarios():
    assert erlang_b(1, 1.0) == pytest.approx(0.5)
    assert erlang_b(10, 5.0) == pytest.approx(0.01838, abs=1e-4)
    esc = escenarios_capacidad(66, 14.0, 5.0, camas_bloqueadas=8, reduccion_estancia=0.5)
    assert esc[0].bloqueo > esc[1].bloqueo > esc[3].bloqueo
    assert esc[3].admisiones_dia > esc[0].admisiones_dia


def test_holt():
    p = holt("lineal", [10, 12, 14, 16, 18, 20], horizonte=2)
    assert p.valores[0] == pytest.approx(22, abs=0.6)
    assert p.inferior[0] <= p.valores[0] <= p.superior[0]


def test_cli_riesgo(tmp_path):
    ruta = generar_tablero(tmp_path / "tablero.xlsx")
    assert main(["riesgo", "--tablero", str(ruta), "--salida", str(tmp_path / "salida")]) == 0
    md = (tmp_path / "salida" / "riesgo_2026-10-27.md").read_text(encoding="utf-8")
    assert "Valorar traqueostomía" in md and "SINT-" not in md and "PACIENTE SINTETICO" not in md


def test_cli_informe_con_tablero(tmp_path):
    from datos_prueba.generar import generar

    generar(tmp_path)
    ruta = generar_tablero(tmp_path / "tablero.xlsx")
    salida = tmp_path / "salida"
    assert main(["informe", "--fecha", "2026-10-26", "--orbit", str(tmp_path / "orbit_censo.csv"),
                 "--correos", str(tmp_path / "correos"), "--costos", str(tmp_path / "cargos.csv"),
                 "--tablero", str(ruta), "--salida", str(salida), "--sin-ia"]) == 0
    md = (salida / "informe_2026-10-26.md").read_text(encoding="utf-8")
    assert "## Riesgo y gestión por paciente" in md and (salida / "riesgo_2026-10-26.json").exists()
