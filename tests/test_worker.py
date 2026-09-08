"""Tests for the PLC worker's wiring, against a scripted PLC.

Covers what the worker does around the trackers: which tags it asks for in one batched
read, that line state is published before the counters are processed, and that a
reconnect resyncs rather than emitting a burst.
"""

from __future__ import annotations

import threading
import time

import pytest
from tests.conftest import requires_redis

from paradas_faena.config import Config, DbConfig
from paradas_faena.db.repository import CounterConfig, PlcConfig
from paradas_faena.events import (
    CounterIncrement,
    Heartbeat,
    InputEdge,
    NoriaStatusEdge,
    SpeedSample,
)
from paradas_faena.plc.client import PlcReadError
from paradas_faena.plc.worker import PlcWorker
from paradas_faena.state import LineState

pytestmark = requires_redis

IP8 = "172.30.10.8"
IP9 = "172.30.10.9"
PLC_8 = PlcConfig(ip=IP8, nombre="PLC 08", variador=False)
PLC_9 = PlcConfig(ip=IP9, nombre="PLC 09", variador=True)

COUNTERS_8 = [
    CounterConfig(ip=IP8, tag="Cont_P1", name="Puesto 1",
                  input_tag="_IO_EM_DI_00", version=1),
    CounterConfig(ip=IP8, tag="Cont_P2", name="Puesto 2",
                  input_tag="_IO_EM_DI_01", version=1),
]
COUNTERS_9 = [
    CounterConfig(ip=IP9, tag="Cont_P3", name="Puesto 3",
                  input_tag="_IO_P1_DI_00", version=1)
]


class FakePlc:
    """Returns scripted readings and records what was asked for."""

    def __init__(self, ip: str, script: list[dict] | None = None) -> None:
        self.ip = ip
        self.requested: list[list[str]] = []
        self._script = script or []
        self._index = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def read(self, names):
        self.requested.append(list(names))
        if self._index >= len(self._script):
            raise PlcReadError("script agotado")
        values = self._script[self._index]
        self._index += 1
        return dict(values)


def fast_config(config: Config, **overrides) -> Config:
    import dataclasses

    return dataclasses.replace(
        config,
        db=DbConfig("h", 5432, "d", "u", "p", "paradas_faena"),
        poll_seconds=0.01,
        backoff_initial_seconds=0.01,
        backoff_max_seconds=0.02,
        **overrides,
    )


def make_worker(plc, counters, fake, stream, config, **overrides):
    return PlcWorker(
        plc=plc,
        counters=counters,
        stream=stream,
        line_state=LineState(30.0),
        config=fast_config(config, **overrides),
        shutdown=threading.Event(),
        client_factory=lambda ip: fake,
    )


def published(stream):
    batch = stream.read_new(100, 50)
    stream.ack([message_id for message_id, _ in batch])
    return [event for _, event in batch]


# --- what gets requested --------------------------------------------------------------


def test_counters_inputs_and_variador_tags_are_all_requested(stream, config):
    worker = make_worker(PLC_9, COUNTERS_9, FakePlc(IP9), stream, config)
    assert worker._read_names == ["Cont_P3", "_IO_P1_DI_00", "frec", "status"]


def test_the_input_tags_come_from_counters_name(stream, config):
    counters = [
        CounterConfig(ip=IP8, tag="a", name="A", input_tag="_IO_EM_DI_07", version=1),
        CounterConfig(ip=IP8, tag="b", name="B", input_tag="_IO_P2_DI_03", version=1),
    ]
    worker = make_worker(PLC_8, counters, FakePlc(IP8), stream, config)
    assert worker._read_names == ["a", "b", "_IO_EM_DI_07", "_IO_P2_DI_03"]


def test_a_tag_asked_for_twice_is_only_requested_once(stream, config):
    """These are Micro820s: pycomm3 sends one request per tag, so a duplicate costs a
    whole round trip."""
    counters = [
        CounterConfig(ip=IP8, tag="Shared", name="A", input_tag="Shared", version=1),
        CounterConfig(ip=IP8, tag="b", name="B", input_tag="_IO_EM_DI_01", version=1),
    ]
    worker = make_worker(PLC_8, counters, FakePlc(IP8), stream, config)
    assert worker._read_names == ["Shared", "b", "_IO_EM_DI_01"]


def test_a_non_variador_plc_does_not_ask_for_frec_or_status(stream, config):
    worker = make_worker(PLC_8, COUNTERS_8, FakePlc(IP8), stream, config)
    assert "frec" not in worker._read_names
    assert "status" not in worker._read_names


def test_unmapped_counters_are_still_polled_for_stops(stream, config):
    """Deploying before the relays are mapped must still record stop counts."""
    counters = [
        CounterConfig(ip=IP8, tag="Cont_P1", name="Puesto 1", input_tag=None,
                      version=1)
    ]
    worker = make_worker(PLC_8, counters, FakePlc(IP8), stream, config)
    assert worker._read_names == ["Cont_P1"]


