"""Historial diario del tablero: una foto por paciente y por día.

Con dos o más días se puede saber cuándo entró cada paciente a la UCI, cuándo salió (dejó de
aparecer en el tablero), cuántos días lleva listo para salir sin salir (días "rojos" en el
lenguaje de Red2Green) y si su fecha esperada de egreso ya se venció.

Guarda cama y seudónimo, nunca nombre ni documento.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS foto (
    fecha TEXT NOT NULL,
    paciente TEXT NOT NULL,
    cama TEXT NOT NULL,
    criterio TEXT NOT NULL,
    estado_alta TEXT NOT NULL,
    fecha_esperada TEXT,
    barreras TEXT,
    PRIMARY KEY (fecha, paciente)
)
"""


@dataclass
class Trayectoria:
    paciente: str
    primer_dia: date
    dias_uci: int
    dias_listo_sin_salir: int  # días consecutivos (hasta hoy) en estado "listo"
    fecha_esperada_original: date | None  # la primera fecha esperada que se le asignó


class Historial:
    def __init__(self, ruta: str | Path):
        self.con = sqlite3.connect(ruta)
        self.con.execute(_ESQUEMA)

    def cerrar(self) -> None:
        self.con.close()

    def guardar(self, fecha: date, filas: list[dict]) -> None:
        """filas: paciente, cama, criterio, estado_alta, fecha_esperada, barreras (texto)."""
        f = fecha.isoformat()
        self.con.execute("DELETE FROM foto WHERE fecha = ?", (f,))
        self.con.executemany(
            "INSERT INTO foto VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(f, r["paciente"], r["cama"], r["criterio"], r["estado_alta"],
              r["fecha_esperada"].isoformat() if r.get("fecha_esperada") else None, r.get("barreras", "")) for r in filas])
        self.con.commit()

    def fecha_anterior(self, fecha: date) -> date | None:
        r = self.con.execute("SELECT MAX(fecha) FROM foto WHERE fecha < ?", (fecha.isoformat(),)).fetchone()[0]
        return date.fromisoformat(r) if r else None

    def trayectoria(self, paciente: str, fecha: date) -> Trayectoria:
        filas = self.con.execute("SELECT fecha, estado_alta, fecha_esperada FROM foto WHERE paciente = ? AND fecha <= ? ORDER BY fecha",
                                 (paciente, fecha.isoformat())).fetchall()
        if not filas:
            return Trayectoria(paciente, fecha, 0, 0, None)
        primer = date.fromisoformat(filas[0][0])
        listo = 0
        for f, estado, _ in reversed(filas):
            if estado != "listo":
                break
            listo += 1
        # El día de hoy cuenta como rojo solo si el paciente sigue en la cama al final del día;
        # por eso se cuentan los días previos en estado listo.
        listo = max(0, listo - 1)
        original = next((date.fromisoformat(e) for _, _, e in filas if e), None)
        return Trayectoria(paciente, primer, (fecha - primer).days, listo, original)

    def egresos(self, fecha: date) -> list[dict]:
        """Pacientes que estaban en el tablero anterior y ya no están: egresaron (o se trasladaron)."""
        anterior = self.fecha_anterior(fecha)
        if anterior is None:
            return []
        filas = self.con.execute(
            "SELECT a.paciente, a.cama, a.estado_alta, a.fecha_esperada FROM foto a WHERE a.fecha = ? AND a.paciente NOT IN "
            "(SELECT paciente FROM foto WHERE fecha = ?)", (anterior.isoformat(), fecha.isoformat())).fetchall()
        return [{"paciente": p, "cama": c, "estado_previo": e, "fecha_esperada": fe} for p, c, e, fe in filas]

    def indicadores_flujo(self, fecha: date, dias: int = 30) -> dict:
        """Indicadores de los últimos `dias`: egresos detectados, cuántos salieron el día que
        estaban listos y días-cama rojos (paciente listo que no salió)."""
        desde = (fecha - timedelta(days=dias)).isoformat()
        fechas = [date.fromisoformat(f) for (f,) in self.con.execute(
            "SELECT DISTINCT fecha FROM foto WHERE fecha >= ? AND fecha <= ? ORDER BY fecha", (desde, fecha.isoformat()))]
        egresos = listos_que_salieron = listos_total = rojos = 0
        for anterior, actual in zip(fechas, fechas[1:]):
            previos = dict(self.con.execute("SELECT paciente, estado_alta FROM foto WHERE fecha = ?", (anterior.isoformat(),)).fetchall())
            hoy = {p for (p,) in self.con.execute("SELECT paciente FROM foto WHERE fecha = ?", (actual.isoformat(),))}
            for paciente, estado in previos.items():
                salio = paciente not in hoy
                egresos += salio
                if estado == "listo":
                    listos_total += 1
                    listos_que_salieron += salio
                    rojos += not salio
        return {
            "dias_con_datos": len(fechas),
            "egresos_detectados": egresos,
            "listos_que_salieron_al_dia_siguiente_pct": round(100 * listos_que_salieron / listos_total, 1) if listos_total else None,
            "dias_cama_rojos": rojos,
        }
