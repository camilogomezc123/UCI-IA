"""Genera un día sintético (26/10/2026) con los patrones vistos en las entregas reales.

Todos los pacientes, números de ingreso, nombres y teléfonos son inventados. Cada cama
ejercita una o más reglas del motor; el comentario de cada caso dice cuál.
"""

from __future__ import annotations

import csv
from email.message import EmailMessage
from pathlib import Path

FECHA = "2026-10-26"

# cama, unidad, ocupada, episodio, ingreso_hosp, ingreso_uci, criterio, vmi, vasopresor, news, sofa, aislamiento
CENSO = [
    ("U63", "UCI Torre B", "Si", "EP-900063", "2026-10-18", "2026-10-19", "UCI", "Si", "Si", "9", "11", ""),
    ("U64", "UCI Torre B", "Si", "EP-900064", "2026-10-20", "2026-10-21", "UCI", "No", "No", "", "", ""),  # R11
    ("U65", "UCI Torre B", "Si", "EP-900065", "2026-10-22", "2026-10-22", "UCI", "No", "No", "5", "6", ""),  # R06
    ("U66", "UCI Torre B", "Si", "EP-900066", "2026-10-10", "2026-10-11", "UCI", "No", "No", "3", "2", ""),  # R02 R14 R16
    ("U67", "UCI Torre B", "Si", "EP-900067", "2026-10-23", "2026-10-23", "UCI", "No", "Si", "7", "8", ""),  # R04 R15
    ("U68", "UCI Torre B", "Si", "EP-900068", "2026-10-24", "2026-10-24", "UCI", "No", "No", "4", "5", ""),  # R12
    ("U69", "UCI Torre B", "No", "", "", "", "", "", "", "", "", ""),  # R13 (cargo sin cama)
    ("U70", "UCI Torre B", "Si", "EP-900070", "2026-10-15", "2026-10-16", "UCI", "Si", "No", "6", "35%", ""),  # R11
    ("U71", "UCI Torre B", "Si", "EP-900071", "2026-10-19", "2026-10-20", "UCI", "No", "No", "4", "4", ""),  # R09 autorización
    ("U72", "UCI Torre B", "Si", "EP-900072", "2026-08-28", "2026-08-29", "UCI", "Si", "No", "6", "9", "Contacto"),  # R08 R09
    ("U73", "UCI Torre C", "Si", "EP-900073", "2026-10-24", "2026-10-25", "UCIN", "No", "No", "3", "3", ""),
    ("U74", "UCI Torre C", "Si", "EP-900074", "2026-10-12", "2026-10-13", "Piso", "No", "No", "2", "1", ""),  # R14
    ("U75", "UCI Torre C", "Si", "EP-900075", "2026-10-17", "2026-10-18", "UCI", "Si", "Si", "8", "10", ""),  # R01 R02 R03 R04
    ("U76", "UCI Torre C", "Si", "EP-900076", "2026-10-21", "2026-10-21", "UCI", "No", "No", "5", "4", ""),  # R10
    ("U77", "UCI Torre C", "Si", "EP-900077", "2026-10-22", "2026-10-22", "UCI", "No", "No", "4", "3", ""),  # R15 fecha
    ("U78", "UCI Torre C", "Si", "EP-900078", "2026-10-09", "2026-10-20", "UCI", "No", "No", "5", "5", ""),  # R07
    ("U79", "UCI Torre C", "Si", "EP-900079", "2026-10-25", "2026-10-25", "UCI", "No", "No", "6", "7", ""),  # R05 sin entrega
    ("U80", "UCI Torre C", "No", "", "", "", "", "", "", "", "", ""),  # R05 entrega sin cama
    ("U81", "UCI Torre C", "Si", "EP-900081", "2026-09-28", "2026-09-30", "UCI", "No", "No", "3", "4", ""),
    ("U82", "UCI Torre C", "Si", "EP-900082", "2026-10-20", "2026-10-21", "UCI", "Si", "Si", "8", "12", ""),  # R03
]

