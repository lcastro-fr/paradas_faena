"""Tests for the single-connection writer, against real PostgreSQL and Redis.

What matters is the failure paths: a batch must survive the database going away, replay
must not duplicate rows, and one bad event must not wedge everything behind it.
"""

from __future__ import annotations

import datetime as dt
import threading
import time

from tests.conftest import SCHEMA, requires_db, requires_redis, ts

from paradas_faena.db.repository import Repository, local_parts
from paradas_faena.events import (
    CounterIncrement,
    Heartbeat,
    InputEdge,
    NoriaStatusEdge,
    SessionClosed,
    SpeedSample,
)
from paradas_faena.writer import Writer

pytestmark = [requires_db, requires_redis]

IP8 = "172.30.10.8"


def count(db, table: str) -> int:
    with db.cursor() as cur:
        cur.execute(f"select count(*) from {SCHEMA}.{table}")
        return cur.fetchone()[0]


def sample_events() -> list:
    return [
        InputEdge(
            ip=IP8, tag="Cont_P1", version=1, ts=ts(10, 0, 0), value=True, reason="change"
        ),
        InputEdge(
            ip=IP8, tag="Cont_P1", version=1, ts=ts(10, 0, 30), value=False, reason="change"
        ),
        CounterIncrement(
            ip=IP8,
            tag="Cont_P1",
            ts=ts(10, 0, 0),
            old_value=4,
            new_value=5,
            dif=1,
            noria_running=True,
            vel=42.3,
            version=1,
        ),
        SpeedSample(
            ip="172.30.10.9", ts=ts(10, 0, 0), frec=10.0, vel=42.3, noria_running=True
        ),
        NoriaStatusEdge(ip="172.30.10.9", ts=ts(10, 0, 0), running=True),
        Heartbeat(ip=IP8, ts=ts(10, 0, 0)),
        SessionClosed(
            fecha=dt.date(2026, 9, 7),
            hora_inicio=dt.time(6, 2),
            hora_fin=dt.time(16, 14),
            registros=1200,
        ),
    ]


def run_writer(stream, config, *, until, timeout=15.0) -> Writer:
    shutdown = threading.Event()
    writer = Writer(stream, config, shutdown)
    writer.start()
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline and not until():
            time.sleep(0.05)
    finally:
        shutdown.set()
        writer.join(timeout=timeout)
    return writer


def test_every_event_type_reaches_its_table(db, stream, config):
    stream.publish_many(sample_events())
    run_writer(stream, config, until=lambda: count(db, "input_status") == 2)

    assert count(db, "input_status") == 2
    assert count(db, "paradas") == 1
    assert count(db, "velocidad") == 1
    assert count(db, "noria_status") == 1
    assert count(db, "plc_heartbeat") == 1
    assert count(db, "generales") == 1


def test_a_committed_batch_is_acknowledged(db, stream, config):
    stream.publish_many(sample_events())
    run_writer(stream, config, until=lambda: count(db, "input_status") == 2)
    assert stream.pending_count() == 0


def test_a_stop_lands_with_the_line_state_captured_at_read_time(db, stream, config):
    stream.publish(
        CounterIncrement(
            ip=IP8,
            tag="Cont_P1",
            ts=ts(10, 0, 0),
            old_value=4,
            new_value=6,
            dif=2,
            noria_running=False,
            vel=0.0,
            version=1,
        )
    )
    run_writer(stream, config, until=lambda: count(db, "paradas") == 1)

    with db.cursor() as cur:
        cur.execute(
            f"select tag, old_value, new_value, dif, status_noria, vel, fecha, hora "
            f"from {SCHEMA}.paradas"
        )
        row = cur.fetchone()
    fecha, hora = local_parts(ts(10, 0, 0))
    assert row[:6] == ("Cont_P1", 4, 6, 2, False, 0.0)
    assert (row[6], row[7]) == (fecha, hora)


