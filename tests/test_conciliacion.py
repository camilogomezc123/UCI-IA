from collections import defaultdict

from uci_control import config
from uci_control.conciliacion import conciliar
from uci_control.indicadores import calcular

from .conftest import FECHA

ESPERADAS = {
    "U64": {"R11"},
    "U65": {"R06"},
    "U66": {"R02", "R14", "R16"},
    "U67": {"R04", "R15"},
    "U68": {"R12"},
    "U69": {"R13"},
    "U70": {"R11"},
    "U71": {"R09"},
    "U72": {"R08", "R09"},
    "U74": {"R14"},
    "U75": {"R01", "R02", "R03", "R04", "R08", "R16"},
    "U76": {"R10"},
    "U77": {"R15"},
    "U78": {"R07"},
    "U79": {"R05"},
    "U80": {"R05"},
    "U82": {"R03"},
}


def test_cada_cama_dispara_sus_reglas(dia):
    resultado = conciliar(FECHA, *dia)
    por_cama = defaultdict(set)
    for d in resultado.discrepancias:
        if d.cama:
            por_cama[d.cama].add(d.regla)
    for cama, reglas in ESPERADAS.items():
        assert por_cama[cama] == reglas, cama
    # Camas limpias: sin discrepancias.
    for cama in ("U63", "U73", "U81"):
        assert not por_cama.get(cama), cama


def test_credencial_en_correo_es_alerta_critica(dia):
    resultado = conciliar(FECHA, *dia)
    [alerta] = [d for d in resultado.alertas_globales if d.regla == "R17"]
    assert alerta.severidad.value == "critica"
    assert "XXXXXXXX" not in alerta.detalle


def test_ids_ordenados_por_severidad(dia):
    resultado = conciliar(FECHA, *dia)
    pesos = [d.severidad.peso for d in resultado.discrepancias]
    assert pesos == sorted(pesos, reverse=True)
    assert resultado.discrepancias[0].id == "D001"


def test_indicadores(dia):
    ind = calcular(conciliar(FECHA, *dia))
    assert ind["camas_ocupadas"] == 18
    assert ind["condicion"]["PISO"] == 3
    assert ind["financiero"]["derecho_cama_sin_cargar_cop"] == config.TARIFA_CAMA_UCI
    assert ind["financiero"]["costo_estancia_evitable_dia_cop"] == 2 * config.COSTO_DIA_UCI
