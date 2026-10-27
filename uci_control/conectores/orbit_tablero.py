"""Lectura del tablero diario de ORBIT ("Reporte_TableroControlUCI1<fecha>.xlsx").

El tablero llega adjunto a las entregas médicas de cada unidad. Trae una fila por paciente
con 29 columnas: escalas (NEWS, SOFA, RASS, CPOT, EVA, Barthel, IMS, MUST), soportes,
criterio de atención y tres campos de texto libre (diagnósticos, observaciones y metas)
donde el médico escribe antibióticos con fecha de inicio, parámetros ventilatorios,
cultivos y pendientes.

El paciente queda identificado por cama y por un seudónimo (hash con sal) del documento.
El nombre y el documento nunca salen de este módulo.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ..fechas import leer_fecha
from .comun import normalizar_cama, sin_tildes

# Antimicrobianos que se buscan en el texto libre. Los de uso restringido piden revisión de PROA.
ANTIMICROBIANOS = {
    "MEROPENEM": True, "IMIPENEM": True, "ERTAPENEM": True, "VANCOMICINA": True, "LINEZOLID": True,
    "COLISTINA": True, "POLIMIXINA": True, "TIGECICLINA": True, "CEFTAZIDIMA AVIBACTAM": True,
    "CEFTOLOZANO": True, "DAPTOMICINA": True, "CASPOFUNGINA": True, "ANIDULAFUNGINA": True,
    "MICAFUNGINA": True, "VORICONAZOL": True, "ANFOTERICINA": True,
    "PIPERACILINA": False, "CEFEPIME": False, "CEFTRIAXONA": False, "CEFAZOLINA": False,
    "AMPICILINA": False, "CLINDAMICINA": False, "METRONIDAZOL": False, "CIPROFLOXACINA": False,
    "LEVOFLOXACINA": False, "AZITROMICINA": False, "CLARITROMICINA": False, "OXACILINA": False,
    "TRIMETOPRIM": False, "FLUCONAZOL": False, "ACICLOVIR": False, "GANCICLOVIR": False,
    "GENTAMICINA": False, "AMIKACINA": False, "DOXICICLINA": False, "NITROFURANTOINA": False,
}

_SOFA_PCT = re.compile(r"(\d+)\s*(?:-\s*(\d+))?\s*%")
_TIEMPO = re.compile(r"(?:(\d+)\s*dias?)?\s*(?:(\d+)\s*hrs?)?\s*(?:(\d+)\s*min)?", re.I)
_FECHA_INICIO = re.compile(r"(?:INICIO|INICIA|DESDE|DIA\s*1)\D{0,25}?(\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?)", re.I)
_INTUBACION = re.compile(r"INTUBACI[OÓ]N[^:\n]{0,30}:?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})", re.I)
_PENDIENTE = re.compile(r"(PENDIENTE[^\n.;]{3,120})", re.I)


@dataclass
class Antimicrobiano:
    nombre: str
    fecha_inicio: date | None
    restringido: bool


@dataclass
class PacienteTablero:
    cama: str
    fecha: date
    seudonimo: str | None
    edad: int | None
    sexo: str | None
    pagador: str | None
    cie10: list[str]
    oncologico: bool
    criterio: str  # UCI, UCIN u OTROS (hospitalización o alta)
    especialidades: list[str]
    soporte_hemodinamico: str | None
    soporte_ventilatorio: str | None
    movilizacion_temprana: str | None
    news: float | None
    sofa_mortalidad: float | None  # mortalidad estimada por SOFA que reporta ORBIT, 0 a 1
    barthel: str | None
    ims: int | None
    rass: float | None
    cpot: float | None
    eva: float | None
    must_alto: bool | None
    riesgos: list[str]
    dias_hospitalizacion: float | None
    antimicrobianos: list[Antimicrobiano] = field(default_factory=list)
    fecha_intubacion: date | None = None
    pendientes: list[str] = field(default_factory=list)
    texto: str = ""  # diagnósticos + observaciones + metas + dieta, para reglas de texto

    @property
    def vmi(self) -> bool:
        return bool(self.soporte_ventilatorio) and "VMI" in self.soporte_ventilatorio.upper()

    @property
    def vasoactivo(self) -> bool:
        s = (self.soporte_hemodinamico or "").upper()
        return any(k in s for k in ("VASOPRES", "INOTR"))

    @property
    def sin_soportes(self) -> bool:
        vent = (self.soporte_ventilatorio or "").upper()
        return not self.vasoactivo and not self.vmi and "ALTO FLUJO" not in vent and "VMNI" not in vent

    @property
    def dependiente(self) -> bool:
        """Barthel moderado, severo o total (el leve no cuenta)."""
        b = sin_tildes(self.barthel or "").upper()
        return bool(re.search(r"(?<!IN)DEPENDIENTE", b)) and "LEVE" not in b

    def dias_vmi(self) -> int | None:
        if self.vmi and self.fecha_intubacion:
            return (self.fecha - self.fecha_intubacion).days
        return None


def seudonimo(documento: object) -> str | None:
    """Hash con sal del documento. La sal vive fuera del repositorio (variable de entorno)."""
    if documento is None or str(documento).strip() in ("", "nan"):
        return None
    sal = os.environ.get("UCI_SAL_SEUDONIMO", "")
    return hashlib.sha256((sal + str(documento).strip()).encode()).hexdigest()[:12]


def _num(valor) -> float | None:
    try:
        v = float(str(valor).replace(",", "."))
        return None if v != v else v  # NaN
    except (TypeError, ValueError):
        return None


def _texto(valor) -> str:
    if valor is None:
        return ""
    s = str(valor)
    return "" if s == "nan" else s


def sofa_a_mortalidad(valor) -> float | None:
    """ORBIT exporta el SOFA como mortalidad esperada ("32-35%"). Se toma el punto medio."""
    m = _SOFA_PCT.search(_texto(valor))
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2) or m.group(1))
    return (a + b) / 200


def tiempo_a_dias(valor) -> float | None:
    texto = _texto(valor)
    if not texto.strip():
        return None
    m = _TIEMPO.search(texto)
    if not m or not any(m.groups()):
        return None
    d, h, mi = (int(x) if x else 0 for x in m.groups())
    return round(d + h / 24 + mi / 1440, 2)


def leer_antimicrobianos(texto: str, referencia: date) -> list[Antimicrobiano]:
    """Busca antimicrobianos activos y su fecha de inicio en el texto libre.

    Descarta las menciones en la sección de antibióticos previos y las que dicen "NINGUNO".
    """
    t = sin_tildes(texto).upper()
    t = re.split(r"ANTIBIOTICOS? PREVIOS|ANTIMICROBIANOS? PREVIOS", t)[0]
    hallados: dict[str, Antimicrobiano] = {}
    for linea in t.splitlines():
        for nombre, restringido in ANTIMICROBIANOS.items():
            if nombre in linea and nombre not in hallados:
                if re.search(rf"(SUSPEND|RETIR|FINALIZ|COMPLET)\w*\D{{0,20}}{nombre}|{nombre}\D{{0,30}}(SUSPEND|FINALIZ|COMPLET)", linea):
                    continue
                m = _FECHA_INICIO.search(linea) or re.search(r"(\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?)", linea)
                fecha = leer_fecha(m.group(1), referencia)[0] if m else None
                if fecha and fecha > referencia:
                    fecha = None
                hallados[nombre] = Antimicrobiano(nombre.title(), fecha, restringido)
    return list(hallados.values())


def leer_tablero(ruta: str | Path) -> list[PacienteTablero]:
    import pandas as pd

    crudo = pd.read_excel(ruta, header=None)
    fecha = None
    for celda in map(str, crudo.iloc[:3, 0]):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", celda)
        if m:
            fecha = date.fromisoformat(m.group(1))
    if fecha is None:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", Path(ruta).name)
        fecha = date.fromisoformat(m.group(1)) if m else date.today()

    fila_encabezado = next(i for i in range(6) if "UBICACI" in sin_tildes(" ".join(map(str, crudo.iloc[i]))).upper())
    df = pd.read_excel(ruta, header=fila_encabezado)
    df.columns = [sin_tildes(str(c)).strip().upper() for c in df.columns]

    pacientes = []
    for _, f in df.iterrows():
        cama = normalizar_cama(_texto(f.get("UBICACION")))
        if not cama:
            continue
        criterio_txt = sin_tildes(_texto(f.get("CRITERIO DE ATENCION"))).upper()
        criterio = "UCIN" if "INTERMEDIO" in criterio_txt else "UCI" if "INTENSIVO" in criterio_txt else "OTROS"
        diagnosticos = _texto(f.get("DIAGNOSTICOS"))
        observaciones = _texto(f.get("OBSERVACIONES"))
        metas = _texto(f.get("METAS CLINICAS DIARIAS"))
        dieta = _texto(f.get("DIETA"))
        texto = "\n".join([diagnosticos, observaciones, metas, dieta])
        ims = re.search(r"(\d+)", _texto(f.get("DE MOVILIDAD")))
        must = sin_tildes(_texto(f.get("MUST"))).upper()
        intub = _INTUBACION.search(sin_tildes(texto).upper())
        onco = sin_tildes(_texto(f.get("DIAGNOSTICO ONCOLOGICO"))).upper()
        pacientes.append(PacienteTablero(
            cama=cama,
            fecha=fecha,
            seudonimo=seudonimo(f.get("DOCUMENTO PACIENTE")),
            edad=int(_num(f.get("EDAD"))) if _num(f.get("EDAD")) is not None else None,
            sexo=_texto(f.get("SEXO")) or None,
            pagador=_texto(f.get("EAPB")).strip() or None,
            cie10=re.findall(r"\b([A-Z]\d{2}[0-9X])\b", _texto(f.get("CIE10"))),
            oncologico=bool(onco) and "NO APLICA" not in onco,
            criterio=criterio,
            especialidades=[e.strip() for e in _texto(f.get("ESPECIALIDAD A CARGO")).split("\n") if e.strip()],
            soporte_hemodinamico=_texto(f.get("SOPORTE HEMODINAMICO")) or None,
            soporte_ventilatorio=_texto(f.get("SOPORTE VENTILATORIO")) or None,
            movilizacion_temprana=_texto(f.get("MOVILIZACION TEMPRANA")) or None,
            news=_num(f.get("NEWS")),
            sofa_mortalidad=sofa_a_mortalidad(f.get("SOFA")),
            barthel=_texto(f.get("BARTHEL")) or None,
            ims=int(ims.group(1)) if ims else None,
            rass=_num(f.get("RASS")),
            cpot=_num(f.get("CPOT")),
            eva=_num(f.get("EVA")),
            must_alto=("RIESGO ALTO" in must) if must else None,
            riesgos=[r.strip() for r in _texto(f.get("RIESGOS DEL PACIENTE")).split("\n") if r.strip()],
            dias_hospitalizacion=tiempo_a_dias(f.get("TIEMPO DE HOSPITALIZACION")),
            antimicrobianos=leer_antimicrobianos(observaciones + "\n" + diagnosticos, fecha),
            fecha_intubacion=leer_fecha(intub.group(1), fecha)[0] if intub else None,
            pendientes=[re.sub(r"\s+", " ", p).strip() for p in _PENDIENTE.findall(texto)],
            texto=texto,
        ))
    return pacientes
