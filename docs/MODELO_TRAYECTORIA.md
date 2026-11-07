# Predicción al ingreso: destino y estancia

`uci_control/prediccion/trayectoria.py` aprende de los egresos de la clínica qué camino y cuánta estancia esperar para un paciente desde que ingresa a la UCI. Reemplaza los supuestos fijos de días restantes de `riesgo.py` (y con ellos la fecha esperada de egreso del plan de camas) cuando hay un modelo entrenado.

## Qué predice

| Desenlace | Definición | Para qué sirve |
|---|---|---|
| Alta directa desde UCI | El último servicio del ingreso es UCI (sale del hospital sin pasar por piso) | Saber desde el ingreso si el paciente va a necesitar cama de hospitalización al salir |
| Reingreso a UCI en el mismo ingreso | La secuencia de servicios tiene UCI dos veces o más | Pacientes a seguir de cerca por el equipo de respuesta rápida después del traslado |
| UCI más de 7 días | Paciente "estancado" (definición del NHS que ya usa el plan de camas) | Activar desde el primer día la revisión de barreras, PICS y traqueostomía |
| Días en UCI y días totales | Cuantiles 10, 50 y 90 | Fecha esperada de egreso, días-cama comprometidos y valor en juego |

**Sobre el destino.** El consolidado no trae una columna de estado ni de destino al egreso (casa, remisión, fallecido). Por eso el destino se define por el último servicio. Hay señales de que el grupo "sale desde UCI" incluye pacientes fallecidos: 344 de los 360 ingresos con TAS registrada de 60 mmHg o menos (mediana 42/26 mmHg) terminan en UCI, el 17% de ese grupo, y el SOFA de 32% o más tiene la mayor proporción de "alta directa" (45%). Con una columna de estado al egreso, el mismo módulo se entrena para casa, piso, remisión y muerte por separado.

## Variables (solo las que se conocen al ingreso)

- **Juego "tablero"**, el que usa el agente cada mañana: edad, sexo, asegurador agrupado, diagnóstico principal (primer código CIE-10 no secundario, su categoría de 3 caracteres y el agrupador del libro de costos), mortalidad esperada por SOFA, Barthel y NEWS iniciales.
- **Juego "ingreso"**: lo anterior más si llega desde piso, ingresos previos del mismo paciente en el periodo, peso y día de la semana.
- **Excluidas por fuga de información**: escalas finales, días, traslados, número de diagnósticos acumulados y los signos vitales del consolidado (por los valores agónicos descritos arriba parecen el último registro de la estancia; con ellos el AUC del destino subía artificialmente de 0,75 a 0,84).

Modelo: gradient boosting por histogramas (scikit-learn), con regresión por cuantiles sobre log(1 + días). La estancia que falta para un paciente que ya lleva t días es la mediana condicional de una log-normal ajustada a sus cuantiles, así quien supera lo esperado no queda con cero días.

## Validación temporal

Se descartan los ingresos anteriores al inicio de la ventana de egresos (solo estarían los de estancia larga) y los de los últimos tres meses (solo estarían los cortos). Se entrena con lo más antiguo y se mide en lo más reciente, como se va a usar.

Ingresos modelados: 6190 de 7032 (ventana 2024-10-01 a 2026-06-30). Validación temporal: entrenamiento antes de 2025-12-01, prueba desde esa fecha.

Nota: el archivo no trae el estado ni el destino al egreso (casa, remisión, fallecido). "Alta directa" es salir del hospital con la UCI como último servicio; si el archivo incluye fallecidos en UCI, quedan en ese grupo.

## Desenlaces binarios (prueba)

| Desenlace | Juego | Prevalencia entren. | Prevalencia prueba | AUC | AUC solo SOFA | Brier | Brier sin modelo |
|---|---|---|---|---|---|---|---|
| Alta a casa directamente desde UCI | tablero | 25.9% | 34.7% | 0.74 | 0.48 | 0.187 | 0.234 |
| Alta a casa directamente desde UCI | ingreso | 25.9% | 34.7% | 0.75 | 0.48 | 0.184 | 0.234 |
| Reingreso a UCI en el mismo ingreso | tablero | 8.1% | 7.6% | 0.69 | 0.56 | 0.070 | 0.070 |
| Reingreso a UCI en el mismo ingreso | ingreso | 8.1% | 7.6% | 0.68 | 0.56 | 0.072 | 0.070 |
| UCI más de 7 días | tablero | 23.3% | 27.5% | 0.76 | 0.65 | 0.169 | 0.201 |
| UCI más de 7 días | ingreso | 23.3% | 27.5% | 0.76 | 0.65 | 0.169 | 0.201 |

## Estancia (prueba)

| Desenlace | Juego | Mediana observada | Error absoluto medio | Sin modelo | Cobertura p10-p90 | Acierto de banda | Banda sin modelo | Spearman |
|---|---|---|---|---|---|---|---|---|
| Días en UCI | tablero | 4 | 3.9 | 4.4 | 87% | 46% | 25% | 0.54 |
| Días en UCI | ingreso | 4 | 3.9 | 4.4 | 88% | 45% | 25% | 0.55 |
| Días totales de hospitalización | tablero | 8 | 9.1 | 10.9 | 84% | 45% | 21% | 0.61 |
| Días totales de hospitalización | ingreso | 8 | 8.7 | 10.9 | 84% | 47% | 21% | 0.66 |

Bandas: 0-3, 4-7, 8-14 y 15 o más días.

## Calibración del destino (juego ingreso, quintiles)

| Predicho | Observado | n |
|---|---|---|
| 6% | 16% | 393 |
| 12% | 21% | 392 |
| 20% | 23% | 393 |
| 32% | 38% | 392 |
| 66% | 75% | 393 |

## Variables que más pesan (juego ingreso, caída del AUC al permutar)

- Alta a casa directamente desde UCI: dx3 (+0.038), agrupador (+0.036), news (+0.029), asegurador (+0.029), sofa_mortalidad (+0.019), barthel (+0.017), desde_piso (+0.014), dia_semana (+0.011)
- UCI más de 7 días: sofa_mortalidad (+0.069), news (+0.036), asegurador (+0.021), dx3 (+0.019), agrupador (+0.015), barthel (+0.011), edad (+0.005), dia_semana (+0.005)

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

- **Estancia prolongada (AUC 0,76)** y **destino (AUC 0,75)** se predicen con utilidad al ingreso; el SOFA solo da 0,65 para estancia y no sirve para destino (0,48). Lo que más pesa es el SOFA, el NEWS, el diagnóstico y el asegurador.
- **Días**: el modelo acierta la banda de estancia el 45-48% de las veces frente a 21-25% sin modelo, y su intervalo p10-p90 contiene el valor real en 84-88% de los casos. El error medio sigue siendo de 4 días en UCI: sirve para planear camas en conjunto, no para fijar la fecha de un paciente.
- **Reingreso a UCI (AUC 0,69)** no mejora el Brier sobre la tasa base: con lo que se sabe al ingreso no alcanza; se predice mejor al momento del traslado con el SWIFT.
- **Deriva**: la proporción de salidas desde UCI subió de 26% (2024-2025) a 35% (2026), por eso el modelo subestima en la prueba. Hay que reentrenarlo cada mes con el consolidado actualizado.

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
