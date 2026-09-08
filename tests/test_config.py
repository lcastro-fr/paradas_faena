"""Tests for configuration loading, which happens once at startup."""

from __future__ import annotations

import datetime as dt
import math

import pytest

from paradas_faena.config import ConfigError, load_config

REQUIRED = {
    "DB_HOST": "192.168.100.140",
    "DB_NAME": "public_rioplatense",
    "DB_USER": "sistemas",
    "DB_PASSWORD": "secret",
    "DB_SCHEMA": "paradas_faena",
    "EDU_PATH": "/mnt/tipificador",
}

# Everything load_config might read, so a stray value in the developer's real
# environment cannot change a test's outcome.
_ALL = [
    *REQUIRED, "HOST", "DBNAME", "PASSWORD", "SCHEMA", "USER", "DB_PORT", "NORIA_CONV",
    "POLL_SECONDS", "REASSERT_SECONDS", "HEARTBEAT_SECONDS", "STATUS_GRACE_SECONDS",
    "SPEED_DEADBAND_HZ", "SPEED_MAX_INTERVAL_SECONDS", "STOPFILE_POLL_SECONDS",
    "TAG_FREC", "TAG_STATUS", "BATCH_MAX_EVENTS",
    "REDIS_URL", "REDIS_STREAM", "REDIS_GROUP", "REDIS_CONSUMER", "REDIS_MAXLEN",
    "REDIS_BLOCK_MS", "PUBLISH_BUFFER_SIZE",
    "BACKOFF_INITIAL_SECONDS", "BACKOFF_MAX_SECONDS", "SHUTDOWN_GRACE_SECONDS",
    "LOG_LEVEL",
]


@pytest.fixture
def env(monkeypatch):
    for name in _ALL:
        monkeypatch.delenv(name, raising=False)

    def apply(**overrides):
        values = {**REQUIRED, **overrides}
        for key, value in values.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, str(value))
        # dotenv=False so the project's real .env cannot leak into a test.
        return load_config(dotenv=False)

    return apply


def test_minimal_environment_loads_with_defaults(env):
    config = env()
    assert config.db.host == "192.168.100.140"
    assert config.db.port == 5432
    assert config.poll_seconds == 0.5
    assert config.reassert_seconds == 60.0
    assert config.noria_conv == 4.23


def test_the_database_user_is_never_read_from_USER(env, monkeypatch):
    """python-dotenv does not override existing variables and every POSIX shell exports
    USER, so reading the database user from it would connect as the container user."""
    monkeypatch.setenv("USER", "root")
    assert env().db.user == "sistemas"

    with pytest.raises(ConfigError, match="DB_USER"):
        env(DB_USER=None)


def test_legacy_names_still_work_for_the_other_database_settings(env):
    """An existing .env keeps working for everything but the user."""
    config = env(DB_HOST=None, HOST="10.0.0.1", DB_NAME=None, DBNAME="db",
                 DB_PASSWORD=None, PASSWORD="pw", DB_SCHEMA=None, SCHEMA="s")
    assert (config.db.host, config.db.dbname, config.db.password, config.db.schema) == (
        "10.0.0.1", "db", "pw", "s"
    )


def test_quoted_and_padded_values_are_cleaned(env):
    """The existing .env has EDU_PATH in single quotes, with CRLF line endings."""
    config = env(EDU_PATH="'//frpacheco12/ser_desp/STOCK/tipificador'")
    assert str(config.edu_path) == "//frpacheco12/ser_desp/STOCK/tipificador"


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_each_required_variable_is_reported_by_name(env, missing):
    with pytest.raises(ConfigError, match=missing):
        env(**{missing: None})


def test_a_non_numeric_conversion_factor_is_rejected_at_startup(env):
    with pytest.raises(ConfigError, match="NORIA_CONV"):
        env(NORIA_CONV="cuatro")


def test_an_absurd_poll_interval_is_rejected(env):
    with pytest.raises(ConfigError, match="POLL_SECONDS"):
        env(POLL_SECONDS="0")


def test_reassert_must_be_longer_than_the_poll_interval(env):
    """Otherwise every poll writes a row for every held relay."""
    with pytest.raises(ConfigError, match="REASSERT_SECONDS"):
        env(POLL_SECONDS="0.5", REASSERT_SECONDS="0.5")


