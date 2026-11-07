# Predicción al ingreso: destino y estancia

`uci_control/prediccion/trayectoria.py` aprende de los egresos de la clínica qué camino y cuánta estancia esperar para un paciente desde que ingresa a la UCI. Reemplaza los supuestos fijos de días restantes de `riesgo.py` (y con ellos la fecha esperada de egreso del plan de camas) cuando hay un modelo entrenado.

## Qué predice

| Desenlace | Definición | Para qué sirve |
|---|---|---|
| Alta directa desde UCI | El último servicio del ingreso es UCI (sale del hospital sin pasar por piso) | Saber desde el ingreso si el paciente va a necesitar cama de hospitalización al salir |
| Camino completo | Excluyentes: sale del hospital desde UCI, pasa a piso y sale, o vuelve a UCI | Una sola lectura que suma 100% para el plan de camas |
| Reingreso a UCI en el mismo ingreso | La secuencia de servicios tiene UCI dos veces o más | Pacientes a seguir de cerca por el equipo de respuesta rápida después del traslado |
| UCI más de 7 días | Paciente "estancado" (definición del NHS que ya usa el plan de camas) | Activar desde el primer día la revisión de barreras, PICS y traqueostomía |
| Días en UCI y días totales | Cuantiles 10, 50 y 90 | Fecha esperada de egreso, días-cama comprometidos y valor en juego |

**Sobre el destino.** El consolidado no trae una columna de estado ni de destino al egreso (casa, remisión, fallecido). Por eso el destino se define por el último servicio. Hay señales de que el grupo "sale desde UCI" incluye pacientes fallecidos: 344 de los 360 ingresos con TAS registrada de 60 mmHg o menos (mediana 42/26 mmHg) terminan en UCI, el 17% de ese grupo, y el SOFA de 32% o más tiene la mayor proporción de "alta directa" (45%). Con una columna de estado al egreso, el mismo módulo se entrena para casa, piso, remisión y muerte por separado.

## Variables (solo las que se conocen al ingreso)

- **Juego "tablero"**, el que usa el agente cada mañana: edad, sexo, asegurador agrupado, diagnóstico principal (primer código CIE-10 no secundario, su categoría de 3 caracteres y el agrupador del libro de costos), mortalidad esperada por SOFA, Barthel y NEWS iniciales.
- **Juego "ingreso"**: lo anterior más si llega desde piso, ingresos previos del mismo paciente en el periodo, peso y día de la semana.
- **Excluidas por fuga de información**: escalas finales, días, traslados, número de diagnósticos acumulados y los signos vitales del consolidado (por los valores agónicos descritos arriba parecen el último registro de la estancia; con ellos el AUC del destino subía artificialmente de 0,75 a 0,84).

- **Probadas y descartadas**: comorbilidades tomadas de la lista de diagnósticos (HTA, diabetes, ERC, EPOC, cáncer, desnutrición y otras). Subían el AUC del destino de 0,75 a 0,80, pero cualquier código secundario baja la "salida desde UCI" de 32% a 11%, sin importar cuál sea: la lista crece mientras el paciente está en piso, así que el modelo aprendía la estancia y no el paciente. Para usarlas hace falta registrar los antecedentes al ingreso. El código CIE-10 de 4 caracteres tampoco mejoró sobre la categoría de 3.

Modelo: gradient boosting por histogramas (scikit-learn) con árboles pequeños (7 hojas, 60 pacientes por hoja, 200 rondas), elegidos en la validación temporal frente a árboles de 15 hojas; regresión por cuantiles sobre log(1 + días). La estancia que falta para un paciente que ya lleva t días es la mediana condicional de una log-normal ajustada a sus cuantiles, así quien supera lo esperado no queda con cero días.

## Validación temporal

Se descartan los ingresos anteriores al inicio de la ventana de egresos (solo estarían los de estancia larga) y los de los últimos tres meses (solo estarían los cortos). Se entrena con lo más antiguo y se mide en lo más reciente, como se va a usar. El intervalo del AUC sale de remuestrear 300 veces los pacientes de prueba.

