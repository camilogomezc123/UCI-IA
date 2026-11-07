"""Barreras de egreso: por qué un paciente que ya puede salir sigue en la cama UCI.

Se codifican como los "delay codes" de los programas de flujo (SAFER, Red2Green): cada barrera
tiene un responsable y una acción de escalamiento. Se leen del texto libre de ORBIT y de los
pendientes; lo que no se reconoce queda como "sin barrera registrada", que también es un hallazgo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..conectores.comun import sin_tildes
from ..conectores.orbit_tablero import PacienteTablero


@dataclass(frozen=True)
class Barrera:
    codigo: str
    nombre: str
    responsable: str
    escalamiento: str


CATALOGO = [
    (r"CAMA (EN |DE )?(PISO|HOSPITALIZ)|ESPERA (DE )?CAMA|SIN CAMA|TRASLADO A (PISO|HOSPITALIZ)",
     Barrera("B01", "Espera de cama en hospitalización", "Gestor de camas", "Priorizar en el comité de camas; activar sala de egresos o cama puente")),
    (r"AUTORIZ",
     Barrera("B02", "Autorización del asegurador", "Autorizaciones", "Escalar al asegurador con el tiempo de espera; registrar glosa potencial")),
    (r"REMISI|CONTRARREFER|REFERENCIA|TRASLADO A OTRA|OTRA INSTITUCI",
     Barrera("B03", "Remisión a otra institución", "Referencia y contrarreferencia", "Escalar al asegurador y a la red; documentar cada intento")),
    (r"\bTAC\b|TOMOGRAF|RESONANCIA|ECOGRAF|ECOCARDIO|ENDOSCOP|ESTUDIO|PROCEDIMIENTO|CIRUGIA|QUIRURG|BIOPSIA",
     Barrera("B04", "Estudio o procedimiento pendiente", "Médico UCI", "Pedir hora confirmada hoy o hacerlo desde hospitalización")),
    (r"VALORACION|INTERCONSULTA|CONCEPTO",
     Barrera("B05", "Valoración de especialista pendiente", "Coordinación médica", "Llamar al especialista; la valoración puede seguir en piso")),
    (r"FAMILI|CUIDADOR|SOCIAL|RED DE APOYO|HOGAR|ALBERGUE",
     Barrera("B06", "Familia o red de apoyo", "Trabajo social", "Reunión con la familia hoy; plan de egreso con cuidador")),
    (r"AISLAMIENTO|KPC|BLEE|CONTACTO|GOTAS|AEROSOL",
     Barrera("B07", "Requiere habitación de aislamiento", "Gestor de camas / infecciones", "Reservar habitación individual en piso")),
    (r"TRAQUEOSTOM|GASTROSTOM|SONDA|DRENAJE|OXIGENO DOMICILIARIO",
     Barrera("B08", "Dispositivo que el piso debe recibir", "Enfermería", "Confirmar que el servicio receptor maneja el dispositivo; educación de enfermería")),
]

SIN_BARRERA = Barrera("B00", "Sin barrera registrada", "Médico UCI", "Definir el egreso en la ronda de la mañana o registrar la barrera")


def barreras(p: PacienteTablero) -> list[Barrera]:
    """Barreras escritas como pendientes en ORBIT. El resto del texto (diagnósticos, antecedentes)
    no se usa porque menciona cirugías y estudios que ya pasaron."""
    texto = sin_tildes(" \n".join(p.pendientes)).upper()
    halladas = [b for patron, b in CATALOGO if re.search(patron, texto)]
    return halladas or [SIN_BARRERA]
