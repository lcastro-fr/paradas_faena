from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Mapping, Sequence

from ..events import CounterIncrement, Heartbeat, InputEdge, NoriaStatusEdge, SpeedSample

log = logging.getLogger(__name__)


class CounterTracker:
    def __init__(self, ip: str, tags: Sequence[str]) -> None:
        self.ip = ip
        self._last: dict[str, int | None] = dict.fromkeys(tags)

    def reset(self) -> None:
        """Forget the last values so the next reading re-seeds without emitting.

        A delta measured across a disconnection of unknown length is not a stop count.
        """
        for tag in self._last:
            self._last[tag] = None

    def observe(
        self,
        values: Mapping[str, int],
        ts: dt.datetime,
        noria_running: bool | None,
        vel: float | None,
    ) -> list[CounterIncrement]:
        events: list[CounterIncrement] = []
        for tag, raw in values.items():
            new = int(raw)
            old = self._last.get(tag)

            if old is None:
                self._last[tag] = new
                continue
            if new == old:
                continue
            if new < old:
                log.warning(
                    "%s: contador %s retrocedio %d -> %d (reset del PLC?); re-sembrando",
                    self.ip,
                    tag,
                    old,
                    new,
                )
                self._last[tag] = new
                continue

            events.append(
                CounterIncrement(
                    ip=self.ip,
                    tag=tag,
                    ts=ts,
                    old_value=old,
                    new_value=new,
                    dif=new - old,
                    noria_running=noria_running,
                    vel=vel,
                )
            )
            self._last[tag] = new
        return events


class InputTracker:
    """Turns the InputStatus relay array into a timeline of transitions.

    Emits on a flip, and re-emits every reassert_seconds while a relay reads true. The
    re-asserts bound a live `true` segment, which is what lets the duration queries
    recognise a longer segment as a data gap and cap it.
    """

    def __init__(
        self, ip: str, index_to_tag: Mapping[int, str], reassert_seconds: float
    ) -> None:
        self.ip = ip
        self._index_to_tag = dict(index_to_tag)
        self._reassert = dt.timedelta(seconds=reassert_seconds)
        self._last_value: dict[str, bool] = {}
        self._last_written: dict[str, dt.datetime] = {}
        self._needs_resync = True
        self._warned_short_array = False

    @property
    def array_size(self) -> int:
        return max(self._index_to_tag) + 1 if self._index_to_tag else 0

    def reset(self) -> None:
        self._needs_resync = True

    def observe(self, array: Sequence[object], ts: dt.datetime) -> list[InputEdge]:
        if len(array) < self.array_size and not self._warned_short_array:
            log.error(
                "%s: el array de inputs devolvio %d elementos, se esperaban al menos "
                "%d; revisar input_index en counters_name",
                self.ip,
                len(array),
                self.array_size,
            )
            self._warned_short_array = True

        resync = self._needs_resync
        events: list[InputEdge] = []

        for index, tag in self._index_to_tag.items():
            if index >= len(array):
                continue
            value = bool(array[index])
            previous = self._last_value.get(tag)

            if resync or previous is None:
                reason = "resync"
            elif value != previous:
                reason = "change"
            elif value and ts - self._last_written.get(tag, ts) >= self._reassert:
                reason = "reassert"
            else:
                continue

            events.append(InputEdge(ip=self.ip, tag=tag, ts=ts, value=value, reason=reason))
            self._last_value[tag] = value
            self._last_written[tag] = ts

        self._needs_resync = False
        return events


class NoriaStatusTracker:
    def __init__(self, ip: str) -> None:
        self.ip = ip
        self._last: bool | None = None

    def reset(self) -> None:
        self._last = None

    def observe(self, running: bool, ts: dt.datetime) -> list[NoriaStatusEdge]:
        running = bool(running)
        if self._last is not None and running == self._last:
            return []
        self._last = running
        return [NoriaStatusEdge(ip=self.ip, ts=ts, running=running)]


# Subtracting two decimal readings lands a hair below an exactly-equal threshold
# (10.2 - 10.0 == 0.1999999999999993), so a change of precisely the configured deadband
# would not register. A relative tolerance makes the configured value mean what it says.
_DEADBAND_TOL = 1e-9


class SpeedTracker:
    """Persists a frequency reading on a deadband, or after max_interval_seconds.

    The live value is still updated every poll; it is what gets stamped onto a stop.
    """

    def __init__(
        self,
        ip: str,
        conversion: float,
        deadband_hz: float,
        max_interval_seconds: float,
    ) -> None:
        self.ip = ip
        self._conversion = conversion
        self._deadband = deadband_hz
        self._max_interval = dt.timedelta(seconds=max_interval_seconds)
        self._last_frec: float | None = None
        self._last_ts: dt.datetime | None = None

    def reset(self) -> None:
        self._last_frec = None
        self._last_ts = None

    def to_velocity(self, frec: float) -> float:
        return frec * self._conversion

    def observe(
        self, frec: float, ts: dt.datetime, noria_running: bool | None
    ) -> list[SpeedSample]:
        frec = float(frec)
        due = (
            self._last_frec is None
            or self._last_ts is None
            or abs(frec - self._last_frec) >= self._deadband * (1.0 - _DEADBAND_TOL)
            or ts - self._last_ts >= self._max_interval
        )
        if not due:
            return []
        self._last_frec = frec
        self._last_ts = ts
        return [
            SpeedSample(
                ip=self.ip,
                ts=ts,
                frec=frec,
                vel=self.to_velocity(frec),
                noria_running=noria_running,
            )
        ]


class HeartbeatTracker:
    """Proof of life, so a quiet period can be told apart from a daemon outage."""

    def __init__(self, ip: str, interval_seconds: float) -> None:
        self.ip = ip
        self._interval = dt.timedelta(seconds=interval_seconds)
        self._last: dt.datetime | None = None

    def reset(self) -> None:
        self._last = None

    def observe(self, ts: dt.datetime) -> list[Heartbeat]:
        if self._last is not None and ts - self._last < self._interval:
            return []
        self._last = ts
        return [Heartbeat(ip=self.ip, ts=ts)]
