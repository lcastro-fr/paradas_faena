"""Shared fixtures.

The database and stream tests need real PostgreSQL and Redis -- the duration calculation
is a window function over interval arithmetic, and the delivery guarantees are the whole
point of the transport, so neither is worth verifying against a mock.

    docker run -d --name pf-test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=testdb \
        -p 55432:5432 postgres:16-alpine
    docker run -d --name pf-redis -p 56379:6379 redis:7-alpine \
        redis-server --appendonly yes --appendfsync everysec
    export PF_TEST_DSN='host=localhost port=55432 dbname=testdb user=postgres password=test'
    export PF_TEST_REDIS_URL=redis://localhost:56379/0

The schema is built the way production will be: schema.sql as it stands, then the
migrations in order, so these tests also cover the migration path.
"""

from __future__ import annotations

import datetime as dt
import os
import pathlib
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = "paradas_faena"

_WRITABLE = [
    "input_status", "noria_status", "plc_heartbeat", "paradas", "velocidad", "generales",
]


def _dsn() -> str | None:
    return os.environ.get("PF_TEST_DSN")


def _redis_url() -> str | None:
    return os.environ.get("PF_TEST_REDIS_URL")


requires_db = pytest.mark.skipif(
    _dsn() is None, reason="PF_TEST_DSN no definido; ver tests/conftest.py"
)

requires_redis = pytest.mark.skipif(
    _redis_url() is None, reason="PF_TEST_REDIS_URL no definido; ver tests/conftest.py"
)


@pytest.fixture(scope="session")
def db_conn():
    dsn = _dsn()
    if dsn is None:
        pytest.skip("PF_TEST_DSN no definido")
    with psycopg.connect(dsn, autocommit=True) as conn:
        _build_schema(conn)
        yield conn


def _build_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(f"drop schema if exists {SCHEMA} cascade")
        cur.execute(f"create schema {SCHEMA}")
    for path in [ROOT / "schema.sql", *sorted((ROOT / "migrations").glob("00*.sql"))]:
        with conn.cursor() as cur:
            cur.execute(path.read_text())

    with conn.cursor() as cur:
        cur.execute(
            f"""insert into {SCHEMA}.plcs (ip, nombre, variador) values
                   ('172.30.10.8', 'PLC 08', false),
                   ('172.30.10.9', 'PLC 09', true)"""
        )
        cur.execute(
            f"""insert into {SCHEMA}.counters_name
                       (ip, tag, name, input_tag, version) values
                   ('172.30.10.8', 'Cont_P1', 'Puesto 1', '_IO_EM_DI_00', 1),
                   ('172.30.10.8', 'Cont_P2', 'Puesto 2', '_IO_EM_DI_01', 1),
                   ('172.30.10.9', 'Cont_P3', 'Puesto 3', '_IO_EM_DI_00', 1)"""
        )


@pytest.fixture
def db(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(f"truncate {', '.join(f'{SCHEMA}.{t}' for t in _WRITABLE)}")
    return db_conn


@pytest.fixture
def redis_client():
    url = _redis_url()
    if url is None:
        pytest.skip("PF_TEST_REDIS_URL no definido")
    from paradas_faena.stream import build_client

    client = build_client(url)
    client.ping()
    yield client
    client.close()


@pytest.fixture
def stream_names(redis_client):
    """A stream name unique to this test, cleaned up afterwards."""
    name = f"test:{uuid.uuid4().hex[:12]}"
    yield name
    redis_client.delete(name, f"{name}:rejected")


@pytest.fixture
def stream(redis_client, stream_names):
    from paradas_faena.stream import EventStream

    ev = EventStream(
        redis_client, stream_names, "writer", "writer", maxlen=10_000, buffer_size=100
    )
    ev.ensure_group()
    return ev


@pytest.fixture
def query():
    def load(name: str) -> str:
        return (ROOT / "queries" / f"{name}.sql").read_text()
    return load


DAY = dt.date(2026, 9, 7)
TZ = dt.UTC


def ts(hour: int, minute: int = 0, second: int = 0) -> dt.datetime:
    return dt.datetime(2026, 9, 7, hour, minute, second, tzinfo=TZ)


# The timings the `config` fixture uses, and the cap they imply. Kept here so the
# constant and the fixture cannot drift apart; the formula matches Config.max_segment().
TEST_POLL_SECONDS = 0.5
TEST_REASSERT_SECONDS = 60.0
MAX_SEGMENT = dt.timedelta(
    seconds=TEST_REASSERT_SECONDS + TEST_POLL_SECONDS + 1.0
)


@pytest.fixture
def config(tmp_path, stream_names):
    """A Config pointing at the test services, with fast timings."""
    from psycopg.conninfo import conninfo_to_dict

    from paradas_faena.config import Config, DbConfig, RedisConfig

    parts = conninfo_to_dict(_dsn() or "")
    db = DbConfig(
        host=parts.get("host", "localhost"),
        port=int(parts.get("port", 5432)),
        dbname=parts.get("dbname", "testdb"),
        user=parts.get("user", "postgres"),
        password=parts.get("password", ""),
        schema=SCHEMA,
    )
    redis_config = RedisConfig(
        url=_redis_url() or "redis://localhost:6379/0",
        stream=stream_names,
        group="writer",
        consumer="writer",
        maxlen=10_000,
        block_ms=50,
        buffer_size=100,
    )
    return Config(
        db=db, redis=redis_config,
        frec_tag="frec", status_tag="status",
        poll_seconds=TEST_POLL_SECONDS, reassert_seconds=TEST_REASSERT_SECONDS,
        heartbeat_seconds=60.0,
        status_grace_seconds=30.0, noria_conv=4.23, speed_deadband_hz=0.2,
        speed_max_interval_seconds=60.0, edu_path=tmp_path, stopfile_poll_seconds=0.05,
        batch_max_events=100,
        backoff_initial_seconds=0.05, backoff_max_seconds=0.2,
        shutdown_grace_seconds=5.0, log_level="CRITICAL",
    )
