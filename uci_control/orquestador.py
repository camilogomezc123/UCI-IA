"""UCI CONTROL AI: coordina las fuentes, verifica y prioriza.

Flujo: conciliación → indicadores → prioridad determinística → Claude redacta el resumen y
ordena las acciones → el orquestador verifica que cada prioridad cite camas y
discrepancias que existen. Si no hay credenciales de Claude, el informe sale igual con la
priorización determinística.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date

from . import config, indicadores
from .indicadores import pesos
from .conciliacion import ResultadoConciliacion, conciliar
from .modelos import CargoCosto, EntregaEstacion, ExpedienteCama, RegistroOrbit
from .privacidad import limpiar_texto

log = logging.getLogger(__name__)

RESPONSABLES = [
    "medico_uci", "enfermeria", "gestor_camas", "autorizaciones",
    "facturacion", "infectologia_proa", "sistemas", "coordinacion",
]
PLAZOS = ["inmediato", "antes_10am", "hoy", "esta_semana"]

# Acción sugerida por regla para la priorización sin IA (y como guía para Claude).
ACCION_POR_REGLA = {
    "R01": ("Confirmar en la ronda si el paciente está realmente en condición piso y aclarar el soporte", "medico_uci", "inmediato"),
    "R02": ("Unificar el nivel de atención en ORBIT y en la entrega", "medico_uci", "antes_10am"),
    "R03": ("Verificar la ventilación al lado de la cama y corregir la fuente errada", "enfermeria", "inmediato"),
    "R04": ("Verificar el soporte vasoactivo al lado de la cama y corregir la fuente errada", "enfermeria", "inmediato"),
    "R05": ("Confirmar la ocupación real y completar el censo o la entrega", "enfermeria", "hoy"),
    "R06": ("Verificar la identidad del paciente y el número de ingreso", "enfermeria", "inmediato"),
    "R07": ("Corregir la fecha de ingreso a UCI", "enfermeria", "hoy"),
    "R08": ("Evaluar el retiro del dispositivo o registrar por qué sigue indicado", "medico_uci", "hoy"),
    "R09": ("Escalar el pendiente con el responsable", "autorizaciones", "hoy"),
    "R10": ("Confirmar el aislamiento, señalizar y corregir la fuente", "infectologia_proa", "inmediato"),
    "R11": ("Registrar NEWS y SOFA como puntaje", "medico_uci", "hoy"),
    "R12": ("Cargar la estancia del día", "facturacion", "hoy"),
    "R13": ("Revisar el cargo de estancia sin cama ocupada", "facturacion", "esta_semana"),
    "R14": ("Llevar a la ronda de flujo de las 10 am y gestionar cama de piso", "gestor_camas", "antes_10am"),
    "R15": ("Corregir la entrega de turno", "enfermeria", "hoy"),
    "R16": ("Revisar la pertinencia del cargo de estancia UCI", "facturacion", "hoy"),
    "R17": ("Retirar las credenciales del formato de entrega y cambiarlas", "sistemas", "inmediato"),
}

SISTEMA = """Eres UCI CONTROL AI, el asistente de coordinación médica de una unidad de cuidado intensivo de adultos.
Recibes la conciliación diaria de tres fuentes: el censo de ORBIT, las entregas de turno de enfermería y los cargos de facturación. Un motor de reglas ya detectó las discrepancias; cada una tiene un id (D001, D002...).

Tu trabajo:
1. Escribir un resumen ejecutivo breve para el coordinador médico: estado de la unidad, lo urgente y el impacto en flujo y costos.
2. Ordenar las acciones del día. Cada prioridad nombra una cama (o "UNIDAD" si es de toda la unidad), cita los ids de discrepancia que la sustentan y propone un responsable y un plazo. Agrupa en una sola prioridad las discrepancias de la misma cama que se resuelven con la misma acción.
3. Emitir alertas solo para lo que no puede esperar a la ronda.
4. Listar las verificaciones de datos que harían más confiable el informe de mañana.