def test_unknown_line_state_is_stored_as_null_not_as_a_stale_value(db, stream, config):
    """The existing report's `where status_noria is not false` keeps these rows."""
    stream.publish(
        CounterIncrement(
            ip=IP8,
            tag="Cont_P1",
            ts=ts(10),
            old_value=1,
            new_value=2,
            dif=1,
            noria_running=None,
            vel=None,
            version=1,
        )
    )
    run_writer(stream, config, until=lambda: count(db, "paradas") == 1)

    with db.cursor() as cur:
        cur.execute(f"select status_noria, vel from {SCHEMA}.paradas")
        assert cur.fetchone() == (None, None)
        cur.execute(
            f"select count(*) from {SCHEMA}.paradas where status_noria is not false"
        )
        assert cur.fetchone()[0] == 1


# --- idempotency ----------------------------------------------------------------------


def test_writing_the_same_batch_twice_inserts_each_row_once(db, config):
    """At-least-once delivery re-presents committed events; every insert absorbs that."""
    repo = Repository.connect(config.db)
    try:
        events = sample_events()
        for _ in range(3):
            repo.insert_input_edges([e for e in events if isinstance(e, InputEdge)])
            repo.insert_counters([e for e in events if isinstance(e, CounterIncrement)])
            repo.insert_speed([e for e in events if isinstance(e, SpeedSample)])
            repo.insert_noria_status([e for e in events if isinstance(e, NoriaStatusEdge)])
            repo.insert_heartbeats([e for e in events if isinstance(e, Heartbeat)])
            repo.upsert_generales([e for e in events if isinstance(e, SessionClosed)])
            repo.commit()
    finally:
        repo.close()

    assert count(db, "input_status") == 2
    assert count(db, "paradas") == 1
    assert count(db, "velocidad") == 1
    assert count(db, "noria_status") == 1
    assert count(db, "plc_heartbeat") == 1
    assert count(db, "generales") == 1


def test_generales_is_updated_rather_than_rejected_on_a_rerun(db, config):
    repo = Repository.connect(config.db)
    try:
        day = dt.date(2026, 9, 7)
        repo.upsert_generales(
            [
                SessionClosed(
                    fecha=day,
                    hora_inicio=dt.time(6, 0),
                    hora_fin=dt.time(16, 0),
                    registros=1000,
                )
            ]
        )
        repo.commit()
        repo.upsert_generales(
            [
                SessionClosed(
                    fecha=day,
                    hora_inicio=dt.time(6, 2),
                    hora_fin=dt.time(16, 30),
                    registros=1234,
                )
            ]
        )
        repo.commit()
    finally:
        repo.close()

    with db.cursor() as cur:
        cur.execute(f"select hora_inicio, hora_fin, registros from {SCHEMA}.generales")
        assert cur.fetchall() == [(dt.time(6, 2), dt.time(16, 30), 1234)]


def test_a_reassert_landing_on_an_existing_instant_is_absorbed(db, config):
    repo = Repository.connect(config.db)
    try:
        for reason in ("change", "reassert"):
            repo.insert_input_edges(
                [
                    InputEdge(
                        ip=IP8,
                        tag="Cont_P1",
                        version=1,
                        ts=ts(10),
                        value=True,
                        reason=reason,
                    )
                ]
            )
            repo.commit()
    finally:
        repo.close()
    assert count(db, "input_status") == 1


# --- failure paths --------------------------------------------------------------------


def test_events_left_in_the_stream_by_a_previous_run_are_written_on_startup(
    db, stream, config
):
    stream.publish_many(sample_events())
    assert stream.read_new(100, 50)  # read but never acked, as if the daemon died
    assert stream.pending_count() == len(sample_events())

    run_writer(stream, config, until=lambda: count(db, "input_status") == 2)

    assert count(db, "input_status") == 2
    assert count(db, "paradas") == 1
    assert stream.pending_count() == 0


