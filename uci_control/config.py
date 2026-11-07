"""Parámetros del agente.

Las cifras financieras de este archivo son ILUSTRATIVAS y redondeadas. Las de la institución
viven fuera del repositorio, en un JSON cuya ruta se da en la variable de entorno
UCI_PARAMETROS (las claves son los mismos nombres de este módulo). Las variables de entorno
UCI_COSTO_DIA, UCI_TARIFA_CAMA y UCI_CONTRIBUCION_DIA también sirven para esos tres valores.
"""

import json
import os

# Modelo de Claude que usa el orquestador.
MODELO_CLAUDE = os.environ.get("UCI_MODELO_CLAUDE", "claude-opus-5-5")

# Cifras financieras de referencia por día-cama de la unidad, en pesos (ilustrativas).
COSTO_DIA_UCI = float(os.environ.get("UCI_COSTO_DIA", 2_000_000))
TARIFA_CAMA_UCI = float(os.environ.get("UCI_TARIFA_CAMA", 1_200_000))
CONTRIBUCION_DIA_UCI = float(os.environ.get("UCI_CONTRIBUCION_DIA", 1_700_000))

# Umbrales de las reglas de conciliación.
DIAS_DISPOSITIVO_REVISAR = 7
DIAS_DISPOSITIVO_CRITICO = 30
HORAS_PENDIENTE_MAX = 72
HORAS_AUTORIZACION_MAX = 24
DIAS_TOLERANCIA_FECHA_INGRESO = 1

# Conceptos de facturación que cuentan como cargo de estancia.
PALABRAS_CARGO_ESTANCIA = ("ESTANCIA", "DERECHO DE CAMA", "DIA CAMA", "DÍA CAMA")
PALABRAS_ESTANCIA_UCI = ("UCI", "CUIDADO INTENSIVO", "INTENSIVA")

# Valor facturado por día-cama según agrupador diagnóstico (ilustrativo). Se usa para estimar
# el valor en juego de cada día de estancia según el diagnóstico principal. Los valores
# reales salen del libro de costo medio de la UCI y se cargan desde UCI_PARAMETROS.
VALOR_DIA_POR_AGRUPADOR = {
    "CARDIOVASCULAR": 2_000_000, "ONCO-HEMATO": 3_000_000, "RESPIRATORIO": 3_000_000,
    "INFECCIOSO": 3_500_000, "GASTRO": 2_700_000, "METABOLICO": 2_400_000, "TRAUMA": 2_800_000,
    "RENAL": 2_400_000, "OBSTETRICO": 1_500_000, "MUSCULOESQUELETICO": 2_800_000,
    "MENTAL": 2_500_000, "PIEL": 2_600_000, "CONGENITO": 1_300_000, "NEUROLOGICO": 3_400_000,
    "OTROS": 2_600_000,
}
VALOR_DIA_UCI_PROMEDIO = 2_500_000
VALOR_EPISODIO_PROMEDIO = 16_000_000
ESTANCIA_MEDIA_UCI = 6.0

# Umbrales del motor de sugerencias.
DIAS_ANTIBIOTICO_REVISAR = 7
DIAS_VMI_TRAQUEOSTOMIA = 10
DIAS_ESTANCIA_PROLONGADA = 14

# Gestión de camas (ilustrativos; los reales por UCI_PARAMETROS).
CAMAS_UCI_TOTAL = 66
SOLICITUDES_CAMA_DIA = 14.0  # demanda media diaria de solicitudes de cama UCI
PROPORCION_SOLICITUDES_ADMITIBLES = 0.7  # parte de las solicitudes que termina en ingreso si hay cama
DIAS_PACIENTE_ESTANCADO = 7  # "stranded" (NHS)
DIAS_PACIENTE_MUY_ESTANCADO = 21  # "super-stranded" (NHS)
SWIFT_UMBRAL_ALTO = 15  # riesgo alto de reingreso a UCI; ajustar con datos locales
HORA_LIMITE_EGRESO = 16  # egresos después de esta hora se asocian a más mortalidad y reingreso


def _cargar_parametros() -> None:
    ruta = os.environ.get("UCI_PARAMETROS")
    if not ruta:
        return
    with open(ruta, encoding="utf-8") as f:
        valores = json.load(f)
    for nombre, valor in valores.items():
        if nombre.isupper() and nombre in globals():
            globals()[nombre] = valor


_cargar_parametros()
