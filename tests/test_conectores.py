import zipfile
from datetime import date
from io import BytesIO

from uci_control.conectores.comun import normalizar_cama
from uci_control.conectores.entregas import parsear_entrega
from uci_control.conectores.gmail import texto_de_adjunto
from uci_control.fechas import leer_fecha
from uci_control.modelos import Condicion
from uci_control.privacidad import contiene_secreto, limpiar_texto

REF = date(2026, 10, 26)


def test_fechas_en_varios_formatos():
    assert leer_fecha("26/10/2026", REF)[0] == REF
    assert leer_fecha("26-10-26", REF)[0] == REF
    assert leer_fecha("2026-10-26", REF)[0] == REF
    assert leer_fecha("26 oct", REF)[0] == REF
    assert leer_fecha("05/09", REF)[0] == date(2026, 9, 5)


def test_fecha_mal_digitada_se_descarta_con_advertencia():
    fecha, aviso = leer_fecha("26-10-36", REF)
    assert fecha is None and "digitación" in aviso
    assert leer_fecha("23-10-23", REF)[0] is None


def test_normalizar_cama():
    assert normalizar_cama("cama 075") == "U75"
    assert normalizar_cama("U-75") == "U75"
    assert normalizar_cama("75") == "U75"


def test_privacidad_retira_datos_personales():
    texto = limpiar_texto("Sra. Martha Lopez, CC 12345678, celular 3109876543, correo a@b.co. Ingreso 2026-10-26.")
    for dato in ("Martha", "12345678", "3109876543", "a@b.co"):
        assert dato not in texto
    assert "2026-10-26" in texto
    assert contiene_secreto("Contraseña equipo: abc")
    assert contiene_secreto("Clave GLPI: abc")
    assert not contiene_secreto("Punto clave: vigilar la hipotensión durante la noche por sedación")


ENTREGA = """Estación: UCI Prueba
Contraseña correo: secreto123
Fecha: 26/10/2026
Turno: Noche

CAMA: 7
Episodio: EP-1
Condición: Piso
Ventilación: VMNI
Vasoactivos: Ninguno
Dispositivos: CVC desde 10/10/2026; Sonda vesical 20/10/2026 (justificación: retención)
Pendientes:
- [autorización] Resonancia (solicitado 24/10/2026)
- Resultado de patología
Nota: Se suspendió norepinefrina en la tarde.
Inicia dobutamina a las 03:00.
"""


def test_parser_de_entrega():
    est = parsear_entrega(ENTREGA, "prueba", REF)
    assert est.estacion == "UCI Prueba" and est.fecha == REF
    assert len(est.alertas) == 1 and "secreto123" not in est.alertas[0]
    cama = est.camas[0]
    assert cama.cama == "U7"
    assert cama.condicion == Condicion.PISO
    assert cama.vmi is False  # VMNI no es invasiva
    assert [d.tipo for d in cama.dispositivos] == ["CVC", "Sonda vesical"]
    assert cama.dispositivos[0].fecha_insercion == date(2026, 10, 10)
    assert cama.dispositivos[1].justificacion == "retención"
    assert cama.pendientes[0].tipo == "autorización" and cama.pendientes[0].fecha_solicitud == date(2026, 10, 24)
    assert cama.pendientes[1].tipo == "otro"
    # La norepinefrina suspendida no cuenta; la dobutamina sí y no está en el campo estructurado.
    assert any("dobutamina" in a for a in cama.advertencias)
    assert not any("norepinefrina" in a for a in cama.advertencias)


def test_texto_de_adjunto_odt():
    contenido = (
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"><office:body><office:text>'
        "<text:p>CAMA: U01</text:p><table:table><table:table-row>"
        "<table:table-cell><text:p>CVC</text:p></table:table-cell>"
        "<table:table-cell><text:p>Sí</text:p></table:table-cell>"
        "</table:table-row></table:table></office:text></office:body></office:document-content>"
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("content.xml", contenido)
    assert texto_de_adjunto("entrega.odt", buffer.getvalue()) == "CAMA: U01\nCVC | Sí"
    assert texto_de_adjunto("entrega.doc", b"") is None


def test_lectura_de_los_datos_de_prueba(dia):
    censo, entregas, cargos = dia
    assert len(censo) == 20
    assert {e.estacion for e in entregas} == {"UCI Torre B", "UCI Torre C"}  # el correo de la cafetería se ignora
    assert sum(len(e.camas) for e in entregas) == 18
    assert any(c.cama == "U69" for c in cargos)
