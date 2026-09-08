"""Configuration, read and validated once at startup."""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(Exception):
    pass


# `USER` is deliberately absent: python-dotenv does not override variables already in the
# environment and every POSIX shell exports USER, so reading the database user from it
# would silently connect as the container user. DB_USER has no fallback.
_LEGACY_NAMES = {
    "DB_HOST": "HOST",
    "DB_NAME": "DBNAME",
    "DB_PASSWORD": "PASSWORD",
    "DB_SCHEMA": "SCHEMA",
}


def _raw(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None and name in _LEGACY_NAMES:
        value = os.environ.get(_LEGACY_NAMES[name])
    if value is None:
        return None
    return value.strip().strip("'\"")


def _require(name: str) -> str:
    value = _raw(name)
    if not value:
        legacy = _LEGACY_NAMES.get(name)
        hint = f" (o el antiguo {legacy})" if legacy else ""
        raise ConfigError(f"Falta la variable de entorno {name}{hint}")
    return value


def _optional(name: str, default: str) -> str:
    value = _raw(name)
    return default if value is None or value == "" else value


def _float(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = _optional(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r} no es un numero") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name}={value} debe ser >= {minimum}")
    return value


def _int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = _optional(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r} no es un entero") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name}={value} debe ser >= {minimum}")
    return value


@dataclass(frozen=True, slots=True)
class DbConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str
    schema: str

    def conninfo(self) -> str:
        return (
            f"host={self.host} port={self.port} dbname={self.dbname} "
            f"user={self.user} password={self.password} "
            f"connect_timeout=10 application_name=paradas_faena"
        )

    def __repr__(self) -> str:
        return (
            f"DbConfig(host={self.host!r}, port={self.port}, dbname={self.dbname!r}, "
            f"user={self.user!r}, password='***', schema={self.schema!r})"
        )


@dataclass(frozen=True, slots=True)
class RedisConfig:
    url: str
    stream: str
    group: str
    consumer: str
    maxlen: int
    block_ms: int
    buffer_size: int


@dataclass(frozen=True, slots=True)
class Config:
    db: DbConfig
    redis: RedisConfig

    input_array_tag: str
    frec_tag: str
    status_tag: str

    poll_seconds: float
    reassert_seconds: float
    heartbeat_seconds: float
    status_grace_seconds: float

    noria_conv: float
    speed_deadband_hz: float
    speed_max_interval_seconds: float

    edu_path: Path
    stopfile_poll_seconds: float

    batch_max_events: int
    backoff_initial_seconds: float
    backoff_max_seconds: float
    shutdown_grace_seconds: float
    log_level: str

    def max_segment(self) -> dt.timedelta:
        """The segment cap for the duration queries.

        Coupled to reassert_seconds: while a relay reads true a row is written every
        reassert_seconds, so a live segment never exceeds it and anything longer is a data
        gap. Pass this to the queries as %(max_segment)s instead of hardcoding an interval
        in the SQL -- if the two disagree, real stop time is silently truncated.
        """
        return dt.timedelta(seconds=self.reassert_seconds * 1.05 + 1.0)

    def heartbeat_interval(self) -> dt.timedelta:
        return dt.timedelta(seconds=self.heartbeat_seconds)


def load_config(*, dotenv: bool = True) -> Config:
    if dotenv:
        # override=False: in Docker the real environment wins over a baked-in .env.
        load_dotenv(override=False)

    db = DbConfig(
        host=_require("DB_HOST"),
        port=_int("DB_PORT", 5432, minimum=1),
        dbname=_require("DB_NAME"),
        user=_require("DB_USER"),
        password=_require("DB_PASSWORD"),
        schema=_require("DB_SCHEMA"),
    )

    stream = _optional("REDIS_STREAM", "paradas:events")
    redis_config = RedisConfig(
        url=_optional("REDIS_URL", "redis://redis:6379/0"),
        stream=stream,
        group=_optional("REDIS_GROUP", "writer"),
        # Must be stable across restarts, or entries read but never acknowledged are
        # orphaned under a consumer name nothing will ever read again.
        consumer=_optional("REDIS_CONSUMER", "writer"),
        maxlen=_int("REDIS_MAXLEN", 100_000, minimum=1000),
        block_ms=_int("REDIS_BLOCK_MS", 1000, minimum=10),
        buffer_size=_int("PUBLISH_BUFFER_SIZE", 5000, minimum=10),
    )

    poll = _float("POLL_SECONDS", 0.5, minimum=0.05)
    reassert = _float("REASSERT_SECONDS", 60.0, minimum=1.0)
    if reassert <= poll:
        raise ConfigError(
            f"REASSERT_SECONDS={reassert} debe ser mayor que POLL_SECONDS={poll}"
        )

    return Config(
        db=db,
        redis=redis_config,
        input_array_tag=_optional("TAG_INPUT_ARRAY", "InputStatus"),
        frec_tag=_optional("TAG_FREC", "frec"),
        status_tag=_optional("TAG_STATUS", "status"),
        poll_seconds=poll,
        reassert_seconds=reassert,
        heartbeat_seconds=_float("HEARTBEAT_SECONDS", 60.0, minimum=1.0),
        status_grace_seconds=_float("STATUS_GRACE_SECONDS", 30.0, minimum=0.0),
        noria_conv=_float("NORIA_CONV", 4.23),
        speed_deadband_hz=_float("SPEED_DEADBAND_HZ", 0.2, minimum=0.0),
        speed_max_interval_seconds=_float("SPEED_MAX_INTERVAL_SECONDS", 60.0, minimum=1.0),
        edu_path=Path(_require("EDU_PATH")),
        stopfile_poll_seconds=_float("STOPFILE_POLL_SECONDS", 30.0, minimum=1.0),
        batch_max_events=_int("BATCH_MAX_EVENTS", 500, minimum=1),
        backoff_initial_seconds=_float("BACKOFF_INITIAL_SECONDS", 1.0, minimum=0.1),
        backoff_max_seconds=_float("BACKOFF_MAX_SECONDS", 30.0, minimum=1.0),
        shutdown_grace_seconds=_float("SHUTDOWN_GRACE_SECONDS", 20.0, minimum=1.0),
        log_level=_optional("LOG_LEVEL", "INFO").upper(),
    )