Ingresos modelados: 6190 de 7032 (ventana 2024-10-01 a 2026-06-30). Validación temporal: entrenamiento antes de 2025-12-01, prueba desde esa fecha.

Nota: el archivo no trae el estado ni el destino al egreso (casa, remisión, fallecido). "Alta directa" es salir del hospital con la UCI como último servicio; si el archivo incluye fallecidos en UCI, quedan en ese grupo.

## Desenlaces binarios (prueba)

| Desenlace | Juego | Prevalencia entren. | Prevalencia prueba | AUC (IC 95%) | AUC solo SOFA | Brier | Brier sin modelo |
|---|---|---|---|---|---|---|---|
| Alta del hospital directamente desde UCI | tablero | 25.9% | 34.7% | 0.75 (0.73-0.78) | 0.48 | 0.184 | 0.234 |
| Alta del hospital directamente desde UCI | ingreso | 25.9% | 34.7% | 0.75 (0.73-0.78) | 0.48 | 0.184 | 0.234 |
| Reingreso a UCI en el mismo ingreso | tablero | 8.1% | 7.6% | 0.70 (0.67-0.74) | 0.56 | 0.069 | 0.070 |
| Reingreso a UCI en el mismo ingreso | ingreso | 8.1% | 7.6% | 0.69 (0.65-0.73) | 0.56 | 0.071 | 0.070 |
| UCI más de 7 días | tablero | 23.3% | 27.5% | 0.77 (0.75-0.80) | 0.65 | 0.164 | 0.201 |
| UCI más de 7 días | ingreso | 23.3% | 27.5% | 0.77 (0.75-0.79) | 0.65 | 0.166 | 0.201 |

## Camino completo (prueba)

| Juego | Sale desde UCI | Piso y alta | Vuelve a UCI | Acierto clase más probable | Sin modelo | Log-loss | Sin modelo |
|---|---|---|---|---|---|---|---|
| tablero | AUC 0.77 (33%) | AUC 0.71 (59%) | AUC 0.70 (8%) | 68% | 59% | 0.769 | 0.891 |
| ingreso | AUC 0.76 (33%) | AUC 0.71 (59%) | AUC 0.70 (8%) | 69% | 59% | 0.768 | 0.891 |

## Estancia (prueba)

| Desenlace | Juego | Mediana observada | Error absoluto medio | Sin modelo | Cobertura p10-p90 | Acierto de banda | Banda sin modelo | Spearman |
|---|---|---|---|---|---|---|---|---|
| Días en UCI | tablero | 4 | 3.9 | 4.4 | 90% | 47% | 25% | 0.55 |
| Días en UCI | ingreso | 4 | 3.9 | 4.4 | 90% | 46% | 25% | 0.55 |
| Días totales de hospitalización | tablero | 8 | 9.1 | 10.9 | 86% | 44% | 21% | 0.61 |
| Días totales de hospitalización | ingreso | 8 | 8.8 | 10.9 | 87% | 47% | 21% | 0.66 |

Bandas: 0-3, 4-7, 8-14 y 15 o más días.

## Probabilidad por rango de días (prueba, juego tablero)

Una sola fecha tiene un techo con estos datos: decir a todos el mismo número de días acierta con ±2 días casi tanto como el modelo. Por eso el modelo da la probabilidad de cada rango. La mejora es la reducción del ranked probability score frente a usar la distribución histórica para todos.

| Estancia | Acierto ±2 días del modelo | De la mejor constante | Mejora por rangos | Rangos | Observado | Predicho |
|---|---|---|---|---|---|---|
| Días en UCI | 56% | 62% | 18% | 0-2, 3-4, 5-7, 8-14, 15+ | 35%, 21%, 16%, 17%, 11% | 32%, 23%, 19%, 15%, 11% |
| Días totales de hospitalización | 29% | 35% | 25% | 0-3, 4-7, 8-14, 15-30, 31+ | 24%, 23%, 21%, 19%, 13% | 21%, 22%, 23%, 21%, 13% |

