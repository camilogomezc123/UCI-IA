import json
from types import SimpleNamespace

from uci_control.conciliacion import conciliar
from uci_control.indicadores import calcular
from uci_control.informe import a_json, a_markdown
from uci_control.orquestador import Orquestador, carga_para_claude

from .conftest import FECHA


class ClienteFalso:
    """Imita client.beta.messages.create y guarda la solicitud."""

    def __init__(self, respuesta: dict | Exception, stop_reason="end_turn"):
        self.respuesta = respuesta
        self.stop_reason = stop_reason
        self.solicitudes = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._crear))

    def _crear(self, **kwargs):
        self.solicitudes.append(kwargs)
        if isinstance(self.respuesta, Exception):
            raise self.respuesta
        bloque = SimpleNamespace(type="text", text=json.dumps(self.respuesta))
        return SimpleNamespace(content=[bloque], stop_reason=self.stop_reason)


def _respuesta(prioridades):
    return {
        "resumen_ejecutivo": "Resumen de prueba.",
        "prioridades": prioridades,
        "alertas": [{"nivel": "critica", "mensaje": "Verificar identidad en U65.", "camas": ["U65"]}],
        "verificaciones_datos": ["Registrar SOFA como puntaje."],
    }


def test_la_carga_no_lleva_identificadores(dia):
    resultado = conciliar(FECHA, *dia)
    texto = json.dumps(carga_para_claude(resultado, calcular(resultado)), ensure_ascii=False)
    for dato in ("EP-9000", "3109876543", "12345678", "Martha", "Pedro", "XXXXXXXX"):
        assert dato not in texto, dato


def test_orquestador_con_claude_verifica_la_respuesta(dia):
    resultado = conciliar(FECHA, *dia)
    id_u65 = next(d.id for d in resultado.discrepancias if d.cama == "U65")
    cliente = ClienteFalso(_respuesta([
        {"orden": 1, "cama": "U65", "accion": "Verificar identidad", "responsable": "enfermeria",
         "plazo": "inmediato", "motivo": "Episodio distinto", "discrepancias": [id_u65, "D999"]},
        {"orden": 2, "cama": "U99", "accion": "Inventada", "responsable": "coordinacion",
         "plazo": "hoy", "motivo": "No existe", "discrepancias": [id_u65]},
    ]))
    informe = Orquestador(cliente=cliente).ejecutar(FECHA, *dia)

    solicitud = cliente.solicitudes[0]
    assert solicitud["model"] == "claude-opus-5-5"
    assert solicitud["fallbacks"] == "default"
    assert solicitud["output_config"]["format"]["type"] == "json_schema"

    assert informe.generado_con_ia
    assert [p["cama"] for p in informe.prioridades] == ["U65"]
    assert informe.prioridades[0]["discrepancias"] == [id_u65]
    obs = " ".join(informe.observaciones_verificacion)
    assert "U99" in obs and "D999" in obs
    assert "no quedó en ninguna prioridad" in obs  # las altas sin prioridad se reportan


def test_si_claude_falla_el_informe_sale_por_reglas(dia):
    informe = Orquestador(cliente=ClienteFalso(RuntimeError("sin red"))).ejecutar(FECHA, *dia)
    assert not informe.generado_con_ia
    assert informe.prioridades[0]["cama"] in {"U65", "UNIDAD"}  # lo crítico va primero
    assert "No se pudo consultar a Claude" in informe.observaciones_verificacion[0]


def test_informe_markdown_y_json(dia):
    informe = Orquestador(usar_ia=False).ejecutar(FECHA, *dia)
    md = a_markdown(informe)
    assert "## Prioridades del día" in md and "U75" in md
    assert "XXXXXXXX" not in md and "3109876543" not in md
    datos = json.loads(a_json(informe))
    assert datos["indicadores"]["camas_ocupadas"] == 18
