"""Minimización de datos antes de enviar texto a Claude o de escribirlo en un informe.

La identificación de un paciente en el agente es la cama. Nombres, documentos, teléfonos,
correos y credenciales se retiran del texto libre. Es una protección adicional; la regla
principal es no meter datos personales en los campos estructurados.
"""

from __future__ import annotations

import re

# "Contraseña equipo: ...", "Password correo = ...". "Clave" y "PIN" solo cuentan en líneas cortas
# del tipo "Clave GLPI: ...", para no confundirlos con texto clínico.
_SECRETO = re.compile(r"(contrase[nñ]a|password|passwd)[^:=\n]{0,40}[:=]", re.I)
_SECRETO_CORTO = re.compile(r"^\s*[-•*]?\s*(clave|pin|usuario\s*y\s*clave)\b[^:=\n]{0,30}[:=]\s*\S+\s*$", re.I)
_CORREO = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_NUMERO_LARGO = re.compile(r"\b\d(?:[ .-]?\d){6,}\b")
_FECHA = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[-.]\d{1,2}[-.]\d{2,4}")
_DOCUMENTO = re.compile(r"\b(CC|C\.C\.|TI|CE|RC|NUIP|documento|c[eé]dula)\b\.?\s*(?:de\s+)?[:#]?\s*\d[\d.]*", re.I)
_NOMBRE_TRAS_PALABRA = re.compile(
    r"\b((?i:paciente|familiar|hij[oa]|espos[oa]|madre|padre|herman[oa]|sr\.?|sra\.?|señor|señora|"
    r"nombre|acompañante|cuidador[a]?))\s*[:,]?\s+((?:[A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+\s*){1,4})"
)


def contiene_secreto(linea: str) -> bool:
    return bool(_SECRETO.search(linea) or _SECRETO_CORTO.match(linea))


def limpiar_texto(texto: str, max_caracteres: int | None = None) -> str:
    lineas = [l for l in texto.splitlines() if not contiene_secreto(l)]
    limpio = "\n".join(lineas)
    limpio = _CORREO.sub("[correo]", limpio)
    limpio = _DOCUMENTO.sub(lambda m: f"{m.group(1)} [doc]", limpio)
    limpio = _NOMBRE_TRAS_PALABRA.sub(lambda m: f"{m.group(1)} [nombre] ", limpio)
    limpio = _NUMERO_LARGO.sub(lambda m: m.group(0) if _FECHA.fullmatch(m.group(0)) else "[número]", limpio)
    limpio = re.sub(r"[ \t]+", " ", limpio).strip()
    if max_caracteres and len(limpio) > max_caracteres:
        limpio = limpio[: max_caracteres - 1] + "…"
    return limpio
