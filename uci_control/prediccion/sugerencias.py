"""Motor de sugerencias de gestión por paciente.

Convierte lo que dice el tablero de ORBIT en acciones concretas con responsable: qué
interconsulta pedir, qué pendiente destrabar, qué paciente puede salir de la cama UCI y qué
revisar con PROA. Cada sugerencia cita el dato que la disparó; ninguna reemplaza el
criterio del médico tratante.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .. import config
from ..conectores.comun import sin_tildes
from ..conectores.orbit_tablero import PacienteTablero
from ..privacidad import limpiar_texto
from .riesgo import EvaluacionPaciente, evaluar

_PRIORIDAD = {"inmediata": 0, "hoy": 1, "48h": 2, "semana": 3}

# Pendientes del texto libre que se pueden enrutar a un responsable.
_RUTAS_PENDIENTE = [
    (r"FONOAUDIOLOG", "Fonoaudiología"), (r"NUTRICI", "Nutrición"), (r"CIRUG", "Cirugía"),
    (r"TAC|TOMOGRAF|RESONANCIA|ECOGRAF|RADIOLOG|IMAGEN", "Imágenes"),
    (r"ECOCARDIO|CARDIOLOG", "Cardiología"), (r"AUTORIZ", "Autorizaciones"),
    (r"CULTIVO|PATOLOG|BIOPSIA|REPORTE", "Laboratorio / patología"),
    (r"TRASLADO|REMISI|REFERENCIA", "Referencia y contrarreferencia"),
    (r"NEUROCIRUG|NEUROLOG", "Neurología / neurocirugía"), (r"NEFROLOG|DIALISIS|HEMODIALISIS", "Nefrología"),
    (r"INFECTOLOG", "Infectología"), (r"HEMATOLOG|ONCOLOG", "Hemato-oncología"),
]

_PAGADOR_EXTERNO = ("GOVERNMENT", "MINISTERIO", "INTERNACIONAL", "EMBAJADA", "PARTICULAR", "INSURANCE")


@dataclass
class Sugerencia:
    cama: str
    tipo: str  # interconsulta, flujo, proa, seguridad, administrativa, pendiente
    servicio: str
    accion: str
    motivo: str
    prioridad: str  # inmediata, hoy, 48h, semana

    @property
    def orden(self) -> int:
        return _PRIORIDAD[self.prioridad]


def _tiene(p: PacienteTablero, *patrones: str) -> bool:
    texto = sin_tildes(p.texto).upper()
    return any(re.search(pat, texto) for pat in patrones)


def sugerir(p: PacienteTablero, ev: EvaluacionPaciente | None = None) -> list[Sugerencia]:
    ev = ev or evaluar(p)
    s: list[Sugerencia] = []

    def add(tipo, servicio, accion, motivo, prioridad):
        s.append(Sugerencia(p.cama, tipo, servicio, accion, motivo, prioridad))

    # Flujo de camas: el paciente que ya no necesita UCI bloquea una admisión.
    if p.criterio == "OTROS" and p.sin_soportes:
        add("flujo", "Gestor de camas", "Gestionar traslado a hospitalización hoy",
            f"ORBIT lo marca con criterio de piso y no tiene soportes; cada día en cama UCI equivale a "
            f"${ev.valor_dia:,.0f} facturados en un nivel que no corresponde y a una admisión bloqueada".replace(",", "."),
            "hoy")
    elif p.criterio == "UCIN" and p.sin_soportes and (p.news or 0) < 5:
        add("flujo", "Gestor de camas", "Evaluar paso a cuidado intermedio",
            "Criterio de intermedio, sin soportes y NEWS bajo", "48h")
    if p.criterio == "OTROS" and not p.sin_soportes:
        add("seguridad", "Médico UCI", "Corregir el criterio de atención en ORBIT",
            "Figura con criterio de piso pero tiene soporte vasoactivo o VMI", "inmediata")

    # Ventilación y sedación (paquete ABCDEF).
    dvmi = p.dias_vmi()
    if p.vmi and p.rass is not None and p.rass <= -4:
        add("seguridad", "Médico UCI", "Interrupción diaria de sedación y prueba de ventilación espontánea si cumple",
            f"VMI con RASS {p.rass:.0f}", "hoy")
    if p.vmi and dvmi is not None and dvmi >= config.DIAS_VMI_TRAQUEOSTOMIA and not _tiene(p, r"TRAQUEOSTOM"):
        add("interconsulta", "Cirugía de cabeza y cuello / cirugía general",
            "Valorar traqueostomía", f"{dvmi} días de ventilación mecánica sin traqueostomía registrada", "48h")

    # Antimicrobianos: PROA.
    for a in p.antimicrobianos:
        dias = (p.fecha - a.fecha_inicio).days if a.fecha_inicio else None
        if dias is not None and dias >= config.DIAS_ANTIBIOTICO_REVISAR:
            add("proa", "Infectología / PROA", f"Revisar duración y desescalamiento de {a.nombre.lower()}",
                f"Día {dias} de tratamiento", "hoy")
        elif a.restringido and dias is None:
            add("proa", "Infectología / PROA", f"Registrar fecha de inicio de {a.nombre.lower()}",
                "Antimicrobiano restringido sin fecha de inicio en ORBIT", "hoy")
    restringidos = [a for a in p.antimicrobianos if a.restringido]
    if len(restringidos) >= 2:
        add("proa", "Infectología / PROA", "Revisión conjunta del esquema antimicrobiano",
            f"{len(restringidos)} antimicrobianos de uso restringido simultáneos", "hoy")
    if p.antimicrobianos and not _tiene(p, r"CULTIVO", r"HEMOCULTIVO", r"UROCULTIVO", r"PANEL"):
        add("proa", "Médico UCI", "Documentar cultivos y su resultado en ORBIT",
            "Hay antimicrobiano activo y no hay cultivos registrados en el texto", "hoy")

    # Nutrición y deglución.
    if p.must_alto and not _tiene(p, r"SEGUIMIENTO POR NUTRICI", r"NUTRICION ENTERAL", r"TERAPIA NUTRICIONAL"):
        add("interconsulta", "Nutrición", "Interconsulta de soporte nutricional",
            "MUST con riesgo alto y sin plan nutricional registrado", "hoy")
    if _tiene(p, r"DOSIS TROFICA") and not p.vmi:
        add("interconsulta", "Nutrición", "Revisar progresión de la nutrición enteral a meta",
            "Nutrición enteral en dosis trófica", "48h")
    if _tiene(p, r"NADA VIA ORAL") and not p.vmi and not _tiene(p, r"VALORACION POR FONOAUDIOLOG\w* (REALIZADA|OK)"):
        add("interconsulta", "Fonoaudiología", "Valoración de deglución para iniciar vía oral",
            "Paciente sin VMI en nada vía oral", "hoy")

    # Movilidad, rehabilitación y PICS.
    if p.ims is not None and p.ims <= 3 and (p.dias_hospitalizacion or 0) >= 3 and not p.vasoactivo:
        add("interconsulta", "Fisioterapia / medicina física", "Plan de movilización temprana progresiva",
            f"IMS {p.ims} con {p.dias_hospitalizacion:.0f} días de estancia", "hoy")
    if (p.dias_hospitalizacion or 0) >= 7 and (p.vmi or p.must_alto):
        add("interconsulta", "Psicología / clínica PICS", "Tamizaje de síndrome post cuidado intensivo y apoyo a familia",
            "Estancia de más de 7 días con VMI o desnutrición", "semana")

    # Dolor.
    if (p.cpot is not None and p.cpot >= 3) or (p.eva is not None and p.eva > 4):
        add("seguridad", "Médico UCI", "Ajustar analgesia y revalorar en 1 hora",
            f"CPOT {p.cpot:.0f}" if p.cpot is not None and p.cpot >= 3 else f"EVA {p.eva:.0f}", "inmediata")

    # Pronóstico y cuidado paliativo.
    if ev.mortalidad.puntaje >= 0.5 or (p.oncologico and ev.mortalidad.puntaje >= 0.3):
        add("interconsulta", "Cuidado paliativo", "Conferencia familiar y definición de metas de cuidado",
            f"Mortalidad esperada por SOFA {ev.mortalidad.puntaje:.0%}" + (" en paciente oncológico" if p.oncologico else ""),
            "48h")

    # Estancia prolongada.
    if (p.dias_hospitalizacion or 0) >= config.DIAS_ESTANCIA_PROLONGADA:
        add("flujo", "Coordinación médica", "Presentar en revisión de estancia prolongada",
            f"{p.dias_hospitalizacion:.0f} días hospitalizado", "semana")
        if p.dependiente:
            add("interconsulta", "Trabajo social", "Planear egreso con red de apoyo y cuidador",
                f"Estancia prolongada con Barthel {p.barthel.lower()}", "semana")

    # Administrativo: pagador fuera de la red habitual.
    if p.pagador and any(k in p.pagador.upper() for k in _PAGADOR_EXTERNO):
        add("administrativa", "Autorizaciones / cartera", "Verificar garantía de pago y autorización vigente",
            f"Pagador: {p.pagador.title()}", "hoy")

    # Pendientes escritos en ORBIT, enrutados a un responsable.
    for pendiente in p.pendientes:
        texto = sin_tildes(pendiente).upper()
        servicio = next((srv for pat, srv in _RUTAS_PENDIENTE if re.search(pat, texto)), "Médico UCI")
        add("pendiente", servicio, "Destrabar pendiente", limpiar_texto(pendiente, 140), "hoy")

    vistos, unicas = set(), []
    for x in sorted(s, key=lambda x: x.orden):
        clave = (x.servicio, x.accion)
        if clave not in vistos:
            vistos.add(clave)
            unicas.append(x)
    return unicas
