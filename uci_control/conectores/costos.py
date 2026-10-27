"""Lectura de cargos de facturación (CSV), la tercera fuente del motor de conciliación."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from ..config import PALABRAS_CARGO_ESTANCIA, PALABRAS_ESTANCIA_UCI
from ..fechas import leer_fecha
from ..modelos import CargoCosto
from .comun import normalizar_cama, sin_tildes, texto_o_none


def leer_cargos(ruta: str | Path, fecha_referencia: date | None = None) -> list[CargoCosto]:
    cargos = []
    with open(ruta, newline="", encoding="utf-8-sig") as f:
        muestra = f.read(2048)
        f.seek(0)
        lector = csv.DictReader(f, dialect=csv.Sniffer().sniff(muestra, delimiters=",;\t"))
        for fila in lector:
            fila = {sin_tildes(k).strip().lower(): v for k, v in fila.items()}
            fecha, _ = leer_fecha(fila.get("fecha"), fecha_referencia)
            if fecha is None:
                continue
            valor = (fila.get("valor") or "0").replace("$", "").replace(".", "").replace(",", ".").strip()
            cargos.append(
                CargoCosto(
                    episodio=texto_o_none(fila.get("episodio")),
                    cama=normalizar_cama(fila.get("cama")),
                    fecha=fecha,
                    concepto=(fila.get("concepto") or "").strip(),
                    valor=float(valor or 0),
                )
            )
    return cargos


def es_cargo_estancia(cargo: CargoCosto) -> bool:
    concepto = sin_tildes(cargo.concepto).upper()
    return any(sin_tildes(p).upper() in concepto for p in PALABRAS_CARGO_ESTANCIA)


def es_estancia_uci(cargo: CargoCosto) -> bool:
    concepto = sin_tildes(cargo.concepto).upper()
    return es_cargo_estancia(cargo) and any(p in concepto for p in PALABRAS_ESTANCIA_UCI) and "INTERMED" not in concepto and "UCIN" not in concepto