CARGOS_SIN_ESTANCIA = {"U68", "U80"}
CONCEPTO_ESPECIAL = {
    "U66": "ESTANCIA UCI ADULTOS",
    "U74": "ESTANCIA CUIDADO INTERMEDIO",
    "U73": "ESTANCIA CUIDADO INTERMEDIO",
}

TORRE_B = """ENTREGA DE TURNO DE ENFERMERÍA (formato estructurado)
Estación: UCI Torre B
Fecha: 26/10/2026
Turno: Noche

CAMA: U63
Episodio: EP-900063
Ingreso UCI: 19/10/2026
Condición: UCI
Ventilación: VMI por tubo orotraqueal
Vasoactivos: Norepinefrina 0,2 mcg/kg/min
TRR: No
Dispositivos: CVC 22/10/2026 (justificación: vasopresor); Línea arterial 19/10/2026 (justificación: monitoreo invasivo)
Antibióticos: Meropenem día 4
Aislamiento: Ninguno
Pendientes:
- [resultado] Hemocultivos (solicitado 25/10/2026)
Nota: Sin cambios relevantes durante la noche.

CAMA: U64
Episodio: EP-900064
Ingreso UCI: 21/10/2026
Condición: UCI
Ventilación: Cánula nasal 2 L
Vasoactivos: Ninguno
TRR: No
Dispositivos: Sonda vesical 21/10/2026 (justificación: balance estricto)
Antibióticos: Ninguno
Aislamiento: Ninguno
Pendientes:
Nota: Estable.

CAMA: U65
Episodio: EP-900056
Ingreso UCI: 22/10/2026
Condición: UCI
Ventilación: Aire ambiente
Vasoactivos: Ninguno
TRR: No
Dispositivos:
Antibióticos: Ninguno
Aislamiento: Ninguno
Pendientes:
Nota: Pendiente valoración por cardiología.

CAMA: U66
Episodio: EP-900066
Ingreso UCI: 11/10/2026
Condición: Piso
Ventilación: Aire ambiente
Vasoactivos: Ninguno
TRR: No
Dispositivos:
Antibióticos: Ninguno
Aislamiento: Ninguno
Pendientes:
- [traslado] Traslado a piso sin cama asignada (solicitado 24/10/2026)
Nota: Condición piso desde hace dos días, en espera de cama.

CAMA: U67
Episodio: EP-900067
Ingreso UCI: 23/10/2026
Condición: UCI
Ventilación: Cánula de alto flujo
Vasoactivos: Ninguno
TRR: No
Dispositivos: CVC 23/10/2026
Antibióticos: Piperacilina tazobactam día 3
Aislamiento: Ninguno
Pendientes:
Nota: Hipotensión a las 02:00, se inicia norepinefrina a 0,05 con mejoría.

CAMA: U68
Episodio: EP-900068
Ingreso UCI: 24/10/2026
Condición: UCI
Ventilación: Cánula nasal 3 L
Vasoactivos: Ninguno
TRR: No
Dispositivos: Línea arterial 24/10/2026 (justificación: gases seriados)
Antibióticos: Ninguno
Aislamiento: Ninguno
Pendientes:
Nota: Posoperatorio de cirugía abdominal, estable.

CAMA: U70
Episodio: EP-900070
Ingreso UCI: 16/10/2026
Condición: UCI
Ventilación: VMI
Vasoactivos: Ninguno
TRR: No
Dispositivos: CVC 16/10/2026 (justificación: nutrición parenteral)
Antibióticos: Vancomicina día 6
Aislamiento: Ninguno
Pendientes:
Nota: Se suspendió norepinefrina ayer.

CAMA: U71
Episodio: EP-900071
Ingreso UCI: 20/10/2026
Condición: UCI
Ventilación: Aire ambiente
Vasoactivos: Ninguno
TRR: No
Dispositivos: PICC 20/10/2026 (justificación: quimioterapia)
Antibióticos: Ninguno
Aislamiento: Ninguno
Pendientes:
- [autorización] Quimioterapia pendiente de autorización de la EPS (solicitado 23/10/2026)
Nota: Paciente oncológico estable, en espera de autorización.

CAMA: U72
Episodio: EP-900072
Ingreso UCI: 29/08/2026
Condición: UCI
Ventilación: Traqueostomía conectada a ventilador (VMI)
Vasoactivos: Ninguno
TRR: Sí, hemodiálisis intermitente
Dispositivos: Mahurkar femoral 05/09/2026; CVC 12/10/2026
Antibióticos: Ceftazidima avibactam día 9
Aislamiento: Contacto
Pendientes:
- [resultado] Lavado broncoalveolar (solicitado 09/10/2026)
Nota: Estancia prolongada. Sr. Pedro Gomez (hijo) solicita reunión, celular 3109876543.
"""

