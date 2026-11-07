"""Seguimiento de las sugerencias de gestión de un día a otro.

Cada sugerencia recibe un código estable (paciente, servicio y acción), así que la misma
sugerencia que se repite mañana es la misma fila. El equipo la marca como hecha o como no
aplica; si el paciente sigue en el tablero y la sugerencia deja de aparecer, se cierra sola
como resuelta. Con eso se mide la adherencia por servicio y el tiempo hasta resolver.

La base es un archivo SQLite local. Guarda cama y seudónimo, nunca nombre ni documento.
"""

from __future__ import annotations

import csv
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import median

from .prediccion.sugerencias import Sugerencia

ESTADOS = ("pendiente", "hecho", "no_aplica", "resuelta")
_ABIERTO = "pendiente"

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS sugerencias (
    id TEXT PRIMARY KEY,
    paciente TEXT NOT NULL,
    cama TEXT NOT NULL,
    tipo TEXT NOT NULL,
    servicio TEXT NOT NULL,
    accion TEXT NOT NULL,
    motivo TEXT NOT NULL,
    prioridad TEXT NOT NULL,
    primera_vez TEXT NOT NULL,
    ultima_vez TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'pendiente',
    fecha_estado TEXT,
    nota TEXT
)
"""


def codigo(paciente: str, servicio: str, accion: str) -> str:
    return hashlib.sha1(f"{paciente}|{servicio}|{accion}".encode()).hexdigest()[:8].upper()


@dataclass
class Seguimiento:
    id: str
    dias_abierta: int
    estado: str


class BaseSeguimiento:
    def __init__(self, ruta: str | Path):
        self.con = sqlite3.connect(ruta)
        self.con.row_factory = sqlite3.Row
        self.con.execute(_ESQUEMA)

    def cerrar(self) -> None:
        self.con.close()

    def registrar(self, fecha: date, sugerencias: list[tuple[str, Sugerencia]]) -> dict[tuple[str, str, str], Seguimiento]:
        """Registra las sugerencias del día. Cada una llega con la clave del paciente
        (seudónimo o, si no hay, cama). Devuelve el seguimiento por (cama, servicio, acción)."""
        hoy = fecha.isoformat()
        vistos, pacientes_hoy, resultado = set(), set(), {}
        for paciente, s in sugerencias:
            pacientes_hoy.add(paciente)
            i = codigo(paciente, s.servicio, s.accion)
            vistos.add(i)
            fila = self.con.execute("SELECT * FROM sugerencias WHERE id = ?", (i,)).fetchone()
            if fila is None:
                self.con.execute(
                    "INSERT INTO sugerencias (id, paciente, cama, tipo, servicio, accion, motivo, prioridad, primera_vez, ultima_vez)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (i, paciente, s.cama, s.tipo, s.servicio, s.accion, s.motivo, s.prioridad, hoy, hoy))
                primera, estado = fecha, _ABIERTO
            else:
                estado = fila["estado"]
                if estado == "resuelta":  # reapareció: vuelve a quedar pendiente
                    estado = _ABIERTO
                self.con.execute("UPDATE sugerencias SET cama = ?, motivo = ?, prioridad = ?, ultima_vez = ?, estado = ? WHERE id = ?",
                                 (s.cama, s.motivo, s.prioridad, hoy, estado, i))
                primera = date.fromisoformat(fila["primera_vez"])
            resultado[(s.cama, s.servicio, s.accion)] = Seguimiento(i, (fecha - primera).days, estado)
        # Paciente presente hoy y sugerencia que ya no aparece: la condición se resolvió.
        for fila in self.con.execute("SELECT id, paciente FROM sugerencias WHERE estado = ? AND ultima_vez < ?", (_ABIERTO, hoy)).fetchall():
            if fila["paciente"] in pacientes_hoy and fila["id"] not in vistos:
                self.con.execute("UPDATE sugerencias SET estado = 'resuelta', fecha_estado = ? WHERE id = ?", (hoy, fila["id"]))
        self.con.commit()
        return resultado

    def marcar(self, ident: str, estado: str, fecha: date, nota: str | None = None) -> bool:
        if estado not in ESTADOS:
            raise ValueError(f"Estado no válido: {estado}. Use uno de {', '.join(ESTADOS)}")
        cur = self.con.execute("UPDATE sugerencias SET estado = ?, fecha_estado = ?, nota = COALESCE(?, nota) WHERE id = ?",
                               (estado, fecha.isoformat(), nota, ident.strip().upper()))
        self.con.commit()
        return cur.rowcount == 1

    def importar(self, ruta: str | Path, fecha: date) -> int:
        """Lee un CSV con columnas id, estado y nota (opcional), como el que devuelve `exportar`."""
        n = 0
        with open(ruta, newline="", encoding="utf-8-sig") as f:
            muestra = f.readline()
            f.seek(0)
            for fila in csv.DictReader(f, delimiter=";" if ";" in muestra else ","):
                estado = (fila.get("estado") or "").strip().lower().replace(" ", "_")
                if estado and estado != _ABIERTO and self.marcar(fila["id"], estado, fecha, (fila.get("nota") or "").strip() or None):
                    n += 1
        return n

    def exportar(self, ruta: str | Path) -> int:
        """Pendientes en CSV (separado por punto y coma, abre directo en Excel) para que el equipo los marque."""
        filas = self.con.execute(
            "SELECT id, cama, servicio, accion, motivo, prioridad, primera_vez FROM sugerencias WHERE estado = ? ORDER BY servicio, cama",
            (_ABIERTO,)).fetchall()
        with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["id", "cama", "servicio", "accion", "motivo", "prioridad", "desde", "estado", "nota"])
            for r in filas:
                w.writerow([*r, "pendiente", ""])
        return len(filas)

    def adherencia(self) -> list[dict]:
        """Por servicio: sugerencias generadas, cómo se cerraron y días hasta marcarlas hechas."""
        filas = self.con.execute("SELECT servicio, estado, primera_vez, fecha_estado FROM sugerencias").fetchall()
        por = {}
        for r in filas:
            d = por.setdefault(r["servicio"], {"servicio": r["servicio"], "total": 0, **{e: 0 for e in ESTADOS}, "_dias": []})
            d["total"] += 1
            d[r["estado"]] += 1
            if r["estado"] == "hecho" and r["fecha_estado"]:
                d["_dias"].append((date.fromisoformat(r["fecha_estado"]) - date.fromisoformat(r["primera_vez"])).days)
        salida = []
        for d in sorted(por.values(), key=lambda d: -d["total"]):
            dias = d.pop("_dias")
            gestionadas = d["hecho"] + d["no_aplica"]
            d["gestionadas_pct"] = round(100 * gestionadas / d["total"], 1) if d["total"] else 0.0
            d["mediana_dias_a_hecho"] = median(dias) if dias else None
            salida.append(d)
        return salida
