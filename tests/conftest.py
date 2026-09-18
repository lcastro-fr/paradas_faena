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

The schema is built the way production will be: the `-- migrate:up` section of every
file in migrations/, in order -- the same slices dbmate applies. SCHEMA is the production
schema name because the migrations and queries/*.sql hardcode it, so isolation comes from
PF_TEST_DSN pointing at a database you can afford to lose.
"""

from __future__ import annotations

import datetime as dt
import os
import pathlib
import uuid
import zoneinfo

import pytest

psycopg = pytest.importorskip("psycopg")

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = "monitoreo_faena"

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


MIGRATIONS = sorted((ROOT / "migrations").glob("*.sql"))


def up_section(text: str) -> str:
    """The `-- migrate:up` slice of a migration -- exactly what dbmate would execute."""
    _, directive, rest = text.partition("-- migrate:up")
    if not directive:
        raise AssertionError("falta la directiva -- migrate:up")
    _, _, body = rest.partition("\n")
    return body.partition("-- migrate:down")[0]


def _guard_disposable(conn) -> None:
    if os.environ.get("PF_TEST_ALLOW_DESTRUCTIVE") == "1":
        return
    dbname = conn.info.dbname
    if "test" not in dbname:
        pytest.exit(
            f"PF_TEST_DSN apunta a la base '{dbname}', que no parece descartable, y "
            f"_build_schema hace `drop schema {SCHEMA} cascade`. Usa una base de prueba "
            f"(ver tests/conftest.py) o exporta PF_TEST_ALLOW_DESTRUCTIVE=1.",
            returncode=1,
        )


def _build_schema(conn) -> None:
    _guard_disposable(conn)
    with conn.cursor() as cur:
        cur.execute(f"drop schema if exists {SCHEMA} cascade")
        # Desde la migracion 008 el schema reporting tiene tablas propias
        # (parametros, puesto_config), asi que tambien lo crean las migraciones.
        cur.execute("drop schema if exists reporting cascade")
    for path in MIGRATIONS:
        with conn.cursor() as cur:
            cur.execute(up_section(path.read_text()))

    with conn.cursor() as cur:
        cur.execute("drop schema if exists produccion cascade")
        cur.execute((ROOT / "tests" / "produccion_stub.sql").read_text())

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
        # Los parametros son editables desde la 008, asi que un test que cambia uno
        # contaminaria al siguiente.
        cur.execute("truncate reporting.puesto_config")
        cur.execute("delete from reporting.parametros")
        cur.execute("insert into reporting.parametros default values")
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
def mirror(redis_client, config):
    """A LiveMirror on keys unique to this test, cleaned up afterwards."""
    from paradas_faena.live import LiveMirror

    live = config.live
    yield LiveMirror(redis_client, live)
    keys = [live.noria_key, live.puestos_key, live.daemon_key]
    keys += list(redis_client.scan_iter(f"{live.key_prefix}:plc:*"))
    redis_client.delete(*keys)


REPORTING = [
    "00_parametros",
    "01_puesto_config",
    "10_dim_puesto",
    "30_input_segmento",
    "60_monitor_hoy",
]


@pytest.fixture(scope="session")
def _reporting_schema(db_conn):
    """La cadena de vistas que necesita v_monitor_hoy, una sola vez.

    Solo vistas: las tablas base (reporting.parametros, reporting.puesto_config) las crea
    la migracion 008, que ya corrio en _build_schema. El resto de reporting/ arrastra el
    schema `produccion`, que no esta en las migraciones de este repo.
    """
    with db_conn.cursor() as cur:
        for name in REPORTING:
            cur.execute((ROOT / "reporting" / f"{name}.sql").read_text())
    return db_conn


@pytest.fixture
def reporting_db(db, _reporting_schema):
    """Tablas vacias y las vistas de reporting en pie."""
    return db


TZ_LOCAL = "America/Argentina/Buenos_Aires"
_AHORA_LOCAL = dt.datetime.now(zoneinfo.ZoneInfo(TZ_LOCAL))

# Los tests ubican datos "hace N minutos" y esperan que caigan en el dia de hoy. En los
# primeros minutos despues de la medianoche local eso deja de ser cierto y medirian el
# dia anterior. Correrlos ahi no aporta nada; el falso rojo si molesta.
demasiado_temprano = pytest.mark.skipif(
    _AHORA_LOCAL.hour < 2,
    reason=f"son las {_AHORA_LOCAL:%H:%M} en {TZ_LOCAL}; los datos caerian en ayer",
)


def con_inicio_de_turno(db, hora: str) -> None:
    """Mueve la ventana que mira el monitor.

    Desde la migracion 008 esto es un UPDATE y nada mas: antes habia que volver a crear
    v_parametros, y con ella todo lo que cuelga.
    """
    with db.cursor() as cur:
        cur.execute(
            "update reporting.parametros set monitor_hora_inicio = %s", (hora,)
        )


@pytest.fixture
def monitor_state(config):
    """A MonitorState with nothing started: enough to exercise the pure translation."""
    from paradas_faena.monitor.app import MonitorState

    return MonitorState(config)


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

    from paradas_faena.config import (
        Config,
        DbConfig,
        LiveConfig,
        MonitorConfig,
        RedisConfig,
    )

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
    live = LiveConfig(
        enabled=True,
        # Unico por test, igual que stream_names: dos tests no pueden pisarse las claves.
        key_prefix=f"test:{stream_names.split(':')[1]}:live",
        speed_seconds=0.0,
        ttl_seconds=5.0,
        timeout_seconds=0.5,
        retry_seconds=0.2,
    )
    monitor = MonitorConfig(
        host="127.0.0.1", port=0, refresh_seconds=1.0, heartbeat_seconds=1.0,
        queue_size=8, stale_seconds=15.0,
    )
    return Config(
        db=db, redis=redis_config, live=live, monitor=monitor,
        frec_tag="frec", status_tag="status",
        poll_seconds=TEST_POLL_SECONDS, reassert_seconds=TEST_REASSERT_SECONDS,
        heartbeat_seconds=60.0,
        status_grace_seconds=30.0, noria_conv=4.23, speed_deadband_hz=0.2,
        speed_max_interval_seconds=60.0, persist_live_speed=False,
        edu_path=tmp_path, stopfile_poll_seconds=0.05,
        batch_max_events=100,
        backoff_initial_seconds=0.05, backoff_max_seconds=0.2,
        shutdown_grace_seconds=5.0, log_level="CRITICAL",
    )
