from __future__ import annotations

from dataclasses import dataclass, field

from espejo.domain import Noria, now_ms


@dataclass(slots=True, frozen=True)
class Plc:
    ip: str
    nombre: str | None
    variador: bool
    vivo: bool = False

    def to_json(self) -> dict:
        return {
            "ip": self.ip,
            "nombre": self.nombre,
            "variador": self.variador,
            "alive": self.vivo,
        }


@dataclass(slots=True, frozen=True)
class Puesto:
    puesto_key: str
    label: str
    plc: str | None
    plc_ip: str
    tag: str
    orden: int | None
    segundos_hoy: float = 0.0
    paradas_hoy: int = 0
    # None mientras el PLC del puesto no contesta: desconocido, no "libre".
    activo: bool | None = None
    desde_ms: int | None = None
    exacto: bool = True

    def to_json(self) -> dict:
        return {
            "puesto_key": self.puesto_key,
            "label": self.label,
            "plc": self.plc,
            "plc_ip": self.plc_ip,
            "orden": self.orden,
            "activo": self.activo,
            "desde_ms": self.desde_ms,
            "exacto": self.exacto,
            "segundos_hoy": self.segundos_hoy,
            "paradas_hoy": self.paradas_hoy,
        }


@dataclass(slots=True, frozen=True)
class Fuentes:
    redis: str = "down"
    db: str = "down"
    db_edad_s: float | None = None
    orden_configurado: bool = False

    def to_json(self) -> dict:
        return {
            "redis": self.redis,
            "db": self.db,
            "db_edad_s": self.db_edad_s,
            "orden_configurado": self.orden_configurado,
        }


@dataclass(slots=True, frozen=True)
class Catalogo:
    plcs: tuple[Plc, ...] = ()
    objetivo_cab_h: float = 220.0
    orden_configurado: bool = False


@dataclass(slots=True, frozen=True)
class Totales:
    puestos: tuple[Puesto, ...] = ()
    at: float = 0.0


@dataclass(slots=True, frozen=True)
class Snapshot:
    objetivo_cab_h: float
    noria: Noria
    plcs: tuple[Plc, ...]
    puestos: tuple[Puesto, ...]
    fuentes: Fuentes
    daemon_vivo: bool = False
    server_epoch_ms: int = field(default_factory=now_ms)

    def to_json(self) -> dict:
        return {
            "server_epoch_ms": self.server_epoch_ms,
            "objetivo_cab_h": self.objetivo_cab_h,
            "daemon_alive": self.daemon_vivo,
            "noria": {
                "running": self.noria.running,
                "vel": self.noria.vel,
                "frec": self.noria.frec,
                "ts_ms": self.noria.ts_ms,
                "stale": self.noria.stale,
            },
            "plcs": [p.to_json() for p in self.plcs],
            "puestos": [p.to_json() for p in self.puestos],
            "totales": {
                "activos": sum(1 for p in self.puestos if p.activo),
                "segundos_hoy": round(sum(p.segundos_hoy for p in self.puestos), 1),
                "paradas_hoy": sum(p.paradas_hoy for p in self.puestos),
            },
            "fuentes": self.fuentes.to_json(),
        }
