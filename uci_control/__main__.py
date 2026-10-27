"""Línea de comandos.

    python -m uci_control informe --fecha 2026-10-26 --orbit censo.csv --correos carpeta/ --costos cargos.csv
    python -m uci_control riesgo --tablero Reporte_TableroControlUCI1.xlsx --salida salida/
    python -m uci_control datos-prueba --salida datos_prueba/
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from .conectores import CarpetaCorreos, leer_cargos, leer_censo_orbit, parsear_entrega
from .informe import a_json, a_markdown
from .orquestador import Orquestador


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="uci_control", description="UCI CONTROL AI")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("informe", help="Concilia las fuentes y genera el informe diario")
    p.add_argument("--fecha", required=True, type=date.fromisoformat)
    p.add_argument("--orbit", required=True, type=Path, help="Exportación CSV del censo de ORBIT")
    p.add_argument("--correos", required=True, type=Path, help="Carpeta con las entregas de turno (.eml)")
    p.add_argument("--costos", type=Path, help="CSV de cargos de facturación")
    p.add_argument("--tablero", type=Path, help="Tablero ORBIT (.xlsx) para sumar riesgo y sugerencias por paciente")
    p.add_argument("--salida", type=Path, default=Path("salida"))
    p.add_argument("--sin-ia", action="store_true", help="No consultar a Claude; solo reglas")
    p.add_argument("--esfuerzo", default="high", choices=["low", "medium", "high", "xhigh", "max"])

    r = sub.add_parser("riesgo", help="Riesgo y sugerencias de gestión por paciente desde el tablero de ORBIT")
    r.add_argument("--tablero", required=True, type=Path, help="Reporte_TableroControlUCI1<fecha>.xlsx")
    r.add_argument("--salida", type=Path, default=Path("salida"))

    d = sub.add_parser("datos-prueba", help="Genera datos sintéticos para probar el agente")
    d.add_argument("--salida", type=Path, default=Path("datos_prueba"))

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.comando == "datos-prueba":
        from datos_prueba.generar import generar

        from datos_prueba.tablero import generar_tablero

        generar(args.salida)
        generar_tablero(args.salida / "tablero_orbit.xlsx")
        print(f"Datos de prueba en {args.salida}")
        return 0

    if args.comando == "riesgo":
        from .conectores import leer_tablero
        from .informe_riesgo import a_json as riesgo_json, a_markdown as riesgo_md

        pacientes = leer_tablero(args.tablero)
        fecha = pacientes[0].fecha.isoformat() if pacientes else "sin_fecha"
        args.salida.mkdir(parents=True, exist_ok=True)
        md = args.salida / f"riesgo_{fecha}.md"
        js = args.salida / f"riesgo_{fecha}.json"
        md.write_text(riesgo_md(pacientes), encoding="utf-8")
        js.write_text(riesgo_json(pacientes), encoding="utf-8")
        print(f"Riesgo y sugerencias: {md}\nDatos: {js}")
        return 0

    censo = leer_censo_orbit(args.orbit, args.fecha)
    entregas = []
    for correo in CarpetaCorreos(args.correos).entregas(args.fecha):
        entrega = parsear_entrega(correo.texto, fuente=correo.id, fecha_referencia=args.fecha)
        for adjunto in correo.adjuntos_sin_leer:
            entrega.alertas.append(f"Adjunto sin leer (formato no soportado): {adjunto}")
        entregas.append(entrega)
    cargos = leer_cargos(args.costos, args.fecha) if args.costos else []

    informe = Orquestador(usar_ia=not args.sin_ia, esfuerzo=args.esfuerzo).ejecutar(args.fecha, censo, entregas, cargos)

    args.salida.mkdir(parents=True, exist_ok=True)
    md = args.salida / f"informe_{args.fecha.isoformat()}.md"
    js = args.salida / f"informe_{args.fecha.isoformat()}.json"
    texto = a_markdown(informe)
    if args.tablero:
        from .conectores import leer_tablero
        from .informe_riesgo import a_json as riesgo_json, a_markdown as riesgo_md

        pacientes = leer_tablero(args.tablero)
        texto += "\n" + riesgo_md(pacientes).replace("# Riesgo", "## Riesgo", 1).replace("\n## ", "\n### ")
        (args.salida / f"riesgo_{args.fecha.isoformat()}.json").write_text(riesgo_json(pacientes), encoding="utf-8")
    md.write_text(texto, encoding="utf-8")
    js.write_text(a_json(informe), encoding="utf-8")
    print(f"Informe: {md}\nDatos: {js}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
