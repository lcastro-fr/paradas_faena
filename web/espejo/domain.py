from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Protocol


def to_ms(valor: str | dt.datetime | None) -> int | None:
    """Los instantes viajan al browser en epoch ms: así no parsea zonas horarias."""
    if valor is None:
        return None
    if isinstance(valor, str):
        valor = dt.datetime.fromisoformat(valor)
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=dt.UTC)
    return int(valor.timestamp() * 1000)


def sse(event: str, payload: dict | str) -> str:
    """The whole frame travels through the queues, so a heartbeat and a delta can share
    one fan-out."""
    data = payload if isinstance(payload, str) else json.dumps(
        payload, separators=(",", ":")
    )
    return f"event: {event}\ndata: {data}\n\n"


def now_ms() -> int:
    return int(dt.datetime.now(dt.UTC).timestamp() * 1000)


@dataclass(slots=True, frozen=True)
class Noria:
    """`running` es tri-estado: None es "no sé", y nunca se pinta como detenida."""

    running: bool | None = None
    vel: float | None = None
    frec: float | None = None
    ts_ms: int | None = None
    stale: bool = True


@dataclass(slots=True, frozen=True)
class Rele:
    value: bool
    ts_ms: int
    since_ms: int
    exact: bool


@dataclass(slots=True, frozen=True)
class EstadoEspejo:
    ok: bool = False
    noria: Noria = field(default_factory=Noria)
    reles: dict[str, Rele] = field(default_factory=dict)      # "<ip>|<tag>" -> Rele
    plcs_vivos: dict[str, bool] = field(default_factory=dict)  # ip -> vivo
    daemon_vivo: bool = False


class Espejo(Protocol):
    async def leer(self, ips: list[str]) -> EstadoEspejo: ...


class Feed(Protocol):
    def subscribe(self): ...
    def broadcast(self, frame: str) -> None: ...
