# UCI CONTROL AI

Agente de coordinación para la UCI de adultos. Lee tres fuentes, las concilia por cama, episodio y fecha, señala discrepancias y entrega un informe diario priorizado.

```
ORBIT (censo, camas)      ─┐
Gmail (entregas de turno) ─┼─► Motor de conciliación ─► UCI CONTROL AI ─► Informe diario
Facturación (cargos)      ─┘   (16 reglas)               (Claude + verificación)   (Markdown + JSON)
```

## Cómo está armado

| Pieza | Archivo | Qué hace |
|---|---|---|
| Conector ORBIT | `uci_control/conectores/orbit.py` | Lee la exportación CSV del censo. Los nombres de columna se buscan entre alias, así que adaptar el formato real es editar un diccionario. |
| Conector Gmail | `uci_control/conectores/gmail.py` | Lee los correos "Entrega de turno" desde archivos .eml o desde la API de Gmail en solo lectura. Extrae el cuerpo y los adjuntos .txt y .odt. Los .doc quedan marcados como no leídos. |
| Parser de entregas | `uci_control/conectores/entregas.py` | Convierte la entrega en datos por cama: condición, ventilación, vasoactivos, TRR, dispositivos con fecha de inserción, antibióticos, aislamiento y pendientes tipificados. Revisa el texto libre para encontrar soportes que el campo estructurado no dice. Descarta las líneas con contraseñas sin guardarlas. |
| Conector de costos | `uci_control/conectores/costos.py` | Lee los cargos de facturación (CSV) y reconoce los de estancia. |
| Motor de conciliación | `uci_control/conciliacion/` | Une las fuentes por cama (y por episodio para los cargos) y aplica las reglas. |
| Indicadores | `uci_control/indicadores.py` | Ocupación, condición, soportes, días-dispositivo, pendientes y cifras financieras del día. |
| Orquestador | `uci_control/orquestador.py` | Pide a Claude el resumen y las prioridades con salida estructurada y verifica que cada prioridad cite camas y discrepancias reales. Si Claude no está disponible, prioriza con reglas. |
| Privacidad | `uci_control/privacidad.py` | Retira nombres, documentos, teléfonos, correos y credenciales del texto libre. A Claude solo viaja la cama, nunca el número de ingreso. |

## Predicción y sugerencias de gestión

| Pieza | Archivo | Qué hace |
|---|---|---|
| Tablero ORBIT | `uci_control/conectores/orbit_tablero.py` | Lee el "Reporte_TableroControlUCI1" (29 columnas): escalas, soportes, criterio, antimicrobianos con fecha de inicio, fecha de intubación y pendientes del texto libre. El documento se reemplaza por un hash con sal (`UCI_SAL_SEUDONIMO`). |
| Riesgo por paciente | `uci_control/prediccion/riesgo.py` | Mortalidad (SOFA), deterioro (NEWS2 y soportes), estancia prolongada, infección asociada a la atención y egreso próximo, cada uno con los factores que pesaron. Estima los días que faltan y su valor según el agrupador diagnóstico del libro de costos. |
| Sugerencias | `uci_control/prediccion/sugerencias.py` | Interconsultas y acciones con responsable y prioridad: traslado a piso, traqueostomía, PROA, nutrición, fonoaudiología, movilización, PICS, analgesia, paliativos, trabajo social, autorizaciones y pendientes enrutados. |
| Demanda y capacidad | `uci_control/prediccion/demanda.py` | Métricas de la trazabilidad de camas, modelo logístico de rechazo de solicitudes (AUC por validación cruzada) y escenarios Erlang-B de capacidad. |
| Pronóstico | `uci_control/prediccion/pronostico.py` | Holt con tendencia amortiguada e intervalo del 90% para series mensuales. |

```bash
python -m uci_control riesgo --tablero Reporte_TableroControlUCI1.xlsx --salida salida/
# o sumarlo al informe diario: informe ... --tablero Reporte_TableroControlUCI1.xlsx
```

Los puntajes son reglas a partir de escalas validadas; no están calibrados con desenlaces de la clínica hasta tener egreso, muerte y estancia final por episodio.

## Gestión de camas y egreso oportuno

`python -m uci_control camas --tablero ...` (y la pestaña "Camas y egresos" de la corrida diaria) dice quién cumple criterios de alta hoy, qué barrera lo frena y con quién escalarla, cuántas camas se liberan frente a los ingresos esperados y en qué nivel de escalamiento está la unidad. Se basa en la guía SCCM 2016, SAFER y Red2Green del NHS, Real-Time Demand Capacity del IHI, OPEL y el SWIFT. Detalle y referencias: [docs/GESTION_CAMAS.md](docs/GESTION_CAMAS.md).

## Corrida diaria y seguimiento

