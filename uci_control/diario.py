"""Corrida diaria: lee el buzón, arma el informe, registra el seguimiento y lo envía.

    fuente (Gmail o carpeta .eml) ─► entregas de turno + tableros ORBIT adjuntos
                                     │
            censo desde el tablero ◄─┤ (o la exportación CSV del censo, si existe)
                                     ▼
            conciliación + riesgo y sugerencias ─► seguimiento (SQLite) ─► MD / JSON / HTML / correo

Pensada para programarse cada mañana en un equipo de la institución (ver docs/OPERACION.md).
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from .conectores import leer_cargos, leer_censo_orbit, parsear_entrega
from .conectores.gmail import FuenteCorreos
from .conectores.orbit_tablero import PacienteTablero, censo_desde_tablero, leer_tablero
from .informe import a_json, a_markdown
from .informe_html import correo, pagina
from .camas import gestionar_camas
from .informe_riesgo import a_markdown as riesgo_md, construir
from .orquestador import Orquestador
from .prediccion import sugerir
from .seguimiento import BaseSeguimiento

log = logging.getLogger(__name__)


@dataclass
class ResultadoDia:
    fecha: date
    archivos: dict[str, Path] = field(default_factory=dict)
    camas_tablero: int = 0
    entregas: int = 0
    sugerencias: int = 0
    html_correo: str = ""
    avisos: list[str] = field(default_factory=list)


def leer_tableros(adjuntos: list[tuple[str, bytes]]) -> list[PacienteTablero]:
    """Lee todos los tableros recibidos (uno por torre) y deja una fila por cama: la del tablero más reciente."""
    por_cama: dict[str, PacienteTablero] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for i, (nombre, datos) in enumerate(adjuntos):
            ruta = Path(tmp) / f"{i}_{Path(nombre).name}"
            ruta.write_bytes(datos)
            try:
                pacientes = leer_tablero(ruta)
            except Exception as e:  # un adjunto dañado no detiene la corrida
                log.warning("No se pudo leer el tablero %s: %s", nombre, e)
                continue
            for p in pacientes:
                if p.cama not in por_cama or p.fecha >= por_cama[p.cama].fecha:
                    por_cama[p.cama] = p
    return sorted(por_cama.values(), key=lambda p: p.cama)


def correr_dia(fecha: date, fuente: FuenteCorreos, salida: Path, base_seguimiento: Path,
               censo_csv: Path | None = None, costos_csv: Path | None = None, usar_ia: bool = False,
               historial_camas: Path | None = None, camas_totales: int | None = None,
               demanda_esperada: float | None = None) -> ResultadoDia:
    r = ResultadoDia(fecha)
    correos = list(fuente.entregas(fecha))
    entregas = []
    adjuntos = [t for c in correos for t in c.tableros]
    for c in correos:
        e = parsear_entrega(c.texto, fuente=c.id, fecha_referencia=fecha)
        for adjunto in c.adjuntos_sin_leer:
            e.alertas.append(f"Adjunto sin leer (formato no soportado): {adjunto}")
        entregas.append(e)
    r.entregas = len(entregas)
    if not adjuntos:
        adjuntos = fuente.tableros(fecha)
    pacientes = leer_tableros(adjuntos)
    r.camas_tablero = len(pacientes)
    if not pacientes:
        r.avisos.append("No llegó tablero de ORBIT: el informe sale sin riesgo por paciente.")
    if not entregas:
        r.avisos.append("No llegaron entregas de turno para la fecha.")

    censo = leer_censo_orbit(censo_csv, fecha) if censo_csv else censo_desde_tablero(pacientes)
    cargos = leer_cargos(costos_csv, fecha) if costos_csv else []
    informe = Orquestador(usar_ia=usar_ia).ejecutar(fecha, censo, entregas, cargos)
    if not costos_csv:
        r.avisos.append("Sin archivo de cargos: las reglas de facturación (R12, R13, R16) no se evaluaron.")

    riesgo = construir(pacientes)
    plan = None
    if pacientes:
        plan = gestionar_camas(fecha, pacientes, historial_camas or base_seguimiento.with_name("historial_camas.sqlite"),
                               camas_totales, demanda_esperada)
    camas = json.loads(json.dumps(asdict(plan), default=str)) if plan else None
    base = BaseSeguimiento(base_seguimiento)
    try:
        pares = [(p.seudonimo or p.cama, s) for p in pacientes for s in sugerir(p)]
        seg = base.registrar(fecha, pares)
        for fila in riesgo:
            for s in fila["sugerencias"]:
                x = seg.get((s["cama"], s["servicio"], s["accion"]))
                s["seguimiento"] = asdict(x) if x else None
        adherencia = base.adherencia()
        pendientes_csv = salida / f"pendientes_{fecha.isoformat()}.csv"
        salida.mkdir(parents=True, exist_ok=True)
        base.exportar(pendientes_csv)
    finally:
        base.cerrar()
    r.sugerencias = len(pares)

    nombre = f"uci_{fecha.isoformat()}"
    md = a_markdown(informe) + ("\n" + riesgo_md(pacientes).replace("# Riesgo", "## Riesgo", 1).replace("\n## ", "\n### ") if pacientes else "")
    datos_informe = json.loads(a_json(informe))
    archivos = {
        "markdown": (salida / f"{nombre}.md", md),
        "json": (salida / f"{nombre}.json", json.dumps({"informe": datos_informe, "riesgo": riesgo, "adherencia": adherencia, "camas": camas, "avisos": r.avisos},
                                                       ensure_ascii=False, indent=1, default=str)),
        "html": (salida / f"{nombre}.html", pagina({"informe": datos_informe, "riesgo": riesgo, "adherencia": adherencia, "camas": camas, "demo": False})),
    }
    for clave, (ruta, texto) in archivos.items():
        ruta.write_text(texto, encoding="utf-8")
        r.archivos[clave] = ruta
    r.archivos["pendientes"] = pendientes_csv
    r.html_correo = correo(informe, riesgo, adherencia, camas)
    if r.avisos:
        r.html_correo = r.html_correo.replace("</h2>", "</h2>" + "".join(
            f'<p style="margin:4px 0;color:#c25e00">Aviso: {a}</p>' for a in r.avisos), 1)
    return r
