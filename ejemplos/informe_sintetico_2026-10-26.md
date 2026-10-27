# UCI CONTROL AI · Informe del 26/10/2026

_Priorización por reglas (sin IA)._

## Resumen

18 de 20 camas ocupadas (90.0%). Según enfermería: 14 en condición UCI, 1 UCIN y 3 en condición piso (16.7%). 5 con ventilación invasiva y 4 con soporte vasoactivo. El motor encontró 2 discrepancias críticas, 12 altas, 12 medias y 2 bajas. Estancia evitable estimada del día: $3.980.000; derecho de cama sin cargar: $1.230.000.

## Alertas

- **CRITICA**: Alerta en el correo de entrega. UCI Torre C: El correo trae una credencial en texto plano (Contraseña equipo); se descartó sin guardarla.
- **CRITICA** (U65): Número de ingreso distinto entre fuentes. El episodio de la entrega no coincide con el de ORBIT: posible paciente equivocado o cama mal digitada.

## Indicadores

| Indicador | Valor |
|---|---|
| Camas ocupadas | 18 de 20 (90.0%) |
| Camas ocupadas con entrega de turno | 17 |
| Condición UCI / UCIN / piso | 14 / 1 / 3 (16.7% piso) |
| Ventilación invasiva | 5 |
| Soporte vasoactivo | 4 |
| Terapia de reemplazo renal | 1 |
| Dispositivos con más de 7 días | 5 |
| Discrepancias (crítica / alta / media / baja) | 2 / 12 / 12 / 2 |
| Estancia evitable del día (estimada) | $3.980.000 |
| Derecho de cama sin cargar | $1.230.000 |
| Estancia UCI en riesgo de glosa | $2.460.000 |

_Costo día UCI $1.990.000 y tarifa de cama $1.230.000 (promedios de la ejecución presupuestal de septiembre; no son costos por episodio)._

## Prioridades del día

