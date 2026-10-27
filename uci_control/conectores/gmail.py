"""Fuentes de correos de entrega de turno.

`CarpetaCorreos` lee archivos .eml exportados (útil para pruebas y para correr sin
credenciales). `GmailAPI` lee directamente del buzón con la API de Gmail, en solo lectura.
Ambas devuelven el texto del cuerpo más el de los adjuntos .txt y .odt.
"""

from __future__ import annotations

import base64
import email
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from email import policy
from io import BytesIO
from pathlib import Path
from typing import Iterable, Protocol
from xml.etree import ElementTree

ASUNTO_ENTREGA = re.compile(r"entrega\s+de\s+turno", re.I)
ADJUNTO_TABLERO = re.compile(r"tablero.*\.xlsx$", re.I)


@dataclass
class CorreoEntrega:
    id: str
    asunto: str
    fecha: str
    texto: str
    adjuntos_sin_leer: list[str]
    tableros: list[tuple[str, bytes]] = field(default_factory=list)  # (nombre, contenido) del tablero ORBIT


class FuenteCorreos(Protocol):
    def entregas(self, fecha: date) -> Iterable[CorreoEntrega]: ...

    def tableros(self, fecha: date) -> list[tuple[str, bytes]]: ...


class CarpetaCorreos:
    def __init__(self, carpeta: str | Path):
        self.carpeta = Path(carpeta)

    def entregas(self, fecha: date) -> Iterable[CorreoEntrega]:
        for ruta in sorted(self.carpeta.glob("*.eml")):
            mensaje = email.message_from_bytes(ruta.read_bytes(), policy=policy.default)
            if not ASUNTO_ENTREGA.search(mensaje.get("Subject", "")):
                continue
            yield _correo_desde_mime(ruta.name, mensaje)

    def tableros(self, fecha: date) -> list[tuple[str, bytes]]:
        """Tableros ORBIT adjuntos en cualquier correo de la carpeta."""
        hallados = []
        for ruta in sorted(self.carpeta.glob("*.eml")):
            mensaje = email.message_from_bytes(ruta.read_bytes(), policy=policy.default)
            hallados += _correo_desde_mime(ruta.name, mensaje).tableros
        return hallados


class GmailAPI:
    """Lectura del buzón con google-api-python-client. Requiere credenciales OAuth con el
    alcance gmail.readonly; no se usa en las pruebas."""

    def __init__(self, servicio, consulta_extra: str = ""):
        self.servicio = servicio
        self.consulta_extra = consulta_extra

    def entregas(self, fecha: date) -> Iterable[CorreoEntrega]:
        desde = fecha.strftime("%Y/%m/%d")
        hasta = (fecha + timedelta(days=2)).strftime("%Y/%m/%d")
        consulta = f'subject:("entrega de turno") after:{desde} before:{hasta} {self.consulta_extra}'.strip()
        usuarios = self.servicio.users()
        respuesta = usuarios.messages().list(userId="me", q=consulta, maxResults=100).execute()
        for ref in respuesta.get("messages", []):
            crudo = usuarios.messages().get(userId="me", id=ref["id"], format="raw").execute()
            mensaje = email.message_from_bytes(base64.urlsafe_b64decode(crudo["raw"]), policy=policy.default)
            yield _correo_desde_mime(ref["id"], mensaje)

    def tableros(self, fecha: date) -> list[tuple[str, bytes]]:
        """Tableros ORBIT ("Reporte_TableroControlUCI1<fecha>.xlsx") adjuntos en el buzón ese día."""
        desde = fecha.strftime("%Y/%m/%d")
        hasta = (fecha + timedelta(days=2)).strftime("%Y/%m/%d")
        consulta = f"has:attachment filename:xlsx tablero after:{desde} before:{hasta}"
        usuarios = self.servicio.users()
        respuesta = usuarios.messages().list(userId="me", q=consulta, maxResults=50).execute()
        hallados = []
        for ref in respuesta.get("messages", []):
            crudo = usuarios.messages().get(userId="me", id=ref["id"], format="raw").execute()
            mensaje = email.message_from_bytes(base64.urlsafe_b64decode(crudo["raw"]), policy=policy.default)
            hallados += _correo_desde_mime(ref["id"], mensaje).tableros
        return hallados

    def enviar(self, destinatarios: list[str], asunto: str, html: str, adjuntos: dict[str, bytes] | None = None) -> str:
        """Envía el informe. Requiere además el alcance gmail.send."""
        from email.message import EmailMessage

        m = EmailMessage()
        m["To"] = ", ".join(destinatarios)
        m["Subject"] = asunto
        m.set_content("Este correo tiene formato HTML. Abra el adjunto para ver el informe completo.")
        m.add_alternative(html, subtype="html")
        for nombre, datos in (adjuntos or {}).items():
            m.add_attachment(datos, maintype="text", subtype="html", filename=nombre)
        crudo = base64.urlsafe_b64encode(bytes(m)).decode()
        return self.servicio.users().messages().send(userId="me", body={"raw": crudo}).execute()["id"]


def _correo_desde_mime(identificador: str, mensaje) -> CorreoEntrega:
    partes, sin_leer, tableros = [], [], []
    cuerpo = mensaje.get_body(preferencelist=("plain",))
    if cuerpo is not None:
        partes.append(cuerpo.get_content())
    for adjunto in mensaje.iter_attachments():
        nombre = adjunto.get_filename() or "adjunto"
        datos = adjunto.get_payload(decode=True) or b""
        if ADJUNTO_TABLERO.search(nombre):
            tableros.append((nombre, datos))
            continue
        texto = texto_de_adjunto(nombre, datos)
        if texto is None:
            sin_leer.append(nombre)
        else:
            partes.append(texto)
    return CorreoEntrega(
        id=identificador,
        asunto=mensaje.get("Subject", ""),
        fecha=mensaje.get("Date", ""),
        texto="\n\n".join(partes),
        adjuntos_sin_leer=sin_leer,
        tableros=tableros,
    )


def texto_de_adjunto(nombre: str, datos: bytes) -> str | None:
    """Texto plano de un adjunto .txt u .odt. Devuelve None para formatos aún no soportados (.doc)."""
    extension = Path(nombre).suffix.lower()
    if extension == ".txt":
        return datos.decode("utf-8", errors="replace")
    if extension == ".odt":
        return _texto_odt(datos)
    return None


def _texto_odt(datos: bytes) -> str:
    ns = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
    tabla = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
    with zipfile.ZipFile(BytesIO(datos)) as z:
        raiz = ElementTree.fromstring(z.read("content.xml"))
    lineas = []

    def recorrer(nodo):
        if nodo.tag == f"{tabla}table-row":
            celdas = ["".join(c.itertext()).strip() for c in nodo if c.tag == f"{tabla}table-cell"]
            lineas.append(" | ".join(celdas))
            return
        if nodo.tag in (f"{ns}p", f"{ns}h"):
            lineas.append("".join(nodo.itertext()))
            return
        for hijo in nodo:
            recorrer(hijo)

    recorrer(raiz)
    return "\n".join(lineas)