## Estabilidad en el tiempo (origen móvil, juego tablero, 3 meses de prueba por corte)

| Corte | Prueba | AUC destino | AUC UCI > 7 días | AUC reingreso UCI | Error días UCI | Sin modelo |
|---|---|---|---|---|---|---|
| 2025-05-01 | 925 | 0.67 | 0.63 | 0.68 | 3.9 | 4.0 |
| 2025-07-01 | 891 | 0.64 | 0.74 | 0.71 | 3.8 | 3.9 |
| 2025-09-01 | 849 | 0.69 | 0.71 | 0.66 | 4.3 | 4.3 |
| 2025-11-01 | 816 | 0.74 | 0.75 | 0.62 | 3.9 | 4.4 |
| 2026-01-01 | 863 | 0.74 | 0.77 | 0.69 | 3.9 | 4.5 |
| 2026-03-01 | 821 | 0.77 | 0.80 | 0.72 | 4.0 | 4.6 |

## Calibración del destino (juego ingreso, quintiles)

| Predicho | Observado | n |
|---|---|---|
| 8% | 16% | 393 |
| 15% | 19% | 392 |
| 21% | 25% | 393 |
| 32% | 40% | 392 |
| 62% | 73% | 393 |

## Variables que más pesan (juego ingreso, caída del AUC al permutar)

- Alta del hospital directamente desde UCI: dx3 (+0.048), asegurador (+0.032), agrupador (+0.028), sofa_mortalidad (+0.024), news (+0.021), barthel (+0.016), desde_piso (+0.012), dia_semana (+0.006)
- UCI más de 7 días: sofa_mortalidad (+0.064), news (+0.038), dx3 (+0.026), asegurador (+0.020), barthel (+0.011), agrupador (+0.011), edad (+0.004), dia_semana (+0.002)

## Perfil por agrupador diagnóstico

| Agrupador | Ingresos | UCI mediana | Total mediana | Alta directa | UCI > 7 días |
|---|---|---|---|---|---|
| CARDIOVASCULAR | 2919 | 3 | 6 | 36% | 20% |
| ONCO-HEMATO | 825 | 4 | 13 | 18% | 27% |
| RESPIRATORIO | 363 | 6 | 13 | 34% | 43% |
| INFECCIOSO | 349 | 6 | 14 | 30% | 43% |
| METABOLICO | 304 | 4 | 8 | 18% | 28% |
| GASTRO | 296 | 5 | 11 | 25% | 30% |
| RENAL | 214 | 4 | 9 | 15% | 20% |
| TRAUMA | 209 | 4 | 10 | 20% | 27% |
| MUSCULOESQUELETICO | 199 | 3 | 11 | 16% | 19% |
| OBSTETRICO | 153 | 2 | 5 | 10% | 3% |
| NEUROLOGICO | 129 | 4 | 11 | 21% | 30% |
| MENTAL | 68 | 6 | 16 | 4% | 41% |
| OTROS | 67 | 4 | 8 | 21% | 25% |
| CONGENITO | 52 | 1 | 1 | 62% | 8% |
| PIEL | 43 | 5 | 14 | 19% | 28% |

## Perfil por SOFA inicial (mortalidad esperada)

| SOFA | Ingresos | UCI mediana | Total mediana | Alta directa |
|---|---|---|---|---|
| 0% | 2011 | 3 | 7 | 27% |
| 6-8% | 1438 | 5 | 11 | 19% |
| 22-25% | 659 | 6 | 14 | 23% |
| 32%+ | 472 | 8 | 15 | 45% |

## Lectura

