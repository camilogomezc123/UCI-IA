# Gestión de camas y egreso oportuno

El módulo `uci_control/camas/` responde cada mañana tres preguntas: quién puede salir hoy de la UCI, qué lo está frenando y si las camas que se liberan alcanzan para los ingresos que vienen. Toma modelos que ya usan hospitales de otros países y los aplica al tablero de ORBIT.

## Modelos en que se basa

| Modelo | De dónde viene | Qué toma el agente |
|---|---|---|
| Guía de admisión, egreso y triage de UCI | Society of Critical Care Medicine, 2016 ([Nates et al., Crit Care Med](https://doi.org/10.1097/CCM.0000000000001856)) | El paciente sale de la UCI cuando su estado fisiológico se estabilizó y ya no necesita monitoreo ni intervenciones de UCI. Base de los criterios de alta y del triage en nivel 4 |
| SAFER patient flow bundle | NHS Improvement, Reino Unido ([guía rápida](https://fabnhsstuff.net/fab-stuff/the-safer-patient-flow-bundle)) | Fecha esperada de egreso y criterios clínicos de alta para cada paciente; revisión senior antes del mediodía; meta de un tercio de los egresos antes del mediodía; revisión diaria de pacientes estancados (7 días o más) |
| Red2Green | NHS, Reino Unido ([HFMA](https://hfma.org.uk/articles/hfma-2017-colour-scheme-supports-flow-improvement)) | Un día "rojo" es un día en que el paciente no recibe nada que lo acerque al egreso. Aquí: un día en que el paciente ya cumplía criterios de alta y siguió en la cama UCI |
| Real-Time Demand Capacity (RTDC) | Institute for Healthcare Improvement ([Barnes et al., JAMIA 2016](https://pmc.ncbi.nlm.nih.gov/articles/PMC4954620)); usado por [Radboudumc](https://www.radboudumc.nl/en/about-radboudumc/organization/adviesgroep-pvi/real-time-demand-and-capacity-management-rtdc) | Cada mañana: predecir capacidad (egresos), predecir demanda (ingresos), comparar y armar el plan con los pacientes a egresar primero |
| OPEL | NHS England ([marco 2024-2026](https://www.england.nhs.uk/long-read/integrated-opel-framework-2024-to-2026/)) | Niveles de escalamiento del 1 al 4 con pocas señales objetivas y acciones definidas por nivel |
| SWIFT | Gajic et al., Crit Care Med 2008 ([doi](https://doi.org/10.1097/CCM.0B013E318164E3B0)) | Riesgo de reingreso a UCI al egreso: origen del ingreso, días de UCI, PaO2/FiO2, Glasgow y PaCO2. AUC 0,75 en la validación original |
| Hora del egreso | Vollam et al., Intensive Care Med 2018 ([doi](https://doi.org/10.1007/s00134-018-5245-2)) | Egresar de UCI fuera de horario se asocia con más mortalidad hospitalaria (RR 1,39) y más reingreso (RR 1,30). Por eso las acciones piden egreso antes del mediodía y nunca después de las 16:00 |
| Retraso de ingreso a UCI | Cardoso et al., Crit Care 2011 ([doi](https://doi.org/10.1186/cc9975)) | Cada hora de espera por cama UCI se asoció con 1,5% más de riesgo de muerte en UCI. Es la razón clínica, además de la económica, para liberar camas |

## Cómo decide el agente

**Criterios de alta** (`camas/alta.py`), con los datos del tablero:

| Criterio | Tipo |
|---|---|
| Sin soporte vasoactivo | Mayor |
| Sin ventilación invasiva | Mayor |
| Sin alto flujo ni ventilación no invasiva | Mayor |
| Oxígeno de bajo flujo o aire ambiente | Menor |
| NEWS menor de 5 | Menor |
| RASS entre -1 y +1 | Menor |
| Dolor controlado (CPOT menor de 3, EVA de 4 o menos) | Menor |

- **Listo:** cumple todos. Debe salir hoy.
- **Casi:** le falta un solo criterio menor. Se deja todo listo para egresar mañana temprano.
- **No:** sigue necesitando UCI. Su fecha esperada de egreso sale del estimado de días restantes.

El destino es hospitalización cuando ORBIT ya marca criterio de piso y el NEWS es bajo; si no, intermedio. Con SWIFT alto se recomienda intermedio y seguimiento por el equipo de respuesta rápida.

**Barreras** (`camas/barreras.py`): se leen de los pendientes escritos en ORBIT y se codifican, cada una con responsable y escalamiento.

| Código | Barrera | Responsable |
|---|---|---|
| B01 | Espera de cama en hospitalización | Gestor de camas |
| B02 | Autorización del asegurador | Autorizaciones |
| B03 | Remisión a otra institución | Referencia y contrarreferencia |
| B04 | Estudio o procedimiento pendiente | Médico UCI |
| B05 | Valoración de especialista pendiente | Coordinación médica |
| B06 | Familia o red de apoyo | Trabajo social |
| B07 | Requiere habitación de aislamiento | Gestor de camas / infecciones |
| B08 | Dispositivo que el piso debe recibir | Enfermería |
| B00 | Sin barrera registrada | Médico UCI |

**Plan del día** (`camas/capacidad.py`), como en RTDC:

```
brecha = camas libres + egresos esperados − ingresos esperados
```

Los egresos esperados son la suma de la probabilidad de egreso de cada paciente: 0,8 si está listo y ORBIT ya cambió el criterio, 0,6 si está listo pero ORBIT sigue en UCI, 0,35 si está casi y 0,05 si no. Los ingresos esperados son las solicitudes diarias por la proporción que termina en ingreso (configurable con `UCI_PARAMETROS`, o `--demanda` en la línea de comandos).

**Nivel de escalamiento**, como en OPEL:

| Nivel | Cuándo | Acciones |
|---|---|---|
| 1 Normal | Ocupación menor de 85% y brecha de cero o más | Ronda de egresos antes de las 10 a. m. |
| 2 Presión moderada | Ocupación desde 85% o brecha negativa | Comité de camas a las 11 a. m.; destrabar barreras hoy |
| 3 Presión alta | Ocupación mayor de 95%, brecha de -3 o menos, o 10% de las camas con paciente listo | Un responsable por paciente listo; escalar a aseguradores; usar intermedio como puente |
| 4 Crítico | Ocupación de 100%, brecha de -3 o menos y 15% de camas con paciente listo | Plan de contingencia; gerencia y aseguradores el mismo día; triage por gravedad |

**Historial** (`camas/historial.py`): guarda una foto diaria del tablero (cama, seudónimo, estado de alta, fecha esperada, barreras). Desde el segundo día detecta egresos (quien deja de aparecer), cuenta días rojos, marca fechas esperadas vencidas y calcula qué porcentaje de los pacientes listos salió al día siguiente.

## Uso

```bash
# Solo el plan de camas, con uno o varios tableros (uno por torre)
python -m uci_control camas --tablero torre_b.xlsx torre_c.xlsx --camas-totales 66 --demanda 10

# Dentro de la corrida diaria: pestaña "Camas y egresos" del informe y sección en el correo
python -m uci_control diario --gmail-token token.json --camas-totales 66 --enviar coordinacion@clinica.co
```

## Lo que falta para que sea más preciso

- **Hora de egreso real.** El tablero es una foto diaria; con la hora de salida de ORBIT se mide el porcentaje de egresos antes del mediodía (meta SAFER) y los egresos fuera de horario.
- **Gases y Glasgow.** Con PaO2/FiO2, PaCO2 y Glasgow el SWIFT deja de ser parcial.
- **Calibración.** Las probabilidades de egreso (0,8, 0,6, 0,35 y 0,05) son supuestos. Con un mes de historial se reemplazan por las observadas en la clínica; el mismo historial sirve para entrenar un modelo de predicción de egreso como el del estudio RTDC de Barnes.
- **Demanda real.** Con la trazabilidad de solicitudes de cama del día se reemplaza el promedio por la demanda observada por día de la semana y franja horaria (`prediccion/demanda.py`).