def test_losing_the_database_mid_run_costs_nothing(db, stream, config):
    """The backend is killed underneath the writer. The in-flight batch is not
    acknowledged, so Redis redelivers it once the writer reconnects."""
    shutdown = threading.Event()
    writer = Writer(stream, config, shutdown)
    writer.start()
    try:
        for n in range(20):
            stream.publish(
                InputEdge(
                    ip=IP8,
                    tag="Cont_P1",
                    ts=ts(10, 0, n),
                    value=bool(n % 2),
                    reason="change",
                    version=1,
                )
            )
        deadline = time.monotonic() + 15
        while count(db, "input_status") < 20 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert count(db, "input_status") == 20

        with db.cursor() as cur:
            cur.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity "
                "where application_name = 'monitoreo_faena' and pid <> pg_backend_pid()"
            )
            assert cur.rowcount >= 1

        for n in range(20, 40):
            stream.publish(
                InputEdge(
                    ip=IP8,
                    tag="Cont_P1",
                    ts=ts(10, 1, n - 20),
                    value=bool(n % 2),
                    reason="change",
                    version=1,
                )
            )

        deadline = time.monotonic() + 25
        while count(db, "input_status") < 40 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        shutdown.set()
        writer.join(timeout=20)

    assert count(db, "input_status") == 40, "eventos perdidos al caerse la conexion"


def test_one_rejected_event_does_not_block_the_rest_of_its_batch(
    db, stream, config, redis_client, stream_names
):
    """A tag absent from counters_name violates the foreign key. Retrying it forever
    would stall every event behind it, so it is parked on the rejected stream."""
    good = [
        InputEdge(
            ip=IP8,
            tag="Cont_P1",
            ts=ts(10, 0, n),
            value=bool(n % 2),
            reason="change",
            version=1,
        )
        for n in range(6)
    ]
    poison = InputEdge(
        ip=IP8, tag="NO_EXISTE", version=1, ts=ts(10, 5), value=True, reason="change"
    )
    stream.publish_many([*good[:3], poison, *good[3:]])

    run_writer(stream, config, until=lambda: count(db, "input_status") == 6)

    assert count(db, "input_status") == 6
    rejected = redis_client.xrange(f"{stream_names}:rejected")
    assert len(rejected) == 1
    assert "NO_EXISTE" in rejected[0][1]["d"]
    assert stream.pending_count() == 0


def test_isolation_leaves_the_batch_pending_if_the_connection_dies(db, stream, config):
    """Isolation walks a rejected batch in halves. If the connection dies partway, the
    batch must stay pending rather than be acknowledged -- the committed halves absorb
    their own redelivery."""
    shutdown = threading.Event()
    writer = Writer(stream, config, shutdown)
    events = [
        InputEdge(
            ip=IP8,
            tag="Cont_P1",
            ts=ts(10, 0, n),
            value=bool(n % 2),
            reason="change",
            version=1,
        )
        for n in range(8)
    ]

    assert writer._isolate(events) is False  # no connection was ever opened
    assert count(db, "input_status") == 0


def test_a_shutdown_with_the_database_gone_leaves_events_in_the_stream(db, stream, config):
    """Nothing may be silently lost at shutdown: un-acked entries wait in Redis."""
    import dataclasses

    unreachable = dataclasses.replace(config, db=dataclasses.replace(config.db, port=1))
    stream.publish_many(sample_events())

    shutdown = threading.Event()
    writer = Writer(stream, unreachable, shutdown)
    writer.start()
    time.sleep(0.5)
    shutdown.set()
    writer.join(timeout=20)

    assert not writer.is_alive()
    assert count(db, "input_status") == 0
    # Still deliverable: either untouched in the stream, or pending for this consumer.
    recovered = stream.read_pending(100) or stream.read_new(100, 50)
    assert len(recovered) == len(sample_events())


# --- configuration loading ------------------------------------------------------------


