"""Normalización compartida por los conectores."""

from __future__ import annotations

import re
import unicodedata

from ..modelos import Condicion


def sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def normalizar_cama(valor: str | None, prefijo: str = "U") -> str | None:
    """Lleva "cama 075", "U-75", "u75" o "75" a "U75", que es el código de ORBIT."""
    if not valor:
        return None
    texto = sin_tildes(valor).upper()
    texto = re.sub(r"\bCAMA\b", "", texto)
    texto = re.sub(r"[\s\-_.]", "", texto)
    m = re.fullmatch(r"([A-Z]*)0*(\d+)([A-Z]?)", texto)
    if not m:
        return texto or None
    letras, numero, sufijo = m.groups()
    return f"{letras or prefijo}{numero}{sufijo}"


def si_no(valor: str | None) -> bool | None:
    if valor is None:
        return None
    texto = sin_tildes(str(valor)).strip().upper()
    if texto in {"SI", "S", "1", "TRUE", "X", "YES"}:
        return True
    if texto in {"NO", "N", "0", "FALSE", "", "NINGUNO", "NINGUNA"}:
        return False if texto else None
    return None


def leer_condicion(valor: str | None) -> Condicion:
    if not valor:
        return Condicion.DESCONOCIDA
    texto = sin_tildes(valor).upper()
    if "PISO" in texto or "HOSPITALIZ" in texto:
        return Condicion.PISO
    if "UCIN" in texto or "INTERMED" in texto:
        return Condicion.UCIN
    if "UCI" in texto or "INTENSIV" in texto:
        return Condicion.UCI
    return Condicion.DESCONOCIDA


def texto_o_none(valor: str | None) -> str | None:
    if valor is None:
        return None
    valor = valor.strip()
    return valor or None
