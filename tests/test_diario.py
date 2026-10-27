import csv
import json
from datetime import date

import pytest

from datos_prueba.generar import generar
from datos_prueba.tablero import generar_correo_tablero
from uci_control.__main__ import main
from uci_control.conectores import CarpetaCorreos
from uci_control.conectores.orbit_tablero import censo_desde_tablero
from uci_control.diario import correr_dia, leer_tableros
from uci_control.prediccion.sugerencias import Sugerencia
from uci_control.seguimiento import BaseSeguimiento, codigo

FECHA = date(2026, 10, 26)


@pytest.fixture
def buzon(tmp_path):
    generar(tmp_path)
    generar_correo_tablero(tmp_path / "correos")
    return tmp_path


def test_tablero_desde_correo(buzon):
    adjuntos = CarpetaCorreos(buzon / "correos").tableros(FECHA)
    assert len(adjuntos) == 1 and adjuntos[0][0].startswith("Reporte_TableroControlUCI1")
    pacientes = leer_tableros(adjuntos + adjuntos)  # el mismo tablero dos veces no duplica camas
    assert [p.cama for p in pacientes] == ["U63", "U73", "U74", "U75"]
    censo = {r.cama: r for r in censo_desde_tablero(pacientes)}
    assert censo["U63"].vmi and censo["U63"].vasopresor and censo["U63"].sofa == "40-50%"
    assert censo["U74"].criterio.value == "PISO"


def test_corrida_diaria_y_seguimiento(buzon, tmp_path):
    base = tmp_path / "seg.sqlite"
    salida = tmp_path / "salida"
    r = correr_dia(FECHA, CarpetaCorreos(buzon / "correos"), salida, base,
                   censo_csv=buzon / "orbit_censo.csv", costos_csv=buzon / "cargos.csv")
    assert r.camas_tablero == 4 and r.entregas == 2 and r.sugerencias > 10 and not r.avisos
    datos = json.loads(r.archivos["json"].read_text(encoding="utf-8"))
    s = datos["riesgo"][0]["sugerencias"][0]
    assert s["seguimiento"]["dias_abierta"] == 0
    html = r.archivos["html"].read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>") and "Seguimiento" in html and "SINT-" not in html
    assert "Sugerencias para hoy" in r.html_correo and "<script" not in r.html_correo

    # Al día siguiente la misma sugerencia sigue abierta un día; una marcada como hecha no vuelve a pendiente.
    with open(r.archivos["pendientes"], encoding="utf-8-sig") as f:
        filas = list(csv.DictReader(f, delimiter=";"))
    hecha = filas[0]["id"]
    seg = BaseSeguimiento(base)
    assert seg.marcar(hecha, "hecho", FECHA)
    seg.cerrar()
    r2 = correr_dia(date(2026, 10, 27), CarpetaCorreos(buzon / "correos"), salida, base,
                    censo_csv=buzon / "orbit_censo.csv", costos_csv=buzon / "cargos.csv")
    sugerencias = [x for p in json.loads(r2.archivos["json"].read_text(encoding="utf-8"))["riesgo"] for x in p["sugerencias"]]
    estados = {x["seguimiento"]["id"]: x["seguimiento"] for x in sugerencias}
    assert estados[hecha]["estado"] == "hecho"
    assert any(x["dias_abierta"] == 1 and x["estado"] == "pendiente" for x in estados.values())
    adherencia = json.loads(r2.archivos["json"].read_text(encoding="utf-8"))["adherencia"]
    assert sum(d["hecho"] for d in adherencia) == 1


def test_seguimiento_resuelve_y_reabre(tmp_path):
    seg = BaseSeguimiento(tmp_path / "s.sqlite")
    a = Sugerencia("U1", "proa", "PROA", "Revisar meropenem", "Día 7", "hoy")
    b = Sugerencia("U1", "flujo", "Gestor de camas", "Traslado", "Piso", "hoy")
    seg.registrar(date(2026, 10, 1), [("p1", a), ("p1", b)])
    seg.registrar(date(2026, 10, 2), [("p1", a)])  # b desaparece con el paciente presente: resuelta
    estado = dict(seg.con.execute("SELECT id, estado FROM sugerencias").fetchall())
    assert estado[codigo("p1", "Gestor de camas", "Traslado")] == "resuelta"
    r = seg.registrar(date(2026, 10, 3), [("p1", a), ("p1", b)])  # reaparece: pendiente otra vez
    assert r[("U1", "Gestor de camas", "Traslado")].estado == "pendiente"
    assert r[("U1", "PROA", "Revisar meropenem")].dias_abierta == 2
    with pytest.raises(ValueError):
        seg.marcar("X", "listo", date(2026, 10, 3))
    seg.cerrar()


def test_cli_diario_y_seguimiento(buzon, tmp_path):
    base = tmp_path / "seg.sqlite"
    assert main(["diario", "--fecha", "2026-10-26", "--correos", str(buzon / "correos"), "--orbit", str(buzon / "orbit_censo.csv"),
                 "--seguimiento", str(base), "--salida", str(tmp_path / "out")]) == 0
    pendientes = tmp_path / "out" / "pendientes_2026-10-26.csv"
    texto = pendientes.read_text(encoding="utf-8-sig").splitlines()
    texto[1] = ";".join(texto[1].split(";")[:-2] + ["hecho", "listo"])
    pendientes.write_text("\n".join(texto), encoding="utf-8-sig")
    assert main(["seguimiento", "--base", str(base), "importar", str(pendientes)]) == 0
    assert main(["seguimiento", "--base", str(base), "marcar", "NOEXISTE", "hecho"]) == 1
    assert main(["seguimiento", "--base", str(base), "resumen"]) == 0