TORRE_C = """ENTREGA DE TURNO DE ENFERMERÍA (formato estructurado)
Estación: UCI Torre C
Contraseña equipo: XXXXXXXX
Fecha: 26/10/2026
Turno: Noche

CAMA: U73
Episodio: EP-900073
Ingreso UCI: 25/10/2026
Condición: UCIN
Ventilación: Cánula nasal 1 L
Vasoactivos: Ninguno
TRR: No
Dispositivos:
Antibióticos: Ninguno
Aislamiento: Ninguno
Pendientes:
Nota: Hija informada por teléfono, Sra. Martha Lopez, CC 12345678.

CAMA: U74
Episodio: EP-900074
Ingreso UCI: 13/10/2026
Condición: Piso
Ventilación: Aire ambiente
Vasoactivos: Ninguno
TRR: No
Dispositivos:
Antibióticos: Ninguno
Aislamiento: Ninguno
Pendientes:
- [traslado] Sin cama asignada en hospitalización (solicitado 25/10/2026)
Nota: Espera cama de piso.

CAMA: U75
Episodio: EP-900075
Ingreso UCI: 18/10/2026
Condición: Piso
Ventilación: Cánula nasal 2 L
Vasoactivos: Ninguno
TRR: No
Dispositivos: Sonda vesical 18/10/2026
Antibióticos: Ninguno
Aislamiento: Ninguno
Pendientes:
- [traslado] Traslado a piso sin cama asignada (solicitado 25/10/2026)
Nota: Tolera vía oral, deambula con ayuda.

CAMA: U76
Episodio: EP-900076
Ingreso UCI: 21/10/2026
Condición: UCI
Ventilación: Aire ambiente
Vasoactivos: Ninguno
TRR: No
Dispositivos:
Antibióticos: Meropenem día 2
Aislamiento: Contacto por Klebsiella BLEE
Pendientes:
Nota: Aislamiento instaurado esta noche.

CAMA: U77
Episodio: EP-900077
Ingreso UCI: 22/10/2026
Condición: UCI
Ventilación: Aire ambiente
Vasoactivos: Ninguno
TRR: No
Dispositivos:
Antibióticos: Ninguno
Aislamiento: Ninguno
Pendientes:
- [procedimiento] Ecocardiograma (solicitado 26-10-36)
Nota: Estable.

CAMA: U78
Episodio: EP-900078
Ingreso UCI: 10/10/2026
Condición: UCI
Ventilación: Aire ambiente
Vasoactivos: Ninguno
TRR: No
Dispositivos:
Antibióticos: Ninguno
Aislamiento: Ninguno
Pendientes:
Nota: Estable.

CAMA: U80
Episodio: EP-900080
Ingreso UCI: 26/10/2026
Condición: UCI
Ventilación: Aire ambiente
Vasoactivos: Ninguno
TRR: No
Dispositivos:
Antibióticos: Ninguno
Aislamiento: Ninguno
Pendientes:
Nota: Ingreso de la madrugada desde urgencias.

CAMA: U81
Episodio: EP-900081
Ingreso UCI: 30/09/2026
Condición: UCI
Ventilación: Aire ambiente
Vasoactivos: Ninguno
TRR: No
Dispositivos: CVC 01/10/2026 (justificación: nutrición parenteral)
Antibióticos: Ninguno
Aislamiento: Ninguno
Pendientes:
Nota: Estable.

CAMA: U82
Episodio: EP-900082
Ingreso UCI: 21/10/2026
Condición: UCI
Ventilación: Cánula de alto flujo
Vasoactivos: Vasopresina 0,03 U/min
TRR: No
Dispositivos: CVC 21/10/2026 (justificación: vasopresor)
Antibióticos: Cefepime día 5
Aislamiento: Ninguno
Pendientes:
Nota: Sin tubo orotraqueal, alto flujo 50 L.
"""