Reglas:
- Usa solo los datos recibidos. No inventes camas, cifras, diagnósticos ni discrepancias.
- Primero seguridad del paciente, después identidad y aislamientos, después flujo de camas y por último lo financiero.
- No das órdenes médicas: propones verificaciones y gestiones para que el equipo decida.
- Las cifras en pesos son estimaciones con promedios de la unidad; preséntalas como tales.
- Escribe en español, frases completas y claras."""

ESQUEMA = {
    "type": "object",
    "properties": {
        "resumen_ejecutivo": {"type": "string"},
        "prioridades": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "orden": {"type": "integer"},
                    "cama": {"type": "string"},
                    "accion": {"type": "string"},
                    "responsable": {"type": "string", "enum": RESPONSABLES},
                    "plazo": {"type": "string", "enum": PLAZOS},
                    "motivo": {"type": "string"},
                    "discrepancias": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["orden", "cama", "accion", "responsable", "plazo", "motivo", "discrepancias"],
                "additionalProperties": False,
            },
        },
        "alertas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "nivel": {"type": "string", "enum": ["critica", "alta", "media"]},
                    "mensaje": {"type": "string"},
                    "camas": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["nivel", "mensaje", "camas"],
                "additionalProperties": False,
            },
        },
        "verificaciones_datos": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["resumen_ejecutivo", "prioridades", "alertas", "verificaciones_datos"],
    "additionalProperties": False,
}


@dataclass
class InformeDiario:
    fecha: date
    indicadores: dict
    conciliacion: ResultadoConciliacion
    resumen: str
    prioridades: list[dict]
    alertas: list[dict]
    verificaciones_datos: list[str]
    generado_con_ia: bool
    observaciones_verificacion: list[str] = field(default_factory=list)


class Orquestador:
    def __init__(self, cliente=None, usar_ia: bool = True, modelo: str = config.MODELO_CLAUDE, esfuerzo: str = "high"):
        self.usar_ia = usar_ia
        self.modelo = modelo
        self.esfuerzo = esfuerzo
        self._cliente = cliente

    @property
    def cliente(self):
        if self._cliente is None:
            import anthropic

            self._cliente = anthropic.Anthropic()
        return self._cliente

    def ejecutar(self, fecha: date, censo: list[RegistroOrbit], entregas: list[EntregaEstacion], cargos: list[CargoCosto]) -> InformeDiario:
        resultado = conciliar(fecha, censo, entregas, cargos)
        ind = indicadores.calcular(resultado)
        base = priorizar(resultado)

        if self.usar_ia:
            try:
                respuesta = self._consultar_claude(resultado, ind)
                prioridades, observaciones = verificar(respuesta["prioridades"], resultado)
                return InformeDiario(
                    fecha=fecha, indicadores=ind, conciliacion=resultado,
                    resumen=respuesta["resumen_ejecutivo"], prioridades=prioridades,
                    alertas=respuesta["alertas"], verificaciones_datos=respuesta["verificaciones_datos"],
                    generado_con_ia=True, observaciones_verificacion=observaciones,
                )
            except Exception as error:  # el informe del día no puede depender de la API
                log.warning("No se pudo usar Claude (%s); se entrega la priorización determinística.", error)
                aviso = f"No se pudo consultar a Claude ({type(error).__name__}); prioridades calculadas por reglas."
                return self._sin_ia(fecha, ind, resultado, base, [aviso])
        return self._sin_ia(fecha, ind, resultado, base, [])

    def _sin_ia(self, fecha, ind, resultado, base, observaciones) -> InformeDiario:
        alertas = [
            {"nivel": d.severidad.value, "mensaje": f"{d.titulo}. {d.detalle}", "camas": [d.cama] if d.cama else []}
            for d in resultado.discrepancias if d.severidad.value == "critica"
        ]
        return InformeDiario(
            fecha=fecha, indicadores=ind, conciliacion=resultado, resumen=resumen_basico(ind),
            prioridades=base, alertas=alertas, verificaciones_datos=[], generado_con_ia=False,
            observaciones_verificacion=observaciones,
        )

    def _consultar_claude(self, resultado: ResultadoConciliacion, ind: dict) -> dict:
        datos = carga_para_claude(resultado, ind)
        respuesta = self.cliente.beta.messages.create(
            model=self.modelo,
            max_tokens=16000,
            system=SISTEMA,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": self.esfuerzo, "format": {"type": "json_schema", "schema": ESQUEMA}},
            messages=[{
                "role": "user",
                "content": "Conciliación del día en JSON:\n\n" + json.dumps(datos, ensure_ascii=False, indent=1),
            }],
        )
        if respuesta.stop_reason == "refusal":
            raise RuntimeError("Claude declinó la solicitud")
        if respuesta.stop_reason == "max_tokens":
            raise RuntimeError("La respuesta de Claude quedó incompleta")
        texto = next(b.text for b in respuesta.content if b.type == "text")
        return json.loads(texto)


def carga_para_claude(resultado: ResultadoConciliacion, ind: dict) -> dict:
    """Datos que viajan a la API: identificados solo por cama, sin número de ingreso y con el texto libre depurado."""
    camas = []
    for exp in sorted(resultado.expedientes.values(), key=lambda e: e.cama):
        if not (exp.entrega or (exp.orbit and exp.orbit.ocupada) or exp.discrepancias):
            continue
        camas.append(_resumen_cama(exp, resultado.fecha))
    return {
        "fecha": resultado.fecha.isoformat(),
        "indicadores": ind,
        "camas": camas,
        "discrepancias": [
            {
                "id": d.id, "regla": d.regla, "titulo": d.titulo, "severidad": d.severidad.value,
                "categoria": d.categoria.value, "cama": d.cama or "UNIDAD",
                "detalle": limpiar_texto(d.detalle, 300), "impacto_cop": d.impacto_cop,
                "accion_sugerida_por_reglas": ACCION_POR_REGLA.get(d.regla, ("", "", ""))[0],
            }
            for d in resultado.discrepancias
        ],
    }


def _resumen_cama(exp: ExpedienteCama, fecha: date) -> dict:
    o, e = exp.orbit, exp.entrega
    return {
        "cama": exp.cama,
        "unidad": o.unidad if o else None,
        "estacion": e.estacion if e else None,
        "ocupada_orbit": o.ocupada if o else None,
        "criterio_orbit": o.criterio.value if o else None,
        "condicion_entrega": e.condicion.value if e else None,
        "dias_en_uci": (fecha - (o.fecha_ingreso_uci if o and o.fecha_ingreso_uci else e.fecha_ingreso_uci)).days
        if (o and o.fecha_ingreso_uci) or (e and e.fecha_ingreso_uci) else None,
        "vmi_orbit": o.vmi if o else None,
        "vasopresor_orbit": o.vasopresor if o else None,
        "ventilacion_entrega": e.ventilacion if e else None,
        "vasoactivos_entrega": e.vasoactivos if e else [],
        "trr": e.trr if e else None,
        "dispositivos": [
            {"tipo": d.tipo, "dias": (fecha - d.fecha_insercion).days if d.fecha_insercion else None,
             "justificado": bool(d.justificacion)}
            for d in (e.dispositivos if e else [])
        ],
        "antibioticos": e.antibioticos if e else [],
        "aislamiento": (e.aislamiento if e else None) or (o.aislamiento if o else None),
        "pendientes": [
            {"tipo": p.tipo, "descripcion": limpiar_texto(p.descripcion, 160),
             "dias": (fecha - p.fecha_solicitud).days if p.fecha_solicitud else None}
            for p in (e.pendientes if e else [])
        ],
        "nota": limpiar_texto(e.nota, 400) if e else "",
        "discrepancias": [d.id for d in exp.discrepancias],
    }


def priorizar(resultado: ResultadoConciliacion) -> list[dict]:
    """Una prioridad por cama y acción, ordenadas por la severidad acumulada."""
    grupos: dict[tuple, dict] = {}
    for d in resultado.discrepancias:
        accion, responsable, plazo = ACCION_POR_REGLA.get(d.regla, ("Revisar", "coordinacion", "hoy"))
        if d.regla == "R09" and d.categoria.value != "financiera":
            responsable = "medico_uci"
        clave = (d.cama or "UNIDAD", accion)
        g = grupos.setdefault(clave, {
            "cama": d.cama or "UNIDAD", "accion": accion, "responsable": responsable, "plazo": plazo,
            "motivo": [], "discrepancias": [], "_sev": 0,
        })
        g["motivo"].append(d.titulo)
        g["discrepancias"].append(d.id)
        g["_sev"] = max(g["_sev"], d.severidad.peso)

    peso_cama: dict[str, int] = {}
    for g in grupos.values():
        peso_cama[g["cama"]] = peso_cama.get(g["cama"], 0) + g["_sev"]
    orden_plazo = {p: i for i, p in enumerate(PLAZOS)}
    lista = sorted(
        grupos.values(),
        key=lambda g: (-g["_sev"], orden_plazo[g["plazo"]], -peso_cama[g["cama"]], -len(g["discrepancias"])),
    )
    for i, g in enumerate(lista, 1):
        g["orden"] = i
        g["motivo"] = "; ".join(dict.fromkeys(g["motivo"])) + "."
        del g["_sev"]
    return lista


def verificar(prioridades: list[dict], resultado: ResultadoConciliacion) -> tuple[list[dict], list[str]]:
    """Descarta lo que Claude cite y no exista en la conciliación, y avisa qué discrepancias graves quedaron sin prioridad."""
    camas = set(resultado.expedientes) | {"UNIDAD"}
    ids = {d.id: d for d in resultado.discrepancias}
    validas, observaciones = [], []
    for p in sorted(prioridades, key=lambda p: p.get("orden", 0)):
        inexistentes = [i for i in p["discrepancias"] if i not in ids]
        if p["cama"] not in camas:
            observaciones.append(f"Se descartó una prioridad sobre la cama «{p['cama']}», que no está en la conciliación.")
            continue
        if inexistentes:
            observaciones.append(f"Prioridad de {p['cama']}: se quitaron referencias inexistentes {', '.join(inexistentes)}.")
            p["discrepancias"] = [i for i in p["discrepancias"] if i in ids]
        if not p["discrepancias"]:
            observaciones.append(f"Se descartó una prioridad de {p['cama']} sin discrepancias que la sustenten.")
            continue
        validas.append(p)
    citadas = {i for p in validas for i in p["discrepancias"]}
    for d in resultado.discrepancias:
        if d.severidad.value in ("critica", "alta") and d.id not in citadas:
            observaciones.append(f"{d.id} ({d.titulo}, {d.cama or 'unidad'}) es {d.severidad.value} y no quedó en ninguna prioridad.")
    for i, p in enumerate(validas, 1):
        p["orden"] = i
    return validas, observaciones


def resumen_basico(ind: dict) -> str:
    c = ind["condicion"]
    sev = ind["discrepancias_por_severidad"]
    fin = ind["financiero"]
    return (
        f"{ind['camas_ocupadas']} de {ind['camas_en_censo']} camas ocupadas ({ind['ocupacion_pct']}%). "
        f"Según enfermería: {c['UCI']} en condición UCI, {c['UCIN']} UCIN y {c['PISO']} en condición piso "
        f"({ind['condicion_piso_pct']}%). {ind['ventilacion_invasiva']} con ventilación invasiva y "
        f"{ind['soporte_vasoactivo']} con soporte vasoactivo. El motor encontró {sev['critica']} discrepancias críticas, "
        f"{sev['alta']} altas, {sev['media']} medias y {sev['baja']} bajas. Estancia evitable estimada del día: "
        f"{pesos(fin['costo_estancia_evitable_dia_cop'])}; derecho de cama sin cargar: {pesos(fin['derecho_cama_sin_cargar_cop'])}."
    )
