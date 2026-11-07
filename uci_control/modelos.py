"""Estructuras de datos comunes a las tres fuentes y al motor de conciliación."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Condicion(str, Enum):
    UCI = "UCI"
    UCIN = "UCIN"
    PISO = "PISO"
    DESCONOCIDA = "DESCONOCIDA"


class Severidad(str, Enum):
    CRITICA = "critica"
    ALTA = "alta"
    MEDIA = "media"
    BAJA = "baja"

    @property
    def peso(self) -> int:
        return {"critica": 4, "alta": 3, "media": 2, "baja": 1}[self.value]


class Categoria(str, Enum):
    CLINICA = "clinica"
    DATOS = "datos"
    FLUJO = "flujo"
    FINANCIERA = "financiera"
    SEGURIDAD = "seguridad"


@dataclass
class RegistroOrbit:
    """Una cama del censo de ORBIT en una fecha."""

    cama: str
    unidad: str
    fecha: date
    ocupada: bool
    episodio: str | None = None
    fecha_ingreso_hospital: date | None = None
    fecha_ingreso_uci: date | None = None
    criterio: Condicion = Condicion.DESCONOCIDA
    vmi: bool | None = None
    vasopresor: bool | None = None
    news: str | None = None
    sofa: str | None = None
    aislamiento: str | None = None


@dataclass
class Dispositivo:
    tipo: str
    fecha_insercion: date | None = None
    justificacion: str | None = None


@dataclass
class Pendiente:
    tipo: str
    descripcion: str
    fecha_solicitud: date | None = None


@dataclass
class EntregaCama:
    """Lo que la entrega de turno dice de una cama."""

    cama: str
    estacion: str
    fecha: date | None
    turno: str | None
    episodio: str | None = None
    fecha_ingreso_uci: date | None = None
    condicion: Condicion = Condicion.DESCONOCIDA
    ventilacion: str | None = None
    vmi: bool | None = None
    vasoactivos: list[str] = field(default_factory=list)
    trr: bool | None = None
    dispositivos: list[Dispositivo] = field(default_factory=list)
    antibioticos: list[str] = field(default_factory=list)
    aislamiento: str | None = None
    pendientes: list[Pendiente] = field(default_factory=list)
    nota: str = ""
    advertencias: list[str] = field(default_factory=list)
    fuente: str = ""


@dataclass
class EntregaEstacion:
    """Un correo de entrega de turno ya leído: encabezado, camas y alertas del mensaje."""

    estacion: str
    fecha: date | None
    turno: str | None
    camas: list[EntregaCama]
    alertas: list[str] = field(default_factory=list)
    fuente: str = ""


@dataclass
class CargoCosto:
    episodio: str | None
    cama: str | None
    fecha: date
    concepto: str
    valor: float


@dataclass
class Discrepancia:
    regla: str
    titulo: str
    severidad: Severidad
    categoria: Categoria
    detalle: str
    cama: str | None = None
    episodio: str | None = None
    fuentes: list[str] = field(default_factory=list)
    impacto_cop: float | None = None
    id: str = ""


@dataclass
class ExpedienteCama:
    """Todo lo que las tres fuentes dicen de una cama en la fecha del informe."""

    cama: str
    orbit: RegistroOrbit | None = None
    entrega: EntregaCama | None = None
    cargos: list[CargoCosto] = field(default_factory=list)
    discrepancias: list[Discrepancia] = field(default_factory=list)

    @property
    def episodio(self) -> str | None:
        if self.orbit and self.orbit.episodio:
            return self.orbit.episodio
        return self.entrega.episodio if self.entrega else None

    @property
    def condicion(self) -> Condicion:
        if self.entrega and self.entrega.condicion != Condicion.DESCONOCIDA:
            return self.entrega.condicion
        return self.orbit.criterio if self.orbit else Condicion.DESCONOCIDA
