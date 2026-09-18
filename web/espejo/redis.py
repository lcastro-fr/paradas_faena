"""Adapter de Redis: el estado que el daemon espeja y el tail del stream de eventos.

Toda lectura tolera un Redis que no está: una pantalla que dice "sin conexión" es correcta,
una que revienta no.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Callable, Sequence

from redis import asyncio as aioredis

from espejo.domain import EstadoEspejo, Noria, Rele, now_ms, sse, to_ms
from paradas_faena.backoff import Backoff
from paradas_faena.config import LiveConfig
from paradas_faena.events import Event, from_json

log = logging.getLogger(__name__)


def build_cliente(url: str, timeout: float = 2.0) -> aioredis.Redis:
    return aioredis.from_url(
        url,
        decode_responses=True,
        socket_timeout=timeout,
        socket_connect_timeout=timeout,
        health_check_interval=30,
    )


class EspejoRedis:
    def __init__(
        self, client: aioredis.Redis, cfg: LiveConfig, stale_seconds: float
    ) -> None:
        self._redis = client
        self._cfg = cfg
        self._stale_ms = int(stale_seconds * 1000)

    async def leer(self, ips: Sequence[str]) -> EstadoEspejo:
        """The whole mirror in one pipeline: noria, relays, and who is alive."""
        try:
            pipe = self._redis.pipeline(transaction=False)
            pipe.get(self._cfg.noria_key)
            pipe.hgetall(self._cfg.puestos_key)
            pipe.get(self._cfg.daemon_key)
            for ip in ips:
                pipe.get(self._cfg.plc_key(ip))
            raw_noria, raw_relays, raw_daemon, *raw_plcs = await pipe.execute()
        except Exception as exc:
            log.warning("no se pudo leer el espejo vivo: %s", exc)
            return EstadoEspejo(False, Noria(), {}, dict.fromkeys(ips, False), False)

        return EstadoEspejo(
            ok=True,
            noria=self._leer_noria(raw_noria),
            reles=_reles(raw_relays),
            # Absence is the signal: the key carries a TTL the daemon refreshes every
            # tick, so an expired key means that PLC stopped answering.
            plcs_vivos={ip: _up(raw) for ip, raw in zip(ips, raw_plcs, strict=True)},
            daemon_vivo=_up(raw_daemon),
        )

    def _leer_noria(self, raw: str | None) -> Noria:
        if not raw:
            return Noria()
        try:
            payload = json.loads(raw)
        except ValueError:
            log.warning("estado de noria ilegible en el espejo")
            return Noria()
        ts_ms = to_ms(payload.get("ts"))
        stale = ts_ms is None or now_ms() - ts_ms > self._stale_ms
        return Noria(
            running=payload.get("running"),
            vel=payload.get("vel"),
            frec=payload.get("frec"),
            ts_ms=ts_ms,
            stale=stale,
        )


def _up(raw: str | None) -> bool:
    if not raw:
        return False
    try:
        return bool(json.loads(raw).get("up"))
    except ValueError:
        return False


def _reles(raw: dict[str, str]) -> dict[str, Rele]:
    out: dict[str, Rele] = {}
    for field, value in (raw or {}).items():
        try:
            payload = json.loads(value)
            ts_ms = to_ms(payload["ts"])
            out[field] = Rele(
                value=bool(payload["value"]),
                ts_ms=ts_ms,
                since_ms=to_ms(payload.get("since")) or ts_ms,
                exact=bool(payload.get("exact", False)),
            )
        except Exception:
            log.warning("estado ilegible en el espejo para %s", field)
    return out


_PAYLOAD_FIELD = "d"
_BLOCK_MS = 5000
# XREAD parks on the socket for the whole block, so the socket timeout has to outlast it
# or every read dies of a timeout that nothing actually caused.
SOCKET_TIMEOUT = _BLOCK_MS / 1000 + 5.0

# Sent to a client whose queue overflowed: cheaper than dropping the connection, which
# would cost a handshake and a snapshot anyway.
RESYNC = sse("delta", '{"t":"resync"}')


class FeedRedis:
    def __init__(
        self,
        client: aioredis.Redis,
        stream: str,
        *,
        queue_size: int,
        translate: Callable[[Event], dict | None],
    ) -> None:
        self._redis = client
        self._stream = stream
        self._queue_size = queue_size
        self._translate = translate
        self._clients: set[asyncio.Queue[str]] = set()
        self._task: asyncio.Task | None = None
        self._connected = False
        self._failed = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def clients(self) -> int:
        return len(self._clients)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._read_loop(), name="live-feed")

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[str]]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=self._queue_size)
        self._clients.add(queue)
        try:
            yield queue
        finally:
            self._clients.discard(queue)

    def broadcast(self, frame: str) -> None:
        """Never awaits and never blocks: the reader must not wait on a browser."""
        for queue in self._clients:
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                _drain(queue)
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(RESYNC)

    async def _read_loop(self) -> None:
        backoff = Backoff(1.0, 15.0)
        cursor = "$"
        while True:
            try:
                response = await self._redis.xread(
                    {self._stream: cursor}, block=_BLOCK_MS, count=200
                )
                if not self._connected:
                    self._connected = True
                    self._failed = False
                    log.info("feed vivo: leyendo %s", self._stream)
                backoff.reset()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # `first` matters: without it the very first failure is silent, and a
                # feed that never connected looks identical to one with nothing to say.
                first = not self._failed
                self._failed = True
                was_up, self._connected = self._connected, False
                delay = backoff.next_delay()
                if was_up or first:
                    log.error(
                        "feed vivo: no se pudo leer %s (%s: %s); reintento en ~%.1fs",
                        self._stream, type(exc).__name__, exc, delay,
                    )
                # Back to `$`: the gap is covered by the snapshot the client gets on
                # reconnect, and replaying a burst of stale edges would be worse.
                cursor = "$"
                await asyncio.sleep(delay)
                continue

            for _stream, entries in response or []:
                for message_id, fields in entries:
                    cursor = message_id
                    frame = self._frame(message_id, fields)
                    if frame is not None:
                        self.broadcast(frame)

    def _frame(self, message_id: str, fields: dict) -> str | None:
        try:
            event = from_json(json.loads(fields[_PAYLOAD_FIELD]))
        except Exception as exc:
            log.warning("feed vivo: entrada %s ilegible (%s)", message_id, exc)
            return None
        payload = self._translate(event)
        return None if payload is None else sse("delta", payload)


def _drain(queue: asyncio.Queue) -> None:
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return