def test_plc_addresses_come_back_without_a_netmask(db, config):
    """`plcs.ip` is `inet`, which renders as CIDR -- ip::text gives "172.30.10.8/32",
    unusable as a LogixDriver connection path."""
    repo = Repository.connect(config.db)
    try:
        plcs = repo.load_plcs()
        counters = repo.load_counters()
    finally:
        repo.close()

    assert {p.ip for p in plcs} == {"172.30.10.8", "172.30.10.9"}
    assert all("/" not in p.ip for p in plcs)
    assert all("/" not in c.ip for c in counters)


def test_counter_addresses_group_onto_the_plc_addresses(db, config):
    repo = Repository.connect(config.db)
    try:
        plc_ips = {p.ip for p in repo.load_plcs()}
        counter_ips = {c.ip for c in repo.load_counters()}
    finally:
        repo.close()
    assert counter_ips <= plc_ips


def test_the_variador_flag_identifies_the_plc_carrying_the_frequency_converter(db, config):
    repo = Repository.connect(config.db)
    try:
        variadores = [p.ip for p in repo.load_plcs() if p.variador]
    finally:
        repo.close()
    assert variadores == ["172.30.10.9"]


def test_a_batch_committed_but_never_acked_is_not_duplicated_on_replay(db, stream, config):
    """The exactly-once claim, made deterministic.

    Models a crash between COMMIT and XACK: the rows are already in PostgreSQL and the
    entries are still pending, so a restarting writer sees them again. Idempotent
    inserts are what turn Redis's at-least-once delivery into exactly-once storage.
    """
    events = sample_events()

    repo = Repository.connect(config.db)
    try:
        repo.insert_input_edges([e for e in events if isinstance(e, InputEdge)])
        repo.insert_counters([e for e in events if isinstance(e, CounterIncrement)])
        repo.insert_speed([e for e in events if isinstance(e, SpeedSample)])
        repo.insert_noria_status([e for e in events if isinstance(e, NoriaStatusEdge)])
        repo.insert_heartbeats([e for e in events if isinstance(e, Heartbeat)])
        repo.upsert_generales([e for e in events if isinstance(e, SessionClosed)])
        repo.commit()
    finally:
        repo.close()

    before = {
        t: count(db, t)
        for t in (
            "input_status",
            "paradas",
            "velocidad",
            "noria_status",
            "plc_heartbeat",
            "generales",
        )
    }

    stream.publish_many(events)
    assert stream.read_new(100, 50)  # delivered, never acked
    run_writer(stream, config, until=lambda: stream.pending_count() == 0)

    after = {t: count(db, t) for t in before}
    assert after == before, "el replay duplico filas"
    assert stream.pending_count() == 0


def test_load_counters_returns_only_the_live_version(db, config):
    """The daemon must read the newest configuration, and stamp that version on every
    event -- otherwise a rewiring would be recorded against the old input mapping."""
    with db.cursor() as cur:
        cur.execute(
            f"""insert into {SCHEMA}.counters_name
                    (ip, tag, name, input_tag, version)
                values (%s, 'Cont_P1', 'Puesto 1', '_IO_P1_DI_02', 2)""",
            (IP8,),
        )
    try:
        repo = Repository.connect(config.db)
        try:
            rows = [c for c in repo.load_counters() if c.tag == "Cont_P1"]
        finally:
            repo.close()

        assert len(rows) == 1, "se devolvieron varias versiones del mismo puesto"
        assert rows[0].version == 2
        assert rows[0].input_tag == "_IO_P1_DI_02"
    finally:
        with db.cursor() as cur:
            cur.execute(
                f"delete from {SCHEMA}.counters_name where ip = %s and tag = 'Cont_P1' "
                f"and version = 2",
                (IP8,),
            )


def test_an_event_is_stored_against_the_version_it_carries(db, stream, config):
    stream.publish(
        InputEdge(ip=IP8, tag="Cont_P1", version=1, ts=ts(10), value=True, reason="change")
    )
    run_writer(stream, config, until=lambda: count(db, "input_status") == 1)
    with db.cursor() as cur:
        cur.execute(f"select version from {SCHEMA}.input_status")
        assert cur.fetchone() == (1,)
