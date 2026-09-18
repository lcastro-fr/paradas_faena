import os

import pytest
import pytest_asyncio
from psycopg.conninfo import conninfo_to_dict


@pytest.fixture(scope="session")
def django_db_modify_db_settings() -> None:
    """Apunta las dos conexiones al Postgres de PF_TEST_DSN.

    `faena` usa esa misma base, que la arma tests/conftest.py con dbmate. `default` conserva
    su nombre: es de Django y la crea el runner como test_<nombre>.
    """
    dsn = os.environ.get("PF_TEST_DSN")
    if not dsn:
        return
    from django.conf import settings

    partes = conninfo_to_dict(dsn)
    servidor = {
        "HOST": partes.get("host", "localhost"),
        "PORT": str(partes.get("port", 5432)),
        "USER": partes.get("user", ""),
        "PASSWORD": partes.get("password", ""),
    }
    settings.DATABASES["default"].update(servidor)
    settings.DATABASES["faena"].update(servidor, NAME=partes.get("dbname", ""))


@pytest.fixture(scope="session")
def django_db_setup(django_db_modify_db_settings, django_db_blocker):
    """Sólo crea la base de Django.

    Una `test_faena` creada de cero no tendría monitoreo_faena —lo arma dbmate— y todo
    modelo managed=False reventaría.
    """
    from django.test.utils import setup_databases, teardown_databases

    with django_db_blocker.unblock():
        config = setup_databases(verbosity=0, interactive=False, aliases={"default"})
    yield
    with django_db_blocker.unblock():
        teardown_databases(config, verbosity=0)


requires_redis = pytest.mark.skipif(
    os.environ.get("PF_TEST_REDIS_URL") is None,
    reason="PF_TEST_REDIS_URL no definido; ver tests/conftest.py",
)


@pytest.fixture
def live_cfg(settings):
    """Prefijo propio por test: dos tests no pueden pisarse las claves del espejo."""
    import uuid

    from paradas_faena.config import LiveConfig

    cfg = LiveConfig(
        enabled=True,
        key_prefix=f"test:{uuid.uuid4().hex[:12]}",
        speed_seconds=0.0,
        ttl_seconds=5.0,
        timeout_seconds=0.5,
        retry_seconds=0.2,
    )
    settings.LIVE_CONFIG = cfg
    settings.REDIS_URL = os.environ.get("PF_TEST_REDIS_URL", "")
    yield cfg

    import redis as redis_sync

    cliente = redis_sync.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    claves = [cfg.noria_key, cfg.puestos_key, cfg.daemon_key]
    claves += list(cliente.scan_iter(f"{cfg.key_prefix}:plc:*"))
    cliente.delete(*claves)
    cliente.close()


@pytest.fixture
def mirror(live_cfg):
    """El daemon escribiendo. Los tests leen lo que él escribe, no payloads a mano."""
    import redis as redis_sync

    from paradas_faena.live import LiveMirror

    cliente = redis_sync.Redis.from_url(
        os.environ["PF_TEST_REDIS_URL"], decode_responses=True
    )
    yield LiveMirror(cliente, live_cfg)
    cliente.close()


@pytest_asyncio.fixture
async def espejo(live_cfg):
    from espejo.redis import EspejoRedis, build_cliente

    cliente = build_cliente(os.environ["PF_TEST_REDIS_URL"])
    yield EspejoRedis(cliente, live_cfg, 15.0)
    await cliente.aclose()
