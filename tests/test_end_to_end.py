"""One test that exercises the whole pipeline against real Redis and PostgreSQL.

A scripted PLC feeds the worker; the trackers produce events; Redis carries them; the
writer commits them; the reporting query reads them back. Nothing is hand-written into
the database, so this is the only test that can catch a disagreement between what the
daemon records and what the SQL assumes about it.
"""

from __future__ import annotations

import dataclasses
import threading
import time

import pytest
from tests.conftest import SCHEMA, requires_db, requires_redis

from paradas_faena.db.repository import CounterConfig, PlcConfig
from paradas_faena.plc.worker import PlcWorker
from paradas_faena.state import LineState
from paradas_faena.writer import Writer

pytestmark = [requires_db, requires_redis]

IP9 = "172.30.10.9"
PLC_9 = PlcConfig(ip=IP9, nombre="PLC 09", variador=True)
COUNTERS_9 = [
    CounterConfig(ip=IP9, tag="Cont_P3", name="Puesto 3",
                  input_tag="_IO_EM_DI_00", version=1)
]


class ScriptedPlc:
    def __init__(self) -> None:
        self.counter = 5
        # Logical intent; read() converts it to the wire level (normally closed).
        self.stop_requested = False
        self.frec = 10.0
        self.running = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def read(self, names):
        return {
            "Cont_P3": self.counter,
            "_IO_EM_DI_00": not self.stop_requested,
            "frec": self.frec,
            "status": self.running,
        }


def test_a_stop_flows_from_the_plc_through_redis_to_the_duration_query(db, stream,
                                                                      config, query):
    plc = ScriptedPlc()
    # Compressed cadence so a stop with several re-asserts fits in a test. The segment
    # cap follows REASSERT_SECONDS automatically via Config.max_segment().
    cfg = dataclasses.replace(config, poll_seconds=0.01, reassert_seconds=0.05)

    shutdown = threading.Event()
    worker = PlcWorker(
        plc=PLC_9, counters=COUNTERS_9, stream=stream, line_state=LineState(30.0),
        config=cfg, shutdown=shutdown, client_factory=lambda ip: plc,
    )
    writer = Writer(stream, cfg, shutdown)

    writer.start()
    worker.start()
    try:
        time.sleep(0.15)          # line running, relay open

        plc.stop_requested = True  # el puesto pide la parada: el relay abre
        plc.counter = 6
        time.sleep(0.45)          # held long enough for several re-asserts

        plc.stop_requested = False
        time.sleep(0.25)
    finally:
        shutdown.set()
        worker.join(timeout=10)
        writer.join(timeout=15)

    with db.cursor() as cur:
        cur.execute(
            f"select ts, value from {SCHEMA}.input_status "
            f"where tag = 'Cont_P3' order by ts"
        )
        rows = cur.fetchall()

    assert rows, "el daemon no registro ningun estado de relay"
    held = [row_ts for row_ts, value in rows if value]
    released = [row_ts for row_ts, value in rows if not value]
    assert held, "no se registro el pedido de parada"
    assert len(held) > 1, "no se reescribio el relay mientras seguia en 1"

    first_hold = min(held)
    closing = [row_ts for row_ts in released if row_ts > first_hold]
    assert closing, "no se registro la liberacion del relay"
    expected = (min(closing) - first_hold).total_seconds()

    with db.cursor() as cur:
        cur.execute(
            query("tiempo_parada_por_puesto"),
            {"desde": first_hold - cfg.max_segment(),
             "hasta": min(closing) + cfg.max_segment(),
             "max_segment": cfg.max_segment()},
        )
        result = {name: tiempo.total_seconds() for name, tiempo, _ in cur.fetchall()}

    assert result["Puesto 3"] == pytest.approx(expected, abs=0.02), (
        f"la query dice {result['Puesto 3']:.3f}s, la linea de tiempo registrada "
        f"dice {expected:.3f}s"
    )
    assert 0.3 < result["Puesto 3"] < 0.7

    with db.cursor() as cur:
        cur.execute(f"select dif, status_noria from {SCHEMA}.paradas")
        assert cur.fetchall() == [(1, True)]
        cur.execute(f"select count(*) from {SCHEMA}.plc_heartbeat")
        assert cur.fetchone()[0] >= 1
        cur.execute(f"select running from {SCHEMA}.noria_status")
        assert cur.fetchall() == [(True,)]

    assert stream.pending_count() == 0, "quedaron eventos sin confirmar"
    assert stream.dropped == 0