| # | Cama | Acción | Responsable | Plazo | Motivo | Ref. |
|---|---|---|---|---|---|---|
| 1 | UNIDAD | Retirar las credenciales del formato de entrega y cambiarlas | Sistemas | Inmediato | Alerta en el correo de entrega. | D001 |
| 2 | U65 | Verificar la identidad del paciente y el número de ingreso | Enfermería | Inmediato | Número de ingreso distinto entre fuentes. | D002 |
| 3 | U75 | Confirmar en la ronda si el paciente está realmente en condición piso y aclarar el soporte | Médico UCI | Inmediato | Condición piso con soporte activo. | D008 |
| 4 | U75 | Verificar la ventilación al lado de la cama y corregir la fuente errada | Enfermería | Inmediato | Ventilación invasiva discordante. | D009 |
| 5 | U75 | Verificar el soporte vasoactivo al lado de la cama y corregir la fuente errada | Enfermería | Inmediato | Soporte vasoactivo discordante. | D010 |
| 6 | U67 | Verificar el soporte vasoactivo al lado de la cama y corregir la fuente errada | Enfermería | Inmediato | Soporte vasoactivo discordante. | D004 |
| 7 | U76 | Confirmar el aislamiento, señalizar y corregir la fuente | Infectología / PROA | Inmediato | Aislamiento declarado en una sola fuente. | D012 |
| 8 | U82 | Verificar la ventilación al lado de la cama y corregir la fuente errada | Enfermería | Inmediato | Ventilación invasiva discordante. | D014 |
| 9 | U75 | Revisar la pertinencia del cargo de estancia UCI | Facturación | Hoy | Estancia UCI facturada a paciente en condición piso. | D011 |
| 10 | U66 | Revisar la pertinencia del cargo de estancia UCI | Facturación | Hoy | Estancia UCI facturada a paciente en condición piso. | D003 |
| 11 | U72 | Evaluar el retiro del dispositivo o registrar por qué sigue indicado | Médico UCI | Hoy | Dispositivo prolongado sin justificación. | D007, D019 |
| 12 | U68 | Cargar la estancia del día | Facturación | Hoy | Cama ocupada sin cargo de estancia. | D005 |
| 13 | U71 | Escalar el pendiente con el responsable | Autorizaciones | Hoy | Autorización sin resolver. | D006 |
| 14 | U80 | Confirmar la ocupación real y completar el censo o la entrega | Enfermería | Hoy | Entrega de una cama que no está ocupada en ORBIT. | D013 |
| 15 | U75 | Unificar el nivel de atención en ORBIT y en la entrega | Médico UCI | Antes de las 10 am | Nivel de atención distinto entre fuentes. | D022 |
| 16 | U66 | Unificar el nivel de atención en ORBIT y en la entrega | Médico UCI | Antes de las 10 am | Nivel de atención distinto entre fuentes. | D015 |
| 17 | U66 | Llevar a la ronda de flujo de las 10 am y gestionar cama de piso | Gestor de camas | Antes de las 10 am | Paciente en condición piso ocupando cama UCI. | D016 |
| 18 | U74 | Llevar a la ronda de flujo de las 10 am y gestionar cama de piso | Gestor de camas | Antes de las 10 am | Paciente en condición piso ocupando cama UCI. | D021 |
| 19 | U75 | Evaluar el retiro del dispositivo o registrar por qué sigue indicado | Médico UCI | Hoy | Dispositivo prolongado sin justificación. | D023 |
| 20 | U67 | Corregir la entrega de turno | Enfermería | Hoy | Inconsistencia dentro de la entrega. | D017 |
| 21 | U72 | Escalar el pendiente con el responsable | Médico UCI | Hoy | Pendiente vencido. | D020 |
| 22 | U77 | Corregir la entrega de turno | Enfermería | Hoy | Inconsistencia dentro de la entrega. | D024 |
| 23 | U78 | Corregir la fecha de ingreso a UCI | Enfermería | Hoy | Fecha de ingreso a UCI incoherente. | D025 |
| 24 | U79 | Confirmar la ocupación real y completar el censo o la entrega | Enfermería | Hoy | Cama ocupada sin entrega de turno. | D026 |
| 25 | U69 | Revisar el cargo de estancia sin cama ocupada | Facturación | Esta semana | Cargo de estancia sin cama ocupada. | D018 |
| 26 | U64 | Registrar NEWS y SOFA como puntaje | Médico UCI | Hoy | Escalas de gravedad incompletas. | D027 |
| 27 | U70 | Registrar NEWS y SOFA como puntaje | Médico UCI | Hoy | Escalas de gravedad incompletas. | D028 |

## Discrepancias detectadas