# --- a tick ---------------------------------------------------------------------------


def test_a_first_tick_publishes_resync_inputs_and_a_heartbeat(stream, config):
    # Wire levels: DI_00 closed (Cont_P1 libre), DI_01 open (Cont_P2 pidiendo parada).
    fake = FakePlc(IP8, [
        {"Cont_P1": 5, "Cont_P2": 2, "_IO_EM_DI_00": True, "_IO_EM_DI_01": False},
    ])
    worker = make_worker(PLC_8, COUNTERS_8, fake, stream, config)

    worker._resync()
    worker._tick(fake)
    events = published(stream)

    assert sorted(
        (e.tag, e.value) for e in events if isinstance(e, InputEdge)
    ) == [("Cont_P1", False), ("Cont_P2", True)]
    assert any(isinstance(e, Heartbeat) for e in events)
    assert not any(isinstance(e, CounterIncrement) for e in events)


def test_a_stop_is_stamped_with_the_same_ticks_line_state(stream, config):
    fake = FakePlc(IP9, [
        # libre, then the relay opens: the puesto asks for a stop.
        {"Cont_P3": 5, "_IO_P1_DI_00": True, "frec": 10.0, "status": True},
        {"Cont_P3": 6, "_IO_P1_DI_00": False, "frec": 10.0, "status": True},
    ])
    worker = make_worker(PLC_9, COUNTERS_9, fake, stream, config)

    worker._resync()
    worker._tick(fake)
    published(stream)
    worker._tick(fake)
    events = published(stream)

    (stop,) = [e for e in events if isinstance(e, CounterIncrement)]
    assert stop.dif == 1
    assert stop.noria_running is True
    assert stop.vel == pytest.approx(42.3)

    (edge,) = [e for e in events if isinstance(e, InputEdge)]
    assert (edge.value, edge.reason) == (True, "change")
    # Every value in the tick shares one timestamp, because it was one read.
    assert stop.ts == edge.ts


def test_the_variador_plc_publishes_speed_and_status(stream, config):
    fake = FakePlc(IP9, [
        {"Cont_P3": 5, "_IO_P1_DI_00": True, "frec": 10.0, "status": True},
    ])
    worker = make_worker(PLC_9, COUNTERS_9, fake, stream, config)
    worker._resync()
    worker._tick(fake)
    events = published(stream)

    (speed,) = [e for e in events if isinstance(e, SpeedSample)]
    assert (speed.frec, speed.vel) == (10.0, pytest.approx(42.3))
    (status,) = [e for e in events if isinstance(e, NoriaStatusEdge)]
    assert status.running is True


def test_a_steady_line_goes_quiet_after_the_first_tick(stream, config):
    reading = {"Cont_P3": 5, "_IO_P1_DI_00": True, "frec": 10.0, "status": True}
    fake = FakePlc(IP9, [dict(reading) for _ in range(5)])
    worker = make_worker(PLC_9, COUNTERS_9, fake, stream, config)

    worker._resync()
    worker._tick(fake)
    published(stream)

    for _ in range(4):
        worker._tick(fake)
    assert published(stream) == []


def test_a_reconnect_resyncs_instead_of_reporting_the_gap_as_stops(stream, config):
    """The counter moved by 200 while disconnected; that is not a burst of stops."""
    fake = FakePlc(IP8, [
        {"Cont_P1": 5, "Cont_P2": 2, "_IO_EM_DI_00": True, "_IO_EM_DI_01": True},
        # DI_00 opens across the gap: Cont_P1 is holding the line on the far side.
        {"Cont_P1": 205, "Cont_P2": 2, "_IO_EM_DI_00": False, "_IO_EM_DI_01": True},
    ])
    worker = make_worker(PLC_8, COUNTERS_8, fake, stream, config)

    worker._resync()
    worker._tick(fake)
    published(stream)

    worker._resync()
    worker._tick(fake)
    events = published(stream)

    assert not any(isinstance(e, CounterIncrement) for e in events)
    assert sorted(
        (e.tag, e.value, e.reason) for e in events if isinstance(e, InputEdge)
    ) == [("Cont_P1", True, "resync"), ("Cont_P2", False, "resync")]


def test_the_worker_thread_reconnects_and_then_exits_on_shutdown(stream, config):
    """A read failure must mean a reconnect, not a dead thread."""
    shutdown = threading.Event()
    attempts = []

    def factory(ip):
        fake = FakePlc(ip, [{"Cont_P1": 5, "Cont_P2": 2,
                             "_IO_EM_DI_00": True, "_IO_EM_DI_01": True}])
        attempts.append(fake)
        return fake

    worker = PlcWorker(
        plc=PLC_8, counters=COUNTERS_8, stream=stream, line_state=LineState(30.0),
        config=fast_config(config), shutdown=shutdown, client_factory=factory,
    )
    worker.start()
    time.sleep(0.4)
    shutdown.set()
    worker.join(timeout=5.0)

    assert not worker.is_alive()
    assert len(attempts) > 1, "el worker no reintento la conexion"
