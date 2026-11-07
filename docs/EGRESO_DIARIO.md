# Egreso diario, ocupación a 7 días y aprendizaje del estado diario

Tres piezas responden las preguntas del gestor de camas cada mañana: quién sale hoy de la UCI, cuántas camas vamos a tener ocupadas esta semana y qué tanto se puede afinar eso con lo que dicen el tablero y las entregas de cada día.

| Pieza | Archivo | Aprende de | Sabe |
|---|---|---|---|
| Egreso diario | `prediccion/ocupacion.py` | Consolidado de egresos 2024-2026 | Lo del ingreso, los días que lleva y el día de la semana |
| Ocupación a 7 días | `prediccion/ocupacion.py` | Lo mismo, más las llegadas por día de la semana | Quiénes están hoy y cuántos suelen llegar |
| Estado diario | `camas/aprendizaje.py` | El tablero y las entregas que el agente guarda cada mañana | Soportes, criterios de alta, pendientes y cambios desde ayer |

## Egreso diario (modelo del consolidado)

Cada estancia en UCI de L días se convierte en L filas, una por día, y la última es la del egreso (riesgo discreto en el tiempo). El modelo (árboles con gradiente) estima la probabilidad de que el paciente ya no esté mañana con las variables del tablero al ingreso (edad, sexo, asegurador, diagnóstico, SOFA, Barthel, NEWS), los días que lleva y el día de la semana del egreso.

Para reconstruir el censo diario hay que ubicar el tramo UCI en el calendario. Es exacto en el 75% de los ingresos (secuencias UCI, UCI → hospitalización y hospitalización → UCI). En el resto, los días de piso previos a la UCI se estiman con la proporción de los casos exactos (57%) y los reingresos a UCI se tratan como un solo tramo.

Prueba con días-paciente que el modelo no vio (diciembre 2025 a junio 2026, 12.721 días-paciente y 1.936 egresos de UCI):

| Medida | Modelo | Solo días en UCI y día de la semana |
|---|---|---|
| AUC por día-paciente | 0,68 | 0,57 |
| Brier | 0,118 | 0,129 |

La calibración es buena en los extremos (6% y 39% predicho contra 6% y 39% observado) y sobreestima hasta 5 puntos en los deciles del medio. De los cinco pacientes con mayor probabilidad cada día sale el 40%, frente al 15% de uno cualquiera. Salieron 9,1 pacientes por día; el modelo predijo 9,9 antes de ajustar la deriva (ver abajo).

## Ocupación de las próximas 7 mañanas

Para cada paciente de hoy se multiplica la probabilidad de seguir día a día; a eso se suman las llegadas esperadas (promedio por día de la semana de las últimas 12 semanas) por la probabilidad de que sigan. Con la distribución exacta (suma de Bernoulli más Poisson) salen la ocupación esperada, el intervalo del 80% y la probabilidad de llenar la unidad.

La deriva importa: la estancia cambia con el tiempo y un modelo entrenado con meses viejos subestima la permanencia. Antes de cada pronóstico se corre el logit para que los egresos predichos de las últimas 8 semanas sumen los observados; eso bajó el sesgo a 7 días de -3,4 a -0,8 pacientes. El modelo final se entrega ya ajustado a sus últimas 8 semanas.

Prueba desde 103 días de origen contra el censo reconstruido (media 60 pacientes). La referencia ingenua supone que la ocupación de hoy se mantiene:

| Días adelante | Error del modelo | Error ingenuo | Sesgo | Real dentro del intervalo 80% |
|---|---|---|---|---|
| 1 | 2,8 | 2,9 | -0,4 | 87% |
| 3 | 3,5 | 3,8 | -0,7 | 92% |
| 5 | 4,0 | 4,7 | -0,7 | 92% |
| 7 | 3,9 | 4,1 | -0,8 | 94% |

Con 66 camas, la probabilidad de llenar la unidad al día siguiente distingue los 13 días llenos de la prueba con AUC 0,79. El intervalo es algo conservador (cubre más del 80%).

**Marco de tiempo.** El tablero de la mañana muestra el censo de la noche anterior. Por eso, desde el tablero, "sale" quiere decir que egresa hoy y el día de la semana que cuenta es el de hoy; el pronóstico rotula cada día con su mañana (la mañana de mañana es el censo del final de hoy). Quien aparece por primera vez en el tablero ingresó el día anterior: ese es el día de la semana de su llegada.

## Aprendizaje del estado diario

El modelo del consolidado no sabe si hoy el paciente sigue con norepinefrina o si ya quedó listo y espera cama. Esa información está en el tablero de ORBIT y en las entregas de turno, pero nadie la guarda día a día. `camas/aprendizaje.py` la guarda y aprende de ella:

1. **Guarda cada mañana** una fila por paciente en la misma base del historial de camas (tablas `estado` y `cobertura`). Variables: soportes (ventilación invasiva, vasoactivos, alto flujo o VMNI, oxígeno de bajo flujo, terapia renal), escalas (NEWS, SOFA, RASS, CPOT, EVA, Barthel, IMS), criterio de ORBIT y condición de la entrega, estado de los criterios de alta y su probabilidad por reglas, antimicrobianos, dispositivos, aislamiento, pendientes y barreras (B01 a B08), y la probabilidad del modelo del consolidado si está configurado. Nunca nombre, documento ni número de ingreso.
2. **Deriva variables del día anterior**: días en UCI de la estancia, cambio del NEWS, retiro de soporte desde ayer, días seguidos sin soporte y días seguidos en estado "listo".
3. **Etiqueta al día siguiente.** El paciente salió si ya no aparece en ninguna fuente y su estación sí trae datos (al menos la mitad de las camas del día anterior). Si de la estación no llegó nada, la fila queda sin etiqueta en vez de contarse como egreso. Si falta un día y el paciente vuelve a aparecer, tampoco cuenta como egreso.
4. **Entrena** cuando hay 300 días-paciente con desenlace y 30 egresos, y **valida** en las últimas semanas (al menos 14 días) contra tres referencias: días en UCI y día de la semana, criterios de alta por reglas calibrados con lo observado, y el modelo del consolidado. El plan de camas lo usa solo si su Brier es mejor que el de las tres.
5. **Se reentrena cada 7 días** dentro de la corrida diaria. El modelo y su informe (`modelo_egreso_estado.joblib`, `.md` y `.json`) quedan junto al historial de camas; `UCI_MODELO_EGRESO_ESTADO` cambia la ruta.

**Fecha de cada estado.** Una entrega enviada antes de la 1 p. m. (la del turno de la noche) describe a los pacientes de esa mañana; una enviada después, a los de la mañana siguiente. Si llegan varias de la misma estación para la misma mañana se usa la más reciente. Sin estación en el encabezado se usa el buzón que la envió.

**Identidad.** El tablero trae el seudónimo del documento (hash con sal, `UCI_SAL_SEUDONIMO`). La entrega trae el número de ingreso, que se guarda solo como hash con sal. Cuando una cama trae los dos el mismo día quedan enlazados, también hacia atrás. Sin ninguno se usa la cama.

**Cuándo empieza a servir.** Con 66 camas y unos 9 egresos por día, los mínimos de entrenamiento se cumplen en menos de una semana de tableros diarios; la validación necesita unas cuatro semanas (14 días de prueba con 10 egresos o más). Desde ahí, cada semana se reentrena y el informe dice si ya supera a las referencias.

Con una UCI simulada de 24 camas y 160 días (`datos_prueba/historial.py`), las etiquetas coinciden 100% con los egresos simulados y el modelo del estado diario logra AUC 0,84 en la validación, frente a 0,75 de los criterios de alta calibrados y 0,70 de días en UCI y día de la semana. Son datos inventados: la cifra real se sabrá con el historial de la clínica.

### Qué cambia en el plan de camas

- Cada paciente trae `probabilidad_egreso_historica` (modelo del consolidado) y, cuando el modelo del estado diario ya superó a las referencias, `probabilidad_egreso_aprendida`.
- La brecha usa los egresos esperados del modelo aprendido cuando existe (`fuente_egresos`); si no, los de los criterios de alta. El informe muestra las otras estimaciones al lado.
- El pronóstico de ocupación usa, para el día de hoy, la probabilidad del modelo aprendido.

## Calculadora

`modelo-trayectoria --modelo-ocupacion modelo_ocupacion.joblib` suma a la calculadora la probabilidad de salir de la UCI cada uno de los próximos 14 días, con el día de la semana (sale menos gente sábado y domingo). Es el modelo de egreso diario, distinto del de estancia: la página muestra las dos estimaciones de "más de 7 días" para que se vea la incertidumbre. Una prueba verifica que la página y Python dan lo mismo.

## Uso

```bash
# Modelo del consolidado y validación del pronóstico (el archivo real y el modelo quedan fuera del repositorio)
python -m uci_control modelo-ocupacion --egresos egresos_uci.xlsx --camas-totales 66 --salida modelos/
export UCI_MODELO_OCUPACION=modelos/modelo_ocupacion.joblib

# Cargar al historial lo que ya está en el buzón (entregas y tableros desde una fecha)
python -m uci_control historial-entregas --gmail-token token.json --desde 2026-10-15 --historial historial_camas.sqlite
export UCI_REMITENTES_ENTREGA=uci.torreb@clinica.co,uci.torrec@clinica.co   # opcional: buzones de las unidades

# Entrenar y validar ya, sin esperar el reentrenamiento semanal de la corrida diaria
python -m uci_control modelo-estado --historial historial_camas.sqlite
```

La corrida diaria (`diario`) guarda el estado de cada mañana y reentrena sola; no hace falta programar nada más.

## Límites

- Las entregas se leen con la plantilla estructurada. Las que llegan en el formato UCIFM041 todavía no se entienden (ver Pendiente en el README); hasta adaptarlo, el estado diario sale sobre todo del tablero de ORBIT, que debe llegar cada día al buzón o a la carpeta.
- El consolidado no dice el estado al egreso: entre los "egresos de UCI" hay fallecidos. El modelo del estado diario aprende cualquier salida de la cama UCI (alta a piso, remisión o fallecimiento).
- La hora de egreso no se conoce: todo es por día.