def generar(salida: str | Path) -> None:
    salida = Path(salida)
    correos = salida / "correos"
    correos.mkdir(parents=True, exist_ok=True)

    with open(salida / "orbit_censo.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["fecha", "cama", "unidad", "ocupada", "episodio", "fecha_ingreso_hospital", "fecha_ingreso_uci",
                    "criterio_atencion", "vmi", "vasopresor", "news", "sofa", "aislamiento"])
        for cama, unidad, ocupada, ep, ih, iu, crit, vmi, vaso, news, sofa, ais in CENSO:
            w.writerow([FECHA, cama, unidad, ocupada, ep, ih, iu, crit, vmi, vaso, news, sofa, ais])

    with open(salida / "cargos.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["fecha", "episodio", "cama", "concepto", "valor"])
        for cama, _, ocupada, ep, *_ in CENSO:
            if ocupada == "Si" and cama not in CARGOS_SIN_ESTANCIA:
                concepto = CONCEPTO_ESPECIAL.get(cama, "ESTANCIA UCI ADULTOS")
                w.writerow([FECHA, ep, cama, concepto, 1230000 if "UCI" in concepto else 780000])
            if ocupada == "Si":
                w.writerow([FECHA, ep, cama, "MEDICAMENTOS", 350000])
        w.writerow([FECHA, "EP-900069", "U69", "ESTANCIA UCI ADULTOS", 1230000])

    # Torre B en el cuerpo del correo; Torre C como adjunto .txt, como suelen llegar.
    b = EmailMessage()
    b["Subject"] = "Entrega de turno UCI Torre B noche 26/10"
    b["From"] = "enfermeria.torreb@ejemplo.invalid"
    b["To"] = "coordinacion.uci@ejemplo.invalid"
    b["Date"] = "Mon, 27 Oct 2026 06:45:00 -0500"
    b.set_content(TORRE_B)
    (correos / "torre_b.eml").write_bytes(bytes(b))

    c = EmailMessage()
    c["Subject"] = "ENTREGA DE TURNO TORRE C"
    c["From"] = "enfermeria.torrec@ejemplo.invalid"
    c["To"] = "coordinacion.uci@ejemplo.invalid"
    c["Date"] = "Mon, 27 Oct 2026 06:50:00 -0500"
    c.set_content("Buenos días, adjunto la entrega de turno.")
    c.add_attachment(TORRE_C.encode("utf-8"), maintype="text", subtype="plain", filename="entrega_torre_c.txt")
    (correos / "torre_c.eml").write_bytes(bytes(c))

    otro = EmailMessage()
    otro["Subject"] = "Menú de la cafetería"
    otro["From"] = "cafeteria@ejemplo.invalid"
    otro.set_content("No es una entrega de turno.")
    (correos / "otro.eml").write_bytes(bytes(otro))


if __name__ == "__main__":
    generar(Path(__file__).parent / "2026-10-26")
