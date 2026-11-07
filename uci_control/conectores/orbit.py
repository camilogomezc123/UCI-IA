"""Lectura de la exportación del censo de ORBIT (CSV).

El formato exacto de la exportación aún no está confirmado; los nombres de columna se
buscan entre varios alias para que ajustar el conector sea cambiar este diccionario.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from ..fechas import leer_fecha
from ..modelos import RegistroOrbit
from .comun import leer_condicion, normalizar_cama, si_no, sin_tildes, texto_o_none

ALIAS_COLUMNAS = {
    "cama": ("cama", "codigo_cama", "ubicacion"),
    "unidad": ("unidad", "servicio", "estacion"),
    "fecha": ("fecha", "fecha_censo", "fecha_corte"),
    "ocupada": ("ocupada", "estado", "estado_cama"),
    "episodio": ("episodio", "ingreso", "numero_ingreso", "no_ingreso"),
    "fecha_ingreso_hospital": ("fecha_ingreso_hospital", "fecha_ingreso", "ingreso_hospital"),
    "fecha_ingreso_uci": ("fecha_ingreso_uci", "ingreso_uci", "fecha_uci"),
    "criterio": ("criterio", "criterio_atencion", "condicion", "nivel_atencion"),
    "vmi": ("vmi", "ventilacion_mecanica", "ventilacion_invasiva"),
    "vasopresor": ("vasopresor", "vasoactivo", "soporte_vasoactivo"),
    "news": ("news", "news2"),
    "sofa": ("sofa",),
    "aislamiento": ("aislamiento", "tipo_aislamiento"),
}


def _clave(nombre: str) -> str:
    return sin_tildes(nombre).strip().lower().replace(" ", "_")


def leer_censo_orbit(ruta: str | Path, fecha_referencia: date | None = None) -> list[RegistroOrbit]:
    with open(ruta, newline="", encoding="utf-8-sig") as f:
        muestra = f.read(2048)
        f.seek(0)
        dialecto = csv.Sniffer().sniff(muestra, delimiters=",;\t")
        lector = csv.DictReader(f, dialect=dialecto)
        columnas = {_clave(c): c for c in lector.fieldnames or []}
        mapa = {}
        for campo, alias in ALIAS_COLUMNAS.items():
            for a in alias:
                if a in columnas:
                    mapa[campo] = columnas[a]
                    break
        if "cama" not in mapa:
            raise ValueError(f"La exportación de ORBIT no trae columna de cama: {lector.fieldnames}")

        registros = []
        for fila in lector:
            def v(campo):
                return texto_o_none(fila.get(mapa.get(campo, ""), None))

            fecha, _ = leer_fecha(v("fecha"), fecha_referencia)
            fecha = fecha or fecha_referencia
            if fecha is None:
                raise ValueError("Falta la fecha del censo en la exportación y no se indicó --fecha")
            estado = v("ocupada")
            ocupada = si_no(estado)
            if ocupada is None and estado:
                ocupada = sin_tildes(estado).upper().startswith("OCUP")
            registros.append(
                RegistroOrbit(
                    cama=normalizar_cama(v("cama")),
                    unidad=v("unidad") or "",
                    fecha=fecha,
                    ocupada=bool(ocupada),
                    episodio=v("episodio"),
                    fecha_ingreso_hospital=leer_fecha(v("fecha_ingreso_hospital"), fecha)[0],
                    fecha_ingreso_uci=leer_fecha(v("fecha_ingreso_uci"), fecha)[0],
                    criterio=leer_condicion(v("criterio")),
                    vmi=si_no(v("vmi")),
                    vasopresor=si_no(v("vasopresor")),
                    news=v("news"),
                    sofa=v("sofa"),
                    aislamiento=v("aislamiento"),
                )
            )
    return registros
