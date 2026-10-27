# Corrida diaria y seguimiento

Cómo dejar UCI CONTROL AI corriendo solo cada mañana y cómo usa el equipo el seguimiento de sugerencias.

## Qué hace la corrida diaria

A la hora programada, `python -m uci_control diario`:

1. Busca en Gmail las entregas de turno del día y los tableros de ORBIT adjuntos (`Reporte_TableroControlUCI1<fecha>.xlsx`). Si llegan varios tableros (uno por torre), los une y deja una fila por cama.
2. Arma el censo desde el tablero, o usa la exportación CSV del censo si se pasa `--orbit`.
3. Concilia ORBIT, entregas y cargos (si se pasa `--costos`), calcula el riesgo por paciente y genera las sugerencias.
4. Registra las sugerencias en la base de seguimiento: las que se repiten conservan su código y cuentan los días abiertas.
5. Escribe en `--salida` el informe en Markdown, JSON y HTML interactivo, más `pendientes_<fecha>.csv`.
6. Con `--enviar`, manda por correo un resumen en el cuerpo y el HTML completo adjunto.

## Dónde debe correr

En un equipo o servidor de la institución, con acceso al buzón y autorizado para datos de salud. No en GitHub Actions ni en servicios externos sin acuerdo de tratamiento de datos. El repositorio es público: ningún archivo de esta lista se versiona.

| Archivo o variable | Para qué | Dónde vive |
|---|---|---|
| `token.json` | Acceso a Gmail (lectura y envío) | Carpeta del agente, solo lectura para la cuenta que lo ejecuta |
| `UCI_SAL_SEUDONIMO` | Sal del seudónimo de pacientes | Variable de entorno del equipo |
| `UCI_PARAMETROS` | Ruta al JSON con las cifras financieras reales | Variable de entorno; el JSON fuera del repositorio |
| `ANTHROPIC_API_KEY` | Solo si se usa `--con-ia` | Variable de entorno |
| `seguimiento.sqlite` | Historia del seguimiento | Carpeta del agente, con copia de respaldo |

## Instalación

```bash
pip install -r requirements-gmail.txt
```

### Acceso a Gmail (una sola vez)

1. En [Google Cloud Console](https://console.cloud.google.com/), con la cuenta institucional: crear un proyecto, habilitar **Gmail API** y crear una credencial **OAuth, aplicación de escritorio**. Descargar el `client_secret.json`.
2. En el equipo del agente: `python scripts/autorizar_gmail.py --cliente client_secret.json --token token.json` y aceptar en el navegador. Con `--solo-lectura` el agente no podrá enviar correos.
3. Probar sin enviar: `python -m uci_control diario --gmail-token token.json --salida salida`.

## Programarlo cada mañana

Linux (cron, 6:30 a. m. de lunes a domingo):

```cron
30 6 * * * cd /opt/uci-control-ai && UCI_SAL_SEUDONIMO=... UCI_PARAMETROS=/opt/uci-privado/parametros.json \
  python -m uci_control diario --gmail-token /opt/uci-privado/token.json --seguimiento /opt/uci-privado/seguimiento.sqlite \
  --salida /opt/uci-privado/salida --enviar coordinacion.uci@clinica.co >> /opt/uci-privado/diario.log 2>&1
```

Windows (Programador de tareas): crear una tarea diaria a las 6:30 que ejecute `python.exe` con los argumentos `-m uci_control diario --gmail-token C:\uci-privado\token.json --seguimiento C:\uci-privado\seguimiento.sqlite --salida C:\uci-privado\salida --enviar coordinacion.uci@clinica.co`, iniciando en la carpeta del repositorio, y definir las variables de entorno en la cuenta que la ejecuta.

## Seguimiento de sugerencias

Cada sugerencia tiene un código de 8 caracteres que aparece en el informe y en el correo. Estados:

| Estado | Quién lo pone | Significa |
|---|---|---|
| pendiente | El agente | Abierta; el informe muestra hace cuántos días |
| hecho | El equipo | Se hizo la interconsulta o la acción |
| no_aplica | El equipo | El tratante decidió que no corresponde |
| resuelta | El agente | El paciente sigue en el tablero y la condición ya no aparece |

Dos formas de marcarlas:

- Una por una: `python -m uci_control seguimiento marcar A1B2C3D4 hecho --nota "valorado por cirugía"`.
- En lote: abrir `pendientes_<fecha>.csv` en Excel, cambiar la columna `estado` a `hecho` o `no_aplica`, guardar y correr `python -m uci_control seguimiento importar pendientes_<fecha>.csv`.

`python -m uci_control seguimiento resumen` da la adherencia por servicio: porcentaje gestionado y mediana de días hasta hecho. Es el indicador de proceso del macroproyecto (líneas de PROA, ventilación, nutrición y flujo de camas).