| Id | Severidad | Cama | Regla | Detalle | Impacto |
|---|---|---|---|---|---|
| D001 | critica | Unidad | R17 · Alerta en el correo de entrega | UCI Torre C: El correo trae una credencial en texto plano (Contraseña equipo); se descartó sin guardarla. | — |
| D002 | critica | U65 | R06 · Número de ingreso distinto entre fuentes | El episodio de la entrega no coincide con el de ORBIT: posible paciente equivocado o cama mal digitada. | — |
| D003 | alta | U66 | R16 · Estancia UCI facturada a paciente en condición piso | Se cargó «ESTANCIA UCI ADULTOS» y enfermería reporta condición piso: riesgo de glosa por pertinencia. | $1.230.000 |
| D004 | alta | U67 | R04 · Soporte vasoactivo discordante | ORBIT: vasopresor sí. Entrega: sin vasoactivos. | — |
| D005 | alta | U68 | R12 · Cama ocupada sin cargo de estancia | ORBIT la registra ocupada el 26/10 y facturación no tiene cargo de estancia para ese día. | $1.230.000 |
| D006 | alta | U71 | R09 · Autorización sin resolver | [autorización] Quimioterapia pendiente de autorización de la EPS: 3 días desde la solicitud (límite 24 h). | $5.970.000 |
| D007 | alta | U72 | R08 · Dispositivo prolongado sin justificación | Mahurkar femoral: 51 días desde la inserción y sin necesidad registrada. | — |
| D008 | alta | U75 | R01 · Condición piso con soporte activo | Enfermería reporta condición piso, pero hay vasopresor en ORBIT y VMI en ORBIT. Confirmar antes de trasladar. | — |
| D009 | alta | U75 | R03 · Ventilación invasiva discordante | ORBIT: VMI sí. Entrega: Cánula nasal 2 L. | — |
| D010 | alta | U75 | R04 · Soporte vasoactivo discordante | ORBIT: vasopresor sí. Entrega: sin vasoactivos. | — |
| D011 | alta | U75 | R16 · Estancia UCI facturada a paciente en condición piso | Se cargó «ESTANCIA UCI ADULTOS» y enfermería reporta condición piso: riesgo de glosa por pertinencia. | $1.230.000 |
| D012 | alta | U76 | R10 · Aislamiento declarado en una sola fuente | Entrega: Contacto por Klebsiella BLEE. ORBIT: sin aislamiento. | — |
| D013 | alta | U80 | R05 · Entrega de una cama que no está ocupada en ORBIT | La entrega reporta paciente en esta cama, pero la cama figura libre en ORBIT. | — |
| D014 | alta | U82 | R03 · Ventilación invasiva discordante | ORBIT: VMI sí. Entrega: Cánula de alto flujo. | — |
| D015 | media | U66 | R02 · Nivel de atención distinto entre fuentes | ORBIT registra criterio UCI; la entrega dice PISO. | — |
| D016 | media | U66 | R14 · Paciente en condición piso ocupando cama UCI | Candidato para la ronda de flujo: cada día en esta condición es estancia evitable. | $1.990.000 |
| D017 | media | U67 | R15 · Inconsistencia dentro de la entrega | La nota menciona norepinefrina pero el campo de vasoactivos no lo registra. | — |
| D018 | media | U69 | R13 · Cargo de estancia sin cama ocupada | Facturación tiene 1 cargo(s) de estancia, pero la cama no figura ocupada en ORBIT. | $1.230.000 |
| D019 | media | U72 | R08 · Dispositivo prolongado sin justificación | CVC: 14 días desde la inserción y sin necesidad registrada. | — |
| D020 | media | U72 | R09 · Pendiente vencido | [resultado] Lavado broncoalveolar: 17 días desde la solicitud (límite 72 h). | — |
| D021 | media | U74 | R14 · Paciente en condición piso ocupando cama UCI | Candidato para la ronda de flujo: cada día en esta condición es estancia evitable. | $1.990.000 |
| D022 | media | U75 | R02 · Nivel de atención distinto entre fuentes | ORBIT registra criterio UCI; la entrega dice PISO. | — |
| D023 | media | U75 | R08 · Dispositivo prolongado sin justificación | Sonda vesical: 8 días desde la inserción y sin necesidad registrada. | — |
| D024 | media | U77 | R15 · Inconsistencia dentro de la entrega | Pendiente «Ecocardiograma»: fecha fuera de rango, posible error de digitación: «26-10-36» | — |
| D025 | media | U78 | R07 · Fecha de ingreso a UCI incoherente | La fecha de ingreso a UCI difiere 10 días entre la entrega y ORBIT. | — |
| D026 | media | U79 | R05 · Cama ocupada sin entrega de turno | ORBIT la registra ocupada y ninguna entrega del turno la menciona. | — |
| D027 | baja | U64 | R11 · Escalas de gravedad incompletas | NEWS vacío; SOFA vacío. Sin SOFA no se puede calcular el SMR. | — |
| D028 | baja | U70 | R11 · Escalas de gravedad incompletas | SOFA con formato inválido («35%»; se espera un puntaje de 0 a 24). Sin SOFA no se puede calcular el SMR. | — |
