"""Lectura tolerante de fechas: las entregas usan al menos seis formatos y traen errores de digitación."""

from __future__ import annotations

import re
from datetime import date, timedelta

_MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}

_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_DMY = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2,4}))?\b")
_TEXTO = re.compile(r"\b(\d{1,2})\s*(?:de\s+)?([a-záéíóú]{3})[a-záéíóú]*\.?\s*(?:de\s+)?(\d{4})?\b", re.I)

PATRON_FECHA = re.compile(
    r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/.\-]\d{1,2}(?:[/.\-]\d{2,4})?|\d{1,2}\s*(?:de\s+)?[a-z]{3}[a-z]*\.?(?:\s*(?:de\s+)?\d{4})?",
    re.I,
)


def leer_fecha(texto: str | None, referencia: date | None = None) -> tuple[date | None, str | None]:
    """Devuelve (fecha, advertencia).

    Sin año se asume el de la referencia. Una fecha a más de un año de la referencia o en el
    futuro se descarta con advertencia, porque casi siempre es un error de digitación
    (por ejemplo "26-10-36").
    """
    if not texto:
        return None, None
    texto = texto.strip()
    referencia = referencia or date.today()
    candidato = None

    if m := _ISO.search(texto):
        a, mes, d = (int(x) for x in m.groups())
        candidato = _armar(a, mes, d)
    elif m := _DMY.search(texto):
        d, mes = int(m.group(1)), int(m.group(2))
        a = m.group(3)
        if a is None:
            anio = referencia.year
        else:
            anio = int(a) + 2000 if len(a) == 2 else int(a)
        candidato = _armar(anio, mes, d)
    elif m := _TEXTO.search(texto):
        mes = _MESES.get(m.group(2).lower()[:3])
        if mes:
            anio = int(m.group(3)) if m.group(3) else referencia.year
            candidato = _armar(anio, mes, int(m.group(1)))

    if candidato is None:
        return None, f"fecha ilegible: «{texto}»"
    if candidato > referencia + timedelta(days=1) or candidato < referencia - timedelta(days=366):
        return None, f"fecha fuera de rango, posible error de digitación: «{texto}»"
    return candidato, None


def _armar(anio: int, mes: int, dia: int) -> date | None:
    try:
        return date(anio, mes, dia)
    except ValueError:
        return None
