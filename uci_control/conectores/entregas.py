"""Parser de la entrega de turno de enfermería.

Lee la plantilla estructurada propuesta (un bloque "CAMA:" por paciente con campos fijos) y,
además, revisa el texto libre para encontrar soportes que el campo estructurado no dice.
Las líneas con contraseñas se descartan sin guardarlas y dejan una alerta de seguridad.
"""

from __future__ import annotations

import re
from datetime import date

from ..fechas import PATRON_FECHA, leer_fecha
from ..modelos import Dispositivo, EntregaCama, EntregaEstacion, Pendiente
from ..privacidad import contiene_secreto
from .comun import leer_condicion, normalizar_cama, si_no, sin_tildes

VASOACTIVOS = (
    "norepinefrina", "noradrenalina", "vasopresina", "adrenalina", "epinefrina", "dopamina",
    "dobutamina", "milrinona", "milrinone", "levosimendan", "fenilefrina", "terlipresina",
)
_NEGACION = re.compile(r"(suspend|retir|sin\s|desmont|se\s+apag|destete\s+complet|no\s+requiere)\w*", re.I)
_VMI_TEXTO = re.compile(r"\bVMI\b|ventilaci[oó]n\s+mec[aá]nica\s+invasiva|\bIOT\b|\bTOT\b|intubad[oa]", re.I)

_CAMPOS = {
    "cama": "cama",
    "episodio": "episodio",
    "ingreso": "episodio",
    "ingreso uci": "fecha_ingreso_uci",
    "fecha ingreso uci": "fecha_ingreso_uci",
    "condicion": "condicion",
    "ventilacion": "ventilacion",
    "soporte ventilatorio": "ventilacion",
    "vasoactivos": "vasoactivos",
    "soporte hemodinamico": "vasoactivos",
    "trr": "trr",
    "terapia de reemplazo renal": "trr",
    "dispositivos": "dispositivos",
    "antibioticos": "antibioticos",
    "aislamiento": "aislamiento",
    "pendientes": "pendientes",
    "nota": "nota",
    "observaciones": "nota",
}
_LINEA_CAMPO = re.compile(r"^\s*([A-Za-zÁÉÍÓÚáéíóúÑñ ]{3,30}?)\s*:\s*(.*)$")
_PENDIENTE = re.compile(r"^\s*[-•*]\s*(?:\[(?P<tipo>[^\]]+)\])?\s*(?P<desc>.*?)\s*(?:\((?:solicitad[oa]|desde)\s*(?P<fecha>[^)]*)\))?\s*$", re.I)
_JUSTIFICACION = re.compile(r"\((?:justificaci[oó]n|indicaci[oó]n)\s*:\s*([^)]*)\)", re.I)


def parsear_entrega(texto: str, fuente: str = "", fecha_referencia: date | None = None) -> EntregaEstacion:
    alertas: list[str] = []
    lineas = []
    for linea in texto.splitlines():
        if contiene_secreto(linea):
            etiqueta = linea.split(":", 1)[0].strip()[:40]
            alertas.append(f"El correo trae una credencial en texto plano ({etiqueta}); se descartó sin guardarla.")
            continue
        lineas.append(linea)

    encabezado: dict[str, str] = {}
    bloques: list[dict[str, list[str]]] = []
    actual: dict[str, list[str]] | None = None
    campo_abierto: str | None = None

    for linea in lineas:
        m = _LINEA_CAMPO.match(linea)
        clave = _CAMPOS.get(sin_tildes(m.group(1)).strip().lower()) if m else None
        if m and not clave and actual is None:
            encabezado[sin_tildes(m.group(1)).strip().lower()] = m.group(2).strip()
            continue
        if clave == "cama":
            actual = {"cama": [m.group(2).strip()]}
            bloques.append(actual)
            campo_abierto = None
            continue
        if actual is None:
            continue
        if clave:
            actual.setdefault(clave, [])
            if m.group(2).strip():
                actual[clave].append(m.group(2).strip())
            campo_abierto = clave if clave in ("pendientes", "nota") else None
        elif linea.strip() and campo_abierto:
            actual[campo_abierto].append(linea.strip())

    fecha, aviso = leer_fecha(encabezado.get("fecha"), fecha_referencia)
    if aviso:
        alertas.append(f"Encabezado: {aviso}")
    fecha = fecha or fecha_referencia
    estacion = encabezado.get("estacion") or encabezado.get("unidad") or ""
    turno = encabezado.get("turno")
    camas = [_armar_cama(b, estacion, fecha, turno, fuente) for b in bloques]
    return EntregaEstacion(estacion=estacion, fecha=fecha, turno=turno, camas=camas, alertas=alertas, fuente=fuente)


