from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from collections.abc import Callable, Sequence

from ..backoff import Backoff
from ..config import Config
from ..db.repository import CounterConfig, PlcConfig
from ..runner import ManagedThread
from ..state import LineState
from ..stream import EventStream
from .client import PlcClient, PlcReadError
from .trackers import (
    CounterTracker,
    HeartbeatTracker,
    InputBinding,
    InputTracker,
    NoriaStatusTracker,
    SpeedTracker,
)

log = logging.getLogger(__name__)


class PlcWorker(ManagedThread):
    def __init__(
        self,
        plc: PlcConfig,
        counters: Sequence[CounterConfig],
        stream: EventStream,
        line_state: LineState,
        config: Config,
        shutdown: threading.Event,
        client_factory: Callable[[str], PlcClient] = PlcClient,
    ) -> None:
        super().__init__(f"plc-{plc.ip}")
        self.plc = plc
        self._client_factory = client_factory
        self._stream = stream
        self._line = line_state
        self._cfg = config
        self._shutdown = shutdown

        # tag -> live config version, and input tag -> (counter tag, version)
        self._counter_versions = {c.tag: c.version for c in counters}
        self._input_bindings = {
            c.input_tag: InputBinding(c.tag, c.version) for c in counters if c.input_tag
        }
        self._counter_tags = list(self._counter_versions)

        self._counters = CounterTracker(plc.ip, self._counter_versions)
        self._inputs = InputTracker(plc.ip, self._input_bindings, config.reassert_seconds)
        self._heartbeat = HeartbeatTracker(plc.ip, config.heartbeat_seconds)
        self._status = NoriaStatusTracker(plc.ip) if plc.variador else None
        self._speed = (
            SpeedTracker(
                plc.ip,
                config.noria_conv,
                config.speed_deadband_hz,
                config.speed_max_interval_seconds,
            )
            if plc.variador
            else None
        )

        if not self._input_bindings:
            log.warning(
                "%s: ningun counters_name.input_tag configurado; no se registraran "
                "tiempos de parada para este PLC",
                plc.ip,
            )

        names = [*self._counter_tags, *self._input_bindings]
        if plc.variador:
            names += [config.frec_tag, config.status_tag]
        # Deduplicated: these are Micro820s, so pycomm3 sends one request per tag and
        # asking twice for the same one costs a whole round trip.
        self._read_names: list[str] = list(dict.fromkeys(names))

    def run(self) -> None:
        backoff = Backoff(self._cfg.backoff_initial_seconds, self._cfg.backoff_max_seconds)
        if not self._read_names:
            log.error("%s: nada que leer, terminando worker", self.plc.ip)
            return

        while not self._shutdown.is_set():
            try:
                with self._client_factory(self.plc.ip) as client:
                    backoff.reset()
                    self._resync()
                    self._poll_loop(client)
            except Exception as exc:
                self._on_disconnect()
                if self._shutdown.is_set():
                    break
                delay = backoff.sleep(self._shutdown)
                log.error(
                    "%s: %s: %s; reintentando en ~%.1fs",
                    self.plc.ip,
                    type(exc).__name__,
                    exc,
                    delay,
                )
        log.info("%s: worker terminado", self.plc.ip)

    def _on_disconnect(self) -> None:
        if self.plc.variador:
            self._line.invalidate()

    def _resync(self) -> None:
        self._counters.reset()
        self._inputs.reset()
        self._heartbeat.reset()
        if self._status is not None:
            self._status.reset()
        if self._speed is not None:
            self._speed.reset()

    def _poll_loop(self, client: PlcClient) -> None:
        interval = self._cfg.poll_seconds
        # Absolute deadlines, so the cadence does not drift with PLC latency.
        next_tick = time.monotonic()
        while not self._shutdown.is_set():
            next_tick += interval
            try:
                self._tick(client)
            except PlcReadError as exc:
                log.warning("%s: lectura fallida: %s", self.plc.ip, exc)
                raise

            drift = next_tick - time.monotonic()
            if drift > 0:
                if self._shutdown.wait(drift):
                    return
            elif drift < -interval:
                log.warning(
                    "%s: ciclo atrasado %.2fs (lectura mas lenta que POLL_SECONDS)",
                    self.plc.ip,
                    -drift,
                )
                next_tick = time.monotonic()

    def _tick(self, client: PlcClient) -> None:
        values = client.read(self._read_names)
        ts = dt.datetime.now(dt.UTC)

        # Line state goes first, so the counters below stamp this tick's values.
        if self._status is not None:
            running = bool(values[self._cfg.status_tag])
            self._line.set_running(running, ts)
            self._stream.publish_many(self._status.observe(running, ts))

        frec: float | None = None
        if self._speed is not None:
            frec = float(values[self._cfg.frec_tag])
            self._line.set_velocity(self._speed.to_velocity(frec), ts)

        noria_running, vel = self._line.snapshot(ts)

        if self._speed is not None and frec is not None:
            self._stream.publish_many(self._speed.observe(frec, ts, noria_running))

        counter_values = {tag: values[tag] for tag in self._counter_tags}
        self._stream.publish_many(
            self._counters.observe(counter_values, ts, noria_running, vel)
        )

        if self._input_bindings:
            self._stream.publish_many(self._inputs.observe(values, ts))

        self._stream.publish_many(self._heartbeat.observe(ts))