- **Camino de salida (AUC 0,75 a 0,77)** y **estancia prolongada (AUC 0,77, IC 95% 0,75 a 0,80)** se predicen con utilidad al ingreso. El SOFA solo da 0,65 para estancia y no sirve para el camino (0,48). Lo que más pesa es el diagnóstico, el asegurador, el SOFA y el NEWS.
- **Camino completo**: la opción más probable acierta en 68% de los casos frente a 59% de asignar siempre "piso y alta".
- **Días**: acierta la banda de estancia 44-47% de las veces frente a 21-25% sin modelo, y el rango p10-p90 contiene el valor real en 86-90% de los casos. El error medio sigue siendo de 4 días en UCI: sirve para planear camas en conjunto, no para fijar la fecha de un paciente. La suma de medianas subestima los días-cama totales porque la estancia tiene cola larga.
- **Fecha puntual de egreso**: con datos de ingreso tiene un techo. El modelo acierta ±2 días en 56% de los egresos de UCI, y decir a todos "3 días" acierta 62% porque la mayoría de estancias son cortas. Por eso el modelo da la **probabilidad de cada rango** (0-2, 3-4, 5-7, 8-14 y 15 o más días): está calibrada y mejora 18% en UCI y 25% en el hospital sobre la distribución histórica.
- **Probado y descartado para afinar**: ocupación de la clínica al ingreso, ingresos de los 7 días previos, estancia y tiempo desde el ingreso anterior y mes del año no mejoraron; LightGBM y CatBoost dieron entre 0,01 y 0,02 más de AUC, dentro del intervalo de confianza; un modelo discreto que busca el día con más probabilidad de acertar ±2 días llegó a 60%, por debajo de la constante.
- **Reingreso a UCI (AUC 0,70)**: discrimina algo pero apenas mejora el Brier sobre la tasa base; se predice mejor al momento del traslado con el SWIFT.
- **Más datos, mejor modelo**: con el origen móvil el AUC del camino pasa de 0,67 (entrenado con 7 meses) a 0,77 (con 17 meses). La salida desde UCI subió de 26% en 2024-2025 a 35% en 2026, por eso hay que reentrenar cada mes.

## Calculadora

`modelo-trayectoria` escribe también `calculadora_trayectoria.html`: una página autónoma que evalúa el modelo en el navegador (los árboles se exportan a JSON con `exportar_json`; una prueba verifica que da lo mismo que scikit-learn). Recibe edad, sexo, diagnóstico, asegurador, SOFA, Barthel, NEWS, fecha de ingreso y días que ya lleva, y muestra el camino más probable, la probabilidad de más de 7 días, los rangos de días y las fechas esperadas de egreso. Con `--modelo-ocupacion modelo_ocupacion.joblib` suma la probabilidad de salir de la UCI cada uno de los próximos 14 días según el modelo de egreso diario ([EGRESO_DIARIO.md](EGRESO_DIARIO.md)). No contiene datos de pacientes.

## Uso

```bash
# Entrenar y validar (el archivo real y los modelos quedan fuera del repositorio)
python -m uci_control modelo-trayectoria --egresos egresos_uci.xlsx --salida modelos/

# Usarlo en el riesgo diario y el plan de camas
export UCI_MODELO_TRAYECTORIA=modelos/modelo_trayectoria_tablero.joblib
python -m uci_control riesgo --tablero Reporte_TableroControlUCI1.xlsx --salida salida/
```

El informe de riesgo suma la tabla "Destino y estancia esperados" y los días restantes de cada paciente salen del modelo. Sin la variable, todo sigue con los supuestos anteriores. Un `.joblib` puede ejecutar código al cargarse: solo cargar modelos generados por el propio agente.

## Lo que lo mejoraría

1. **Estado y destino al egreso** (vivo o fallecido; casa, remisión, hogar de paso): convierte el destino en el desenlace que se quiere y separa la mortalidad.
2. **Fecha y hora de ingreso y salida de cada tramo UCI**: estancia UCI del primer tramo, separada de los reingresos.
3. **Signos vitales, gases y Glasgow de las primeras 24 horas**, y el motivo de ingreso a UCI: con eso se puede calcular APACHE II o SAPS 3.
4. **Soportes al ingreso** (ventilación invasiva, vasoactivos, terapia renal), que en la literatura son los mayores predictores de estancia.