def _armar_cama(b: dict[str, list[str]], estacion: str, fecha: date | None, turno: str | None, fuente: str) -> EntregaCama:
    def uno(clave: str) -> str | None:
        valores = b.get(clave) or []
        return " ".join(valores).strip() or None

    advertencias: list[str] = []
    ref = fecha or date.today()

    fecha_uci, aviso = leer_fecha(uno("fecha_ingreso_uci"), ref)
    if aviso:
        advertencias.append(f"Ingreso UCI: {aviso}")

    ventilacion = uno("ventilacion")
    vmi = None
    if ventilacion:
        v = sin_tildes(ventilacion).upper()
        vmi = bool(re.search(r"\bVMI\b|MECANICA INVASIVA|VENTILADOR", v)) and "NO INVASIVA" not in v

    texto_vaso = uno("vasoactivos") or ""
    vasoactivos = [] if si_no(texto_vaso) is False else _farmacos(texto_vaso)
    if texto_vaso and si_no(texto_vaso) is None and not vasoactivos:
        vasoactivos = [texto_vaso]

    dispositivos = []
    for item in _items(uno("dispositivos")):
        just = _JUSTIFICACION.search(item)
        sin_just = _JUSTIFICACION.sub("", item)
        m_fecha = PATRON_FECHA.search(sin_just)
        f_ins, aviso = leer_fecha(m_fecha.group(0), ref) if m_fecha else (None, None)
        if aviso:
            advertencias.append(f"Dispositivo «{item}»: {aviso}")
        tipo = PATRON_FECHA.sub("", sin_just)
        tipo = re.sub(r"\b(desde|del|el)\b", "", tipo, flags=re.I).strip(" -,")
        dispositivos.append(Dispositivo(tipo=tipo, fecha_insercion=f_ins, justificacion=just.group(1).strip() if just else None))

    pendientes = []
    for linea in b.get("pendientes", []):
        m = _PENDIENTE.match(linea if linea.lstrip().startswith(("-", "•", "*")) else f"- {linea}")
        if not m or not m.group("desc"):
            continue
        f_sol, aviso = leer_fecha(m.group("fecha"), ref) if m.group("fecha") else (None, None)
        if aviso:
            advertencias.append(f"Pendiente «{m.group('desc')}»: {aviso}")
        pendientes.append(Pendiente(tipo=(m.group("tipo") or "otro").strip().lower(), descripcion=m.group("desc"), fecha_solicitud=f_sol))

    nota = "\n".join(b.get("nota", []))

    # El texto libre a veces dice más que el campo estructurado.
    en_nota = [f for f in _farmacos(nota) if f not in vasoactivos]
    if en_nota:
        advertencias.append(f"La nota menciona {', '.join(en_nota)} pero el campo de vasoactivos no lo registra.")
    if vmi is False and _VMI_TEXTO.search(nota):
        advertencias.append("La nota menciona ventilación invasiva pero el campo de ventilación no la registra.")

    aislamiento = uno("aislamiento")
    if aislamiento and si_no(aislamiento) is False:
        aislamiento = None

    return EntregaCama(
        cama=normalizar_cama(uno("cama")),
        estacion=estacion,
        fecha=fecha,
        turno=turno,
        episodio=uno("episodio"),
        fecha_ingreso_uci=fecha_uci,
        condicion=leer_condicion(uno("condicion")),
        ventilacion=ventilacion,
        vmi=vmi,
        vasoactivos=vasoactivos,
        trr=si_no(uno("trr")) if si_no(uno("trr")) is not None else (bool(uno("trr")) if uno("trr") else None),
        dispositivos=dispositivos,
        antibioticos=_items(uno("antibioticos")) if si_no(uno("antibioticos")) is not False else [],
        aislamiento=aislamiento,
        pendientes=pendientes,
        nota=nota,
        advertencias=advertencias,
        fuente=fuente,
    )


def _items(texto: str | None) -> list[str]:
    if not texto:
        return []
    return [p.strip() for p in re.split(r";|\n", texto) if p.strip()]


def _farmacos(texto: str) -> list[str]:
    encontrados = []
    for m in re.finditer("|".join(VASOACTIVOS), texto, re.I):
        previo = texto[max(0, m.start() - 30): m.start()]
        if _NEGACION.search(previo):
            continue
        nombre = m.group(0).lower()
        if nombre not in encontrados:
            encontrados.append(nombre)
    return encontrados
