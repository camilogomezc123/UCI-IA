"""Tablero ORBIT sintético con el mismo formato del "Reporte_TableroControlUCI1".

Ningún paciente es real: documentos, edades, diagnósticos y textos están inventados para
disparar cada regla de riesgo y de sugerencia al menos una vez.
"""

from __future__ import annotations

from pathlib import Path

COLUMNAS = ["ID", "UBICACIÓN", "DOCUMENTO PACIENTE", "NOMBRE PACIENTE", "EDAD", "SEXO", "EAPB", "CIE10",
            "DIAGNÓSTICO ONCOLÓGICO", "DIAGNÓSTICOS", "CRITERIO DE ATENCIÓN", "ESPECIALIDAD A CARGO",
            "ESTADO NUTRICIONAL", "DIETA", "SOPORTE HEMODINÁMICO", "SOPORTE VENTILATORIO", "MOVILIZACIÓN TEMPRANA",
            "NEWS", "SOFA", "BARTHEL", "DE MOVILIDAD", "RASS", "CPOT", "EVA", "MUST", "RIESGOS DEL PACIENTE",
            "OBSERVACIONES", "METAS CLINICAS DIARIAS", "TIEMPO DE HOSPITALIZACIÓN"]

_MUST_ALTO = "<p><b>RIESGO ALTO</b></p>"
_MUST_BAJO = "<p>RIESGO BAJO</p>"

# cama, documento, edad, sexo, eapb, cie10, onco, diagnósticos, criterio, dieta, hemodinámico, ventilatorio,
# news, sofa, barthel, ims, rass, cpot, eva, must, riesgos, observaciones, metas, tiempo
PACIENTES = [
    ("CAMA 01", "SINT-0001", 67, "M", "EPS EJEMPLO", "J960 R572 A419", "NO APLICA",
     "CHOQUE SEPTICO DE ORIGEN PULMONAR\nINTUBACION OROTRAQUEAL: 10/10/2026", "CUIDADO INTENSIVO",
     "NUTRICION ENTERAL DOSIS TROFICA", "VASOPRESOR", "VMI", 9, "40-50%", "DEPENDIENTE SEVERO", 0, -5, 4, None,
     _MUST_ALTO, "RIESGO DE INFECCION\nRIESGO DE CAIDA",
     "SOPORTES: VASOACTIVO NOREPINEFRINA\nANTIBIOTICO(S):\nMEROPENEM FECHA DE INICIO 17/10/2026\n"
     "VANCOMICINA FECHA DE INICIO 18/10/2026\nANTIBIOTICOS PREVIOS: CEFTRIAXONA\nCULTIVOS: HEMOCULTIVO NEGATIVO",
     "PENDIENTE TAC DE TORAX", "17dias 3hrs 10min"),
    ("CAMA 02", "SINT-0002", 54, "F", "EPS EJEMPLO", "I214", "NO APLICA",
     "INFARTO AGUDO DE MIOCARDIO", "OTROS", "DIETA HIPOSODICA", "NINGUNO", "AIRE AMBIENTE", 2, "0-3%",
     "INDEPENDIENTE", 7, 0, 0, 1, _MUST_BAJO, "", "SIN ANTIBIOTICOS", "", "3dias 1hrs 0min"),
    ("CAMA 03", "SINT-0003", 72, "M", "GOVERNMENT EXAMPLE", "C189 K650", "ADENOCARCINOMA DE COLON",
     "PERITONITIS POSTOPERATORIA", "CUIDADO INTERMEDIO", "NADA VIA ORAL", "NINGUNO", "CANULA NASAL BAJO FLUJO", 3,
     "30-35%", "DEPENDIENTE MODERADO", 2, 0, 0, 6, _MUST_ALTO, "",
     "ANTIBIOTICO(S): PIPERACILINA TAZOBACTAM", "PENDIENTE VALORACION POR NUTRICION", "15dias 0hrs 0min"),
    ("CAMA 04", "SINT-0004", 45, "F", "EPS EJEMPLO", "G409", "NO APLICA",
     "ESTADO EPILEPTICO", "OTROS", "", "VASOPRESOR", "VMI", None, None, "", None, -2, 0, None, None, "",
     "", "", "1dias 2hrs 0min"),
]


def generar_tablero(ruta: str | Path, fecha: str = "2026-10-27") -> Path:
    from openpyxl import Workbook

    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.append(["TABLERO DE UCI"])
    ws.append([f"FECHA GENERACIÓN: {fecha} 06:00:00"])
    ws.append(COLUMNAS)
    for i, (cama, doc, edad, sexo, eapb, cie, onco, dx, crit, dieta, hemo, vent, news, sofa, barthel, ims,
            rass, cpot, eva, must, riesgos, obs, metas, tiempo) in enumerate(PACIENTES, 1):
        ws.append([i, cama, doc, "PACIENTE SINTETICO", edad, sexo, eapb, cie, onco, dx, crit, "MEDICINA CRITICA",
                   "", dieta, hemo, vent, "", news, sofa, barthel, f"IMS {ims}" if ims is not None else "",
                   rass, cpot, eva, must, riesgos, obs, metas, tiempo])
    wb.save(ruta)
    return ruta
