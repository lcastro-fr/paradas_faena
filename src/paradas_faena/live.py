"""Last known line state in Redis, for the live monitor.

The opposite of EventStream in how it fails: that one buffers and retries, because an
event not delivered is an event lost. Here a failed write is skipped and never retried --
live state is worth nothing a second later, and the poll loop must not spend its budget
waiting on a Redis that is not answering. The cost is that the mirror goes stale during
an outage; REASSERT_SECONDS bounds the error.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
import time
from collections.abc import Callable, Iterable, Sequence

import redis

from paradas_faena.config import LiveConfig
from paradas_faena.events import InputEdge, LiveSpeed

log = logging.getLogger(__name__)

# A Redis that has stopped answering must not be reported once per tick.
_LOG_INTERVAL = 60.0


def build_live_client(url: str, timeout: float) -> redis.Redis:
    """The durable client waits 2 s, which on a one-second tick is two ticks lost to
    every call. The mirror gets a client of its own."""
    return redis.Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=timeout,
        socket_connect_timeout=timeout,
        health_check_interval=30,
    )


def _dumps(payload: dict[str, object]) -> str:
    return json.dumps(payload, separators=(",", ":"))


def puesto_field(ip: str, tag: str) -> str:
    """Keyed without `version`: with the wiring generation in the field, rewiring would
    leave the old one dangling forever. It travels inside the payload instead."""
    return f"{ip}|{tag}"


class LiveSpeedTracker:
    """Unlike SpeedTracker there is no deadband: that one is sparse because it feeds a
    time-weighted average, but a gauge that only moves when the frequency changes looks
    broken. A change of running state bypasses the throttle so the screen never lags the
    line stopping.
    """

    def __init__(self, ip: str, conversion: float, interval_seconds: float) -> None:
        self.ip = ip
        self._conversion = conversion
        self._interval = dt.timedelta(seconds=interval_seconds)
        self._last_ts: dt.datetime | None = None
        self._last_running: bool | None = None

    def reset(self) -> None:
        self._last_ts = None
        self._last_running = None

    def observe(
        self, frec: float, ts: dt.datetime, noria_running: bool | None
    ) -> list[LiveSpeed]:
        due = (
            self._last_ts is None
            or noria_running != self._last_running
            or ts - self._last_ts >= self._interval
        )
        if not due:
            return []
        self._last_ts = ts
        self._last_running = noria_running
        return [
            LiveSpeed(
                ip=self.ip,
                ts=ts,
                frec=float(frec),
                vel=float(frec) * self._conversion,
                noria_running=noria_running,
            )
        ]


class LiveMirror:
    """Shared by every PlcWorker thread. Never propagates an exception and never blocks
    longer than the client's timeout."""

    def __init__(self, client: redis.Redis, cfg: LiveConfig) -> None:
        self._redis = client
        self._cfg = cfg
        self._lock = threading.Lock()
        # The authoritative copy: Redis is only where it gets published, so a skipped
        # write does not lose it.
        self._puestos: dict[str, tuple[bool, str, bool]] = {}
        self._blocked_until = 0.0
        self._next_log = 0.0

    def seed(self, puestos: Iterable[tuple[str, str]]) -> None:
        """Adopt the state a previous run left behind, so a puesto that held the line
        across a restart keeps its real `since` instead of resetting to now."""
        known = {puesto_field(ip, tag) for ip, tag in puestos}
        try:
            stored = self._redis.hgetall(self._cfg.puestos_key)
        except Exception as exc:
            # Degraded, not fatal: the first resync of each puesto repopulates the hash,
            # losing only the exactness of `since`.
            log.error("espejo vivo: no se pudo leer el estado previo (%s)", exc)
            return

        stale: list[str] = []
        for field, raw in stored.items():
            if field not in known:
                stale.append(field)
                continue
            try:
                payload = json.loads(raw)
                self._puestos[field] = (
                    bool(payload["value"]),
                    str(payload["since"]),
                    bool(payload.get("exact", False)),
                )
            except Exception:
                log.warning("espejo vivo: estado ilegible para %s, se descarta", field)
                stale.append(field)

        if stale:
            self._flush(lambda pipe: pipe.hdel(self._cfg.puestos_key, *stale))
            log.info("espejo vivo: %d puestos obsoletos removidos", len(stale))
        if self._puestos:
            log.info("espejo vivo: %d puestos recuperados", len(self._puestos))

    def tick(
        self,
        ip: str,
        ts: dt.datetime,
        *,
        line: LiveSpeed | None = None,
        input_edges: Sequence[InputEdge] = (),
    ) -> None:
        """One pipeline, one round trip. A PLC with no news sends a single command."""
        stamp = ts.isoformat()
        entries = [(edge, self._advance(edge)) for edge in input_edges]

        def ops(pipe: redis.client.Pipeline) -> None:
            pipe.set(
                self._cfg.plc_key(ip),
                _dumps({"ip": ip, "ts": stamp, "up": True}),
                ex=int(self._cfg.ttl_seconds),
            )
            if line is not None:
                pipe.set(self._cfg.noria_key, _dumps(_noria_payload(line)))
            for edge, (since, exact) in entries:
                pipe.hset(
                    self._cfg.puestos_key,
                    puesto_field(edge.ip, edge.tag),
                    _dumps(
                        {
                            "ip": edge.ip,
                            "tag": edge.tag,
                            "version": edge.version,
                            "value": edge.value,
                            "ts": edge.ts.isoformat(),
                            "since": since,
                            "exact": exact,
                            "reason": edge.reason,
                        }
                    ),
                )

        self._flush(ops)

    def _advance(self, edge: InputEdge) -> tuple[str, bool]:
        """Where the current state started, and whether that instant is trustworthy.

        `exact` is false when a resync found a different value than the one mirrored: the
        flip happened somewhere inside the outage, so the screen shows a lower bound.
        """
        field = puesto_field(edge.ip, edge.tag)
        stamp = edge.ts.isoformat()
        previous = self._puestos.get(field)

        if edge.reason == "change" or previous is None:
            resolved = (stamp, edge.reason == "change")
        else:
            value, since, exact = previous
            # A re-assert is the same state restated, and a resync that agrees with the
            # mirror means the state held across the gap. Both keep the original start.
            resolved = (since, exact) if value == edge.value else (stamp, False)

        self._puestos[field] = (edge.value, resolved[0], resolved[1])
        return resolved

    def plc_down(self, ip: str, ts: dt.datetime, *, variador: bool) -> None:
        """Say it out loud instead of waiting for the TTL: one tick, not five seconds."""
        stamp = ts.isoformat()

        def ops(pipe: redis.client.Pipeline) -> None:
            pipe.set(
                self._cfg.plc_key(ip),
                _dumps({"ip": ip, "ts": stamp, "up": False}),
                ex=int(self._cfg.ttl_seconds),
            )
            if variador:
                # Unknown, not stopped. The screen must never read a dead variador as a
                # halted line.
                pipe.set(
                    self._cfg.noria_key,
                    _dumps(
                        {
                            "ip": ip,
                            "ts": stamp,
                            "running": None,
                            "frec": None,
                            "vel": None,
                        }
                    ),
                )

        self._flush(ops)

    def daemon_alive(self, ts: dt.datetime) -> None:
        payload = _dumps({"ts": ts.isoformat(), "up": True})
        self._flush(
            lambda pipe: pipe.set(
                self._cfg.daemon_key, payload, ex=int(self._cfg.ttl_seconds)
            )
        )

    def daemon_down(self) -> None:
        """Best effort, so a routine restart does not grey the board for the few seconds
        the TTL would take to say the same thing."""
        payload = _dumps({"ts": dt.datetime.now(dt.UTC).isoformat(), "up": False})
        self._flush(
            lambda pipe: pipe.set(
                self._cfg.daemon_key, payload, ex=int(self._cfg.ttl_seconds)
            )
        )

    def _flush(self, ops: Callable[[redis.client.Pipeline], None]) -> None:
        now = time.monotonic()
        with self._lock:
            if now < self._blocked_until:
                return
        try:
            pipe = self._redis.pipeline(transaction=False)
            ops(pipe)
            pipe.execute()
        except Exception as exc:
            # Broader than RedisError on purpose. A malformed payload raising TypeError
            # here would kill the PLC thread and, through the supervisor in __main__, the
            # whole process. Live state never justifies stopping data acquisition.
            self._trip(now, exc)

    def _trip(self, now: float, exc: Exception) -> None:
        with self._lock:
            self._blocked_until = now + self._cfg.retry_seconds
            due = now >= self._next_log
            if due:
                self._next_log = now + _LOG_INTERVAL
        if due:
            log.error(
                "espejo vivo: redis no responde (%s: %s); se omite por %.1fs",
                type(exc).__name__,
                exc,
                self._cfg.retry_seconds,
            )


def _noria_payload(line: LiveSpeed) -> dict[str, object]:
    return {
        "ip": line.ip,
        "ts": line.ts.isoformat(),
        "running": line.noria_running,
        "frec": line.frec,
        "vel": line.vel,
    }
