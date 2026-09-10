"""The only thread that talks to PostgreSQL."""

from __future__ import annotations

import logging
import threading
import time

import psycopg
from redis.exceptions import RedisError

from paradas_faena.backoff import Backoff
from paradas_faena.config import Config
from paradas_faena.db.repository import Repository
from paradas_faena.events import (
    CounterIncrement,
    Event,
    Heartbeat,
    InputEdge,
    NoriaStatusEdge,
    SessionClosed,
    SpeedSample,
)
from paradas_faena.runner import ManagedThread
from paradas_faena.stream import EventStream

log = logging.getLogger(__name__)

# A dead socket or a database refusing connections: the batch itself is fine, so it must
# not be acknowledged.
_CONNECTION_ERRORS = (psycopg.OperationalError, psycopg.InterfaceError)

_STATS_INTERVAL = 60.0


class Writer(ManagedThread):
    def __init__(
        self, stream: EventStream, config: Config, shutdown: threading.Event
    ) -> None:
        super().__init__("writer")
        self._stream = stream
        self._cfg = config
        self._shutdown = shutdown
        self._repo: Repository | None = None
        self._written = 0
        self._rejected = 0
        # Starts true so a restart picks up whatever the previous run read but never
        # acknowledged.
        self._maybe_pending = True
        self._redis_down = False
        self._last_stats = time.monotonic()

    def run(self) -> None:
        backoff = Backoff(self._cfg.backoff_initial_seconds, self._cfg.backoff_max_seconds)
        deadline: float | None = None

        while True:
            if self._shutdown.is_set() and deadline is None:
                deadline = time.monotonic() + self._cfg.shutdown_grace_seconds
                log.info("writer: drenando el stream")
            if deadline is not None and time.monotonic() > deadline:
                break

            if self._repo is None and not self._connect(backoff):
                continue

            try:
                batch = self._next_batch(draining=deadline is not None)
            except RedisError as exc:
                self._redis_down = True
                delay = backoff.sleep(self._shutdown)
                log.error("writer: redis no responde (%s); reintento en ~%.1fs", exc, delay)
                continue
            if self._redis_down:
                # Repeated failures followed by silence is ambiguous; say so explicitly.
                self._redis_down = False
                backoff.reset()
                log.warning("writer: redis restablecido")

            if not batch:
                if deadline is not None:
                    break
                continue

            self._process_batch(batch)
            self._maybe_log_stats()

        if self._repo is not None:
            self._repo.close()
        log.info(
            "writer terminado: %d filas escritas, %d eventos rechazados, "
            "%d descartados por buffer lleno",
            self._written,
            self._rejected,
            self._stream.dropped,
        )

    def _next_batch(self, *, draining: bool) -> list[tuple[str, Event]]:
        if self._maybe_pending:
            batch = self._stream.read_pending(self._cfg.batch_max_events)
            if batch:
                return batch
            self._maybe_pending = False
        block = 50 if draining else self._cfg.redis.block_ms
        return self._stream.read_new(self._cfg.batch_max_events, block)

    def _connect(self, backoff: Backoff) -> bool:
        try:
            self._repo = Repository.connect(self._cfg.db)
        except psycopg.Error as exc:
            delay = backoff.sleep(self._shutdown)
            log.error(
                "writer: no se pudo conectar a la db: %s; reintento en ~%.1fs", exc, delay
            )
            return False
        backoff.reset()
        log.info("writer: conectado a la base de datos")
        return True

    def _drop_connection(self) -> None:
        if self._repo is not None:
            self._repo.rollback()
            self._repo.close()
            self._repo = None

    def _process_batch(self, batch: list[tuple[str, Event]]) -> None:
        events = [event for _, event in batch]
        ids = [message_id for message_id, _ in batch]
        try:
            self._flush(events)
        except _CONNECTION_ERRORS as exc:
            # Not acknowledging is the whole retry mechanism: the entries stay pending in
            # Redis and the next pending read returns exactly them.
            log.error(
                "writer: conexion perdida con %d eventos en vuelo (%s)", len(events), exc
            )
            self._drop_connection()
            self._maybe_pending = True
            return
        except psycopg.Error as exc:
            log.error(
                "writer: error de datos en un lote de %d (%s); aislando", len(events), exc
            )
            self._rollback()
            if not self._isolate(events):
                # Isolation was cut short by a connection failure. Leave the whole batch
                # pending; the halves already committed absorb their own redelivery.
                self._maybe_pending = True
                return
        self._stream.ack(ids)

    def _rollback(self) -> None:
        if self._repo is not None:
            self._repo.rollback()

    def _flush(self, events: list[Event]) -> None:
        assert self._repo is not None
        counters: list[CounterIncrement] = []
        inputs: list[InputEdge] = []
        speeds: list[SpeedSample] = []
        noria: list[NoriaStatusEdge] = []
        beats: list[Heartbeat] = []
        sessions: list[SessionClosed] = []

        for event in events:
            if isinstance(event, InputEdge):
                inputs.append(event)
            elif isinstance(event, Heartbeat):
                beats.append(event)
            elif isinstance(event, SpeedSample):
                speeds.append(event)
            elif isinstance(event, CounterIncrement):
                counters.append(event)
            elif isinstance(event, NoriaStatusEdge):
                noria.append(event)
            elif isinstance(event, SessionClosed):
                sessions.append(event)
            else:
                log.error("writer: evento desconocido %r", event)

        self._repo.insert_input_edges(inputs)
        self._repo.insert_counters(counters)
        self._repo.insert_speed(speeds)
        self._repo.insert_noria_status(noria)
        self._repo.insert_heartbeats(beats)
        self._repo.upsert_generales(sessions)
        self._repo.commit()
        self._written += len(events)

    def _isolate(self, batch: list[Event]) -> bool:
        """Binary-split a batch the database rejects to find the offending event.

        Everything writable is written and the bad event is parked in the rejected
        stream, rather than retried forever with the whole batch stuck behind it.
        Returns False if a connection failure cut the search short.
        """
        pending: list[list[Event]] = [batch]
        while pending:
            if self._repo is None:
                return False
            current = pending.pop()
            try:
                self._flush(current)
            except _CONNECTION_ERRORS as exc:
                log.error(
                    "writer: conexion perdida aislando %d eventos: %s", len(current), exc
                )
                self._drop_connection()
                return False
            except psycopg.Error as exc:
                self._rollback()
                if len(current) == 1:
                    log.error(
                        "writer: evento rechazado por la db: %r (%s)", current[0], exc
                    )
                    self._stream.reject(current[0], str(exc))
                    self._rejected += 1
                else:
                    mid = len(current) // 2
                    pending.append(current[mid:])
                    pending.append(current[:mid])
        return True

    def _maybe_log_stats(self) -> None:
        now = time.monotonic()
        if now - self._last_stats < _STATS_INTERVAL:
            return
        self._last_stats = now
        log.info(
            "writer: %d filas escritas, pendientes=%d, buffer=%d, "
            "rechazados=%d, descartados=%d",
            self._written,
            self._stream.pending_count(),
            self._stream.buffered,
            self._rejected,
            self._stream.dropped,
        )
