"""Línea de comandos.

    python -m uci_control informe --fecha 2026-10-26 --orbit censo.csv --correos carpeta/ --costos cargos.csv
    python -m uci_control riesgo --tablero Reporte_TableroControlUCI1.xlsx --salida salida/
    python -m uci_control diario --gmail-token token.json --enviar coordinacion@clinica.co
    python -m uci_control seguimiento marcar A1B2C3D4 hecho --nota "traqueostomía programada"
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

    dia = sub.add_parser("diario", help="Corrida diaria: lee el buzón, genera el informe, actualiza el seguimiento y lo envía")
    dia.add_argument("--fecha", type=date.fromisoformat, default=None, help="Por defecto, hoy")
    origen = dia.add_mutually_exclusive_group(required=True)
    origen.add_argument("--gmail-token", type=Path, help="Token OAuth de Gmail (gmail.readonly, y gmail.send si se usa --enviar)")
    origen.add_argument("--correos", type=Path, help="Carpeta con correos .eml (entregas y tableros adjuntos)")
    dia.add_argument("--orbit", type=Path, help="Exportación CSV del censo; si falta, el censo sale del tablero")
    dia.add_argument("--costos", type=Path, help="CSV de cargos de facturación")
    dia.add_argument("--seguimiento", type=Path, default=Path("seguimiento.sqlite"), help="Base del seguimiento de sugerencias")
    dia.add_argument("--salida", type=Path, default=Path("salida"))
    dia.add_argument("--enviar", nargs="+", metavar="CORREO", help="Destinatarios del informe (requiere --gmail-token)")
    dia.add_argument("--con-ia", action="store_true", help="Pedir a Claude el resumen y las prioridades")

    sg = sub.add_parser("seguimiento", help="Marcar sugerencias y ver la adherencia")
    sg.add_argument("--base", type=Path, default=Path("seguimiento.sqlite"))
    sgs = sg.add_subparsers(dest="accion", required=True)
    m = sgs.add_parser("marcar", help="Marcar una sugerencia por su código")
    m.add_argument("codigo")
    m.add_argument("estado", choices=["hecho", "no_aplica", "pendiente"])
    m.add_argument("--nota")
    imp = sgs.add_parser("importar", help="Leer el CSV de pendientes con la columna estado diligenciada")
    imp.add_argument("archivo", type=Path)
    exp = sgs.add_parser("exportar", help="Escribir los pendientes en CSV para diligenciar en Excel")
    exp.add_argument("archivo", type=Path)
    sgs.add_parser("resumen", help="Adherencia por servicio")

    d = sub.add_parser("datos-prueba", help="Genera datos sintéticos para probar el agente")
    d.add_argument("--salida", type=Path, default=Path("datos_prueba"))

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.comando == "datos-prueba":
        from datos_prueba.generar import generar

        from datos_prueba.tablero import generar_correo_tablero, generar_tablero

        generar(args.salida)
        generar_tablero(args.salida / "tablero_orbit.xlsx")
        generar_correo_tablero(args.salida / "correos")
        print(f"Datos de prueba en {args.salida}")
        return 0

    if args.comando == "seguimiento":
        from .seguimiento import BaseSeguimiento

        base = BaseSeguimiento(args.base)
        try:
            if args.accion == "marcar":
                ok = base.marcar(args.codigo, args.estado, date.today(), args.nota)
                print("Marcada." if ok else f"No existe la sugerencia {args.codigo}.")
                return 0 if ok else 1
            if args.accion == "importar":
                print(f"{base.importar(args.archivo, date.today())} sugerencias actualizadas.")
            elif args.accion == "exportar":
                print(f"{base.exportar(args.archivo)} pendientes en {args.archivo}")
            else:
                for d in base.adherencia():
                    print(f"{d['servicio']}: {d['total']} sugerencias, {d['gestionadas_pct']}% gestionadas, {d['pendiente']} pendientes")
        finally:
            base.cerrar()
        return 0

    if args.comando == "diario":
        from .conectores.gmail import GmailAPI
        from .diario import correr_dia

        fecha = args.fecha or date.today()
        gmail = GmailAPI(servicio_gmail(args.gmail_token)) if args.gmail_token else None
        if args.enviar and not gmail:
            parser.error("--enviar requiere --gmail-token")
        r = correr_dia(fecha, gmail or CarpetaCorreos(args.correos), args.salida, args.seguimiento,
                       censo_csv=args.orbit, costos_csv=args.costos, usar_ia=args.con_ia)
        for aviso in r.avisos:
            logging.warning(aviso)
        print(f"{r.camas_tablero} camas del tablero, {r.entregas} entregas, {r.sugerencias} sugerencias.")
        for clave, ruta in r.archivos.items():
            print(f"{clave}: {ruta}")
        if args.enviar:
            gmail.enviar(args.enviar, f"UCI CONTROL AI · {fecha:%d/%m/%Y}", r.html_correo,
                         {r.archivos["html"].name: r.archivos["html"].read_bytes()})
            print(f"Enviado a {', '.join(args.enviar)}")
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


def servicio_gmail(token: Path):
    """Servicio de la API de Gmail desde un token OAuth guardado (ver docs/OPERACION.md)."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    credenciales = Credentials.from_authorized_user_file(str(token))
    if credenciales.expired and credenciales.refresh_token:
        credenciales.refresh(Request())
        token.write_text(credenciales.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=credenciales, cache_discovery=False)


if __name__ == "__main__":
    sys.exit(main())
