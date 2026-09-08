from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field, fields
from typing import Any, ClassVar


def new_uid() -> str:
    """A unique id for events whose table has no natural key."""
    return str(uuid.uuid4())


@dataclass(frozen=True, slots=True)
class Event:
    kind: ClassVar[str] = "event"

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, dt.date | dt.time):
                out[f.name] = value.isoformat()
            else:
                out[f.name] = value
        return out


@dataclass(frozen=True, slots=True)
class CounterIncrement(Event):
    """A stop counter moved: one workstation requested a line stop."""

    kind: ClassVar[str] = "counter"

    ip: str
    tag: str
    ts: dt.datetime
    old_value: int
    new_value: int
    dif: int
    noria_running: bool | None
    vel: float | None
    event_uid: str = field(default_factory=new_uid)


@dataclass(frozen=True, slots=True)
class InputEdge(Event):
    """The state of a workstation's stop relay at an instant.

    reason is `change` for a real flip, `reassert` for the periodic re-write while the
    relay reads true, `resync` for the first reading after a (re)connect.
    """

    kind: ClassVar[str] = "input"

    ip: str
    tag: str
    ts: dt.datetime
    value: bool
    reason: str = "change"


@dataclass(frozen=True, slots=True)
class SpeedSample(Event):
    kind: ClassVar[str] = "speed"

    ip: str
    ts: dt.datetime
    frec: float
    vel: float
    noria_running: bool | None
    event_uid: str = field(default_factory=new_uid)


@dataclass(frozen=True, slots=True)
class NoriaStatusEdge(Event):
    kind: ClassVar[str] = "noria"

    ip: str
    ts: dt.datetime
    running: bool


@dataclass(frozen=True, slots=True)
class Heartbeat(Event):
    kind: ClassVar[str] = "heartbeat"

    ip: str
    ts: dt.datetime


@dataclass(frozen=True, slots=True)
class SessionClosed(Event):
    """The tipificador CSV appeared and was parsed: the faena day is over."""

    kind: ClassVar[str] = "session"

    fecha: dt.date
    hora_inicio: dt.time
    hora_fin: dt.time
    registros: int


_KINDS: dict[str, type[Event]] = {
    cls.kind: cls
    for cls in (
        CounterIncrement,
        InputEdge,
        SpeedSample,
        NoriaStatusEdge,
        Heartbeat,
        SessionClosed,
    )
}

_DATETIME_FIELDS = {"ts"}
_DATE_FIELDS = {"fecha"}
_TIME_FIELDS = {"hora_inicio", "hora_fin"}


def from_json(payload: dict[str, Any]) -> Event:
    kind = payload["kind"]
    cls = _KINDS[kind]
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        value = payload[f.name]
        if f.name in _DATETIME_FIELDS:
            value = dt.datetime.fromisoformat(value)
        elif f.name in _DATE_FIELDS:
            value = dt.date.fromisoformat(value)
        elif f.name in _TIME_FIELDS:
            value = dt.time.fromisoformat(value)
        kwargs[f.name] = value
    return cls(**kwargs)
