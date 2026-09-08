"""Redis Streams transport between the PLC readers and the database writer."""

from __future__ import annotations

import json
import logging
import time
from collections import deque

import redis
from redis.exceptions import RedisError, ResponseError

from .events import Event, from_json

log = logging.getLogger(__name__)

_PAYLOAD_FIELD = "d"

_RETRY_INTERVAL = 5.0
_SOCKET_TIMEOUT = 2.0


def build_client(url: str) -> redis.Redis:
    return redis.Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=_SOCKET_TIMEOUT,
        socket_connect_timeout=_SOCKET_TIMEOUT,
        health_check_interval=30,
    )


class EventStream:
    def __init__(
        self,
        client: redis.Redis,
        stream: str,
        group: str,
        consumer: str,
        *,
        maxlen: int,
        buffer_size: int,
        rejected_stream: str | None = None,
    ) -> None:
        self._redis = client
        self._stream = stream
        self._group = group
        self._consumer = consumer
        self._maxlen = maxlen
        self._rejected = rejected_stream or f"{stream}:rejected"
        self._buffer: deque[Event] = deque(maxlen=buffer_size)
        self._dropped = 0
        self._buffer_size = buffer_size
        self._next_retry = 0.0

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    def ensure_group(self) -> None:
        try:
            self._redis.xgroup_create(self._stream, self._group, id="0", mkstream=True)
            log.info("grupo %s creado en %s", self._group, self._stream)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def publish(self, event: Event) -> None:
        """Append an event, buffering in memory if Redis is unreachable."""
        if self._buffer:
            self._append_to_buffer(event)
            if time.monotonic() >= self._next_retry:
                self.flush_buffer()
            return
        try:
            self._xadd(event)
        except RedisError as exc:
            log.error("no se pudo publicar en %s (%s); al buffer", self._stream, exc)
            self._append_to_buffer(event)
            self._next_retry = time.monotonic() + _RETRY_INTERVAL

    def publish_many(self, events: list[Event]) -> None:
        for event in events:
            self.publish(event)

    def _append_to_buffer(self, event: Event) -> None:
        if len(self._buffer) == self._buffer_size:
            self._dropped += 1
            if self._dropped == 1 or self._dropped % 100 == 0:
                log.error(
                    "buffer de publicacion lleno (%d): %d eventos descartados",
                    self._buffer_size,
                    self._dropped,
                )
        self._buffer.append(event)

    def flush_buffer(self) -> int:
        """Try to drain the buffer; return how many events are still held."""
        while self._buffer:
            try:
                self._xadd(self._buffer[0])
            except RedisError:
                self._next_retry = time.monotonic() + _RETRY_INTERVAL
                return len(self._buffer)
            self._buffer.popleft()
        log.info("buffer de publicacion drenado")
        return 0

    def _xadd(self, event: Event) -> None:
        self._redis.xadd(
            self._stream,
            {_PAYLOAD_FIELD: json.dumps(event.to_json(), separators=(",", ":"))},
            maxlen=self._maxlen,
            approximate=True,
        )

    def read_pending(self, count: int) -> list[tuple[str, Event]]:
        """Entries this consumer received but never acknowledged."""
        return self._read("0", count, block=None)

    def read_new(self, count: int, block_ms: int) -> list[tuple[str, Event]]:
        return self._read(">", count, block=block_ms)

    def _read(self, cursor: str, count: int, block: int | None) -> list[tuple[str, Event]]:
        response = self._redis.xreadgroup(
            self._group, self._consumer, {self._stream: cursor}, count=count, block=block
        )
        if not response:
            return []

        out: list[tuple[str, Event]] = []
        poison: list[str] = []
        for _stream, entries in response:
            for message_id, fields in entries:
                try:
                    out.append((message_id, from_json(json.loads(fields[_PAYLOAD_FIELD]))))
                except Exception as exc:
                    log.error("entrada %s ilegible: %s", message_id, exc)
                    poison.append(message_id)
        if poison:
            self.ack(poison)
        return out

    def ack(self, message_ids: list[str]) -> None:
        if message_ids:
            self._redis.xack(self._stream, self._group, *message_ids)

    def reject(self, event: Event, reason: str) -> None:
        """Park an event the database refuses. Inspect with XRANGE."""
        try:
            self._redis.xadd(
                self._rejected,
                {
                    _PAYLOAD_FIELD: json.dumps(event.to_json(), separators=(",", ":")),
                    "reason": reason,
                },
                maxlen=self._maxlen,
                approximate=True,
            )
        except RedisError as exc:
            log.error("no se pudo registrar el evento rechazado: %s", exc)

    def pending_count(self) -> int:
        try:
            return int(self._redis.xpending(self._stream, self._group)["pending"])
        except (RedisError, KeyError, TypeError):
            return -1