`python -m uci_control diario` lee del buzón las entregas y los tableros de ORBIT, genera el informe (Markdown, JSON y HTML interactivo), registra las sugerencias en una base de seguimiento y, con `--enviar`, lo manda por correo. El equipo marca cada sugerencia como hecha o no aplica (`seguimiento marcar` o el Excel de pendientes) y el informe muestra cuántos días lleva abierta cada una y la adherencia por servicio. Instalación, acceso a Gmail y programación: [docs/OPERACION.md](docs/OPERACION.md).

## Reglas de discrepancia

| Regla | Qué detecta | Severidad |
|---|---|---|
| R01 | Condición piso con soporte vasoactivo o VMI en cualquier fuente | Alta |
| R02 | Nivel de atención distinto entre ORBIT y la entrega | Media |
| R03 | Ventilación invasiva distinta entre fuentes | Alta |
| R04 | Soporte vasoactivo distinto entre fuentes | Alta |
| R05 | Cama ocupada sin entrega, entrega sin cama ocupada o cama en dos entregas | Media / Alta |
| R06 | Número de ingreso distinto entre fuentes | Crítica |
| R07 | Fecha de ingreso a UCI incoherente | Media |
| R08 | Dispositivo de más de 7 días sin justificación (más de 30: alta) | Media / Alta |
| R09 | Pendiente de más de 72 h; autorización de más de 24 h | Media / Alta |
| R10 | Aislamiento declarado en una sola fuente | Alta |
| R11 | NEWS o SOFA vacíos o con formato inválido (por ejemplo, un SOFA "35%"; el rango "32-35%" del tablero se acepta) | Baja |
| R12 | Cama ocupada sin cargo de estancia | Alta |
| R13 | Cargo de estancia sin cama ocupada | Media |
| R14 | Paciente en condición piso, sin soporte, ocupando cama UCI | Media |
| R15 | Inconsistencia dentro de la entrega (texto libre contra campo, fechas mal digitadas) | Media |
| R16 | Estancia UCI facturada a paciente en condición piso (riesgo de glosa) | Alta |
| R17 | Credenciales en el correo de entrega o adjuntos sin leer | Crítica / Media |

Agregar una regla es escribir una función en `uci_control/conciliacion/reglas.py` y sumarla a `REGLAS`.

## Empezar en Visual Studio Code

```bash
git clone https://github.com/camilogomezc123/UCI-IA.git
cd UCI-IA
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements-gmail.txt
code .
```

En VS Code: instalar las extensiones recomendadas (Python y Claude Code), elegir el intérprete `.venv`, y en "Ejecutar y depurar" correr primero **Demo: generar datos sintéticos** y luego **Demo: corrida diaria con datos sintéticos**. El informe queda en `demo/salida/uci_2026-10-26.html`. Las pruebas están en la pestaña de pruebas o con `python -m pytest`.

## Uso

```bash
pip install -r requirements.txt

# Datos sintéticos de un día (26/10/2026)
python datos_prueba/generar.py

# Informe solo con reglas
python -m uci_control informe --fecha 2026-10-26 \
  --orbit datos_prueba/2026-10-26/orbit_censo.csv \
  --correos datos_prueba/2026-10-26/correos \
  --costos datos_prueba/2026-10-26/cargos.csv \
  --salida salida --sin-ia

# Con Claude: definir ANTHROPIC_API_KEY y quitar --sin-ia
python -m pytest
```

El modelo por defecto es `claude-opus-5-5` (se cambia con `UCI_MODELO_CLAUDE`). Las cifras financieras de `uci_control/config.py` son ilustrativas. Las de la institución se cargan desde un JSON fuera del repositorio con `UCI_PARAMETROS=/ruta/parametros_institucionales.json`.

## Datos y privacidad

- Este repositorio solo contiene datos sintéticos. Ningún paciente, número de ingreso, nombre o teléfono de `datos_prueba/` es real.
- No se guardan credenciales en el código. Las líneas con contraseñas que lleguen en un correo se descartan y generan una alerta.
- La carga que se envía a Claude identifica a cada paciente solo por cama y lleva el texto libre depurado. El retiro automático de nombres es una protección adicional, no un reemplazo de no escribirlos en la entrega.
- Antes de usarlo con datos reales hay que definir con la institución dónde corre, quién accede al informe y cómo se audita, y verificar el tratamiento de datos de salud con la Ley 1581 de 2012.

## Pendiente

- Ajustar el conector ORBIT al formato real de la exportación del panel.
- Adaptar el parser al formato UCIFM041 actual (tabla de dispositivos, Padua y condición) o adoptar la plantilla estructurada propuesta.
- Leer adjuntos .doc.
- Serie histórica con los tableros ORBIT y exportación estructurada de laboratorios, cultivos, antibióticos, balance de líquidos y desenlaces, para calibrar los modelos.