def test_the_password_is_not_in_the_repr(env):
    """DbConfig is logged at startup."""
    config = env(DB_PASSWORD="Prod*4567")
    assert "Prod*4567" not in repr(config.db)
    assert "***" in repr(config.db)
    assert "Prod*4567" in config.db.conninfo()      # but it does reach libpq


def test_the_connection_string_carries_a_timeout_and_an_application_name(env):
    """application_name finds this daemon in pg_stat_activity."""
    conninfo = env().db.conninfo()
    assert "connect_timeout=" in conninfo
    assert "application_name=paradas_faena" in conninfo


def test_the_segment_cap_is_derived_from_the_reassert_interval(env):
    """The coupling that must never be hardcoded in the SQL."""
    assert env(REASSERT_SECONDS="60").max_segment() > dt.timedelta(seconds=60)
    assert env(REASSERT_SECONDS="30").max_segment() > dt.timedelta(seconds=30)
    assert env(REASSERT_SECONDS="30").max_segment() < dt.timedelta(seconds=60)


def test_the_cap_always_exceeds_the_interval_it_is_derived_from(env):
    """A cap below the interval would silently truncate real stop time."""
    for seconds in (5, 15, 30, 60, 120, 600):
        config = env(REASSERT_SECONDS=str(seconds))
        assert config.max_segment().total_seconds() > seconds


def test_the_cap_also_accounts_for_the_poll_interval(env):
    """The error is one poll interval, not a percentage of the re-assert interval.

    The row is rewritten on the first tick at or after reassert_seconds, so a live
    segment is reassert rounded up to the next multiple of poll. A cap derived only from
    reassert (the old `reassert * 1.05 + 1`) breaks as soon as poll is a meaningful
    fraction of it -- reassert=10 with poll=3 gives a real segment of 12 s against a cap
    of 11.5 s, truncating half a second on every re-assert.
    """
    for reassert, poll in [
        (60, 0.5), (60, 1.5), (60, 7), (60, 11),
        (30, 4), (10, 1.5), (10, 3), (10, 9),
    ]:
        config = env(REASSERT_SECONDS=str(reassert), POLL_SECONDS=str(poll))
        worst = math.ceil(reassert / poll) * poll
        assert config.max_segment().total_seconds() > worst, (
            f"REASSERT={reassert} POLL={poll}: tope "
            f"{config.max_segment().total_seconds()} <= segmento real {worst}"
        )


def test_a_slower_poll_raises_the_cap(env):
    slow = env(REASSERT_SECONDS="60", POLL_SECONDS="5").max_segment()
    fast = env(REASSERT_SECONDS="60", POLL_SECONDS="0.5").max_segment()
    assert slow > fast


# --- Redis ----------------------------------------------------------------------------


def test_redis_defaults_target_the_compose_service(env):
    redis_config = env().redis
    assert redis_config.url == "redis://redis:6379/0"
    assert redis_config.stream == "paradas:events"
    assert redis_config.group == "writer"
    assert redis_config.consumer == "writer"


def test_redis_settings_are_overridable(env):
    redis_config = env(
        REDIS_URL="redis://127.0.0.1:6379/1",
        REDIS_STREAM="otro:stream",
        REDIS_CONSUMER="writer-b",
        REDIS_MAXLEN="5000",
        PUBLISH_BUFFER_SIZE="250",
    ).redis
    assert redis_config.url == "redis://127.0.0.1:6379/1"
    assert redis_config.stream == "otro:stream"
    assert redis_config.consumer == "writer-b"
    assert redis_config.maxlen == 5000
    assert redis_config.buffer_size == 250


def test_a_non_numeric_redis_setting_is_rejected_at_startup(env):
    with pytest.raises(ConfigError, match="REDIS_MAXLEN"):
        env(REDIS_MAXLEN="muchos")


def test_an_absurdly_small_stream_cap_is_rejected(env):
    """A cap near the batch size would trim entries before the writer could read them."""
    with pytest.raises(ConfigError, match="REDIS_MAXLEN"):
        env(REDIS_MAXLEN="10")

