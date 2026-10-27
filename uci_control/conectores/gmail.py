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
from dataclasses import dataclass
from datetime import date, timedelta
from email import policy
from io import BytesIO
from pathlib import Path
from typing import Iterable, Protocol
from xml.etree import ElementTree

ASUNTO_ENTREGA = re.compile(r"entrega\s+de\s+turno", re.I)


@dataclass
class CorreoEntrega:
    id: str
    asunto: str
    fecha: str
    texto: str
    adjuntos_sin_leer: list[str]


class FuenteCorreos(Protocol):
    def entregas(self, fecha: date) -> Iterable[CorreoEntrega]: ...


class CarpetaCorreos:
    def __init__(self, carpeta: str | Path):
        self.carpeta = Path(carpeta)

    def entregas(self, fecha: date) -> Iterable[CorreoEntrega]:
        for ruta in sorted(self.carpeta.glob("*.eml")):
            mensaje = email.message_from_bytes(ruta.read_bytes(), policy=policy.default)
            if not ASUNTO_ENTREGA.search(mensaje.get("Subject", "")):
                continue
            yield _correo_desde_mime(ruta.name, mensaje)


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


def _correo_desde_mime(identificador: str, mensaje) -> CorreoEntrega:
    partes, sin_leer = [], []
    cuerpo = mensaje.get_body(preferencelist=("plain",))
    if cuerpo is not None:
        partes.append(cuerpo.get_content())
    for adjunto in mensaje.iter_attachments():
        nombre = adjunto.get_filename() or "adjunto"
        datos = adjunto.get_payload(decode=True) or b""
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
