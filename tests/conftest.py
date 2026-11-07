from datetime import date

import pytest

from datos_prueba.generar import generar
from uci_control.conectores import CarpetaCorreos, leer_cargos, leer_censo_orbit, parsear_entrega

FECHA = date(2026, 10, 26)


@pytest.fixture(scope="session")
def dia(tmp_path_factory):
    carpeta = tmp_path_factory.mktemp("dia")
    generar(carpeta)
    censo = leer_censo_orbit(carpeta / "orbit_censo.csv", FECHA)
    entregas = [parsear_entrega(c.texto, c.id, FECHA) for c in CarpetaCorreos(carpeta / "correos").entregas(FECHA)]
    cargos = leer_cargos(carpeta / "cargos.csv", FECHA)
    return censo, entregas, cargos
