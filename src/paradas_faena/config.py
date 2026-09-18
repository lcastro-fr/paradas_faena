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


_TRUE = {"1", "true", "yes", "on", "si"}
_FALSE = {"0", "false", "no", "off"}


def _bool(name: str, default: bool) -> bool:
    raw = _optional(name, "true" if default else "false").lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ConfigError(f"{name}={raw!r} no es un booleano; use true o false")


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
class LiveConfig:
    """The ephemeral mirror the live monitor reads.

    Deliberately separate from RedisConfig: that one carries the durable path, where an
    event not delivered is an event lost. Here a write that fails is simply skipped --
    see live.LiveMirror.
    """

    enabled: bool
    key_prefix: str
    speed_seconds: float
    ttl_seconds: float
    timeout_seconds: float
    retry_seconds: float

    @classmethod
    def from_env(cls, poll_seconds: float) -> LiveConfig:
        """Tambien la usa la app web, que necesita el layout de claves del espejo."""
        return cls(
            enabled=_bool("LIVE_ENABLED", True),
            key_prefix=_optional("LIVE_KEY_PREFIX", "paradas:live"),
            speed_seconds=_float("LIVE_SPEED_SECONDS", 1.0, minimum=0.0),
            # Derivado de poll por el mismo motivo que max_segment(): la caida de un PLC
            # se detecta porque su clave vence, asi que el TTL tiene que sobrevivir a un
            # par de ciclos. Un default fijo se rompe solo en cuanto POLL_SECONDS crece.
            ttl_seconds=_float(
                "LIVE_TTL_SECONDS", max(5.0, 3.0 * poll_seconds), minimum=1.0
            ),
            # Corto a proposito: el cliente durable usa 2 s, y a ese timeout un Redis
            # caido le comeria dos segundos a cada tick de un segundo.
            timeout_seconds=_float("LIVE_TIMEOUT_SECONDS", 0.5, minimum=0.05),
            retry_seconds=_float("LIVE_RETRY_SECONDS", 5.0, minimum=0.0),
        )

    @property
    def noria_key(self) -> str:
        return f"{self.key_prefix}:noria"

    @property
    def puestos_key(self) -> str:
        return f"{self.key_prefix}:puestos"

    @property
    def daemon_key(self) -> str:
        return f"{self.key_prefix}:daemon"

    def plc_key(self, ip: str) -> str:
        return f"{self.key_prefix}:plc:{ip}"


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    """The live monitor's own settings. Every one has a default, so adding this never
    gives the daemon a new way to fail at startup."""

    host: str
    port: int
    # Los acumulados del dia solo se mueven cuando CIERRA una parada, y al lado hay un
    # cronometro vivo: refrescarlos mas seguido serian miles de consultas por dia sin
    # ninguna diferencia visible.
    refresh_seconds: float
    heartbeat_seconds: float
    queue_size: int
    stale_seconds: float


@dataclass(frozen=True, slots=True)
class Config:
    db: DbConfig
    redis: RedisConfig
    live: LiveConfig
    monitor: MonitorConfig

    frec_tag: str
    status_tag: str

    poll_seconds: float
    reassert_seconds: float
    heartbeat_seconds: float
    status_grace_seconds: float

    noria_conv: float
    speed_deadband_hz: float
    speed_max_interval_seconds: float
    # Las muestras de LiveSpeed son para la pantalla, no para la serie historica: el
    # writer las descarta salvo que esto diga lo contrario.
    persist_live_speed: bool

    edu_path: Path
    stopfile_poll_seconds: float

    batch_max_events: int
    backoff_initial_seconds: float
    backoff_max_seconds: float
    shutdown_grace_seconds: float
    log_level: str

    def max_segment(self) -> dt.timedelta:
        """The segment cap for the duration queries.

        While a relay reads true the daemon rewrites its row on the first tick at or
        after reassert_seconds, so a live segment is reassert_seconds rounded *up* to the
        next multiple of poll_seconds -- up to a whole poll longer:

            segmento real = ceil(reassert_seconds / poll_seconds) * poll_seconds

        The cap has to clear that. If it does not, every re-assert loses the difference,
        always downwards, and long stops are under-reported. The extra second covers
        jitter.

        This is why the cap depends on poll_seconds and not on a percentage of
        reassert_seconds: the error is one poll interval, which has nothing to do with
        how long reassert_seconds is.

        One caveat: if a read takes longer than poll_seconds, the tick spacing becomes
        the read time and this bound no longer holds. Keep POLL_SECONDS above the read
        time tools/probe_read.py reports -- on the Micro820s that is 300-525 ms.

        Pass it to the queries as %(max_segment)s; never hardcode an interval in the SQL.
        """
        return dt.timedelta(seconds=self.reassert_seconds + self.poll_seconds + 1.0)

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
        # Con LiveSpeed a 1 Hz entran ~86k entradas por dia ademas de las durables. Con
        # el tope viejo de 100k el colchon ante un writer caido bajaba de ~17 dias a poco
        # mas de uno, y pasado eso Redis evicciona filas que nadie escribio todavia.
        maxlen=_int("REDIS_MAXLEN", 1_000_000, minimum=1000),
        block_ms=_int("REDIS_BLOCK_MS", 1000, minimum=10),
        buffer_size=_int("PUBLISH_BUFFER_SIZE", 5000, minimum=10),
    )

    poll = _float("POLL_SECONDS", 0.5, minimum=0.05)
    reassert = _float("REASSERT_SECONDS", 60.0, minimum=1.0)
    if reassert <= poll:
        raise ConfigError(
            f"REASSERT_SECONDS={reassert} debe ser mayor que POLL_SECONDS={poll}"
        )

    live = LiveConfig.from_env(poll)
    # El default ya cumple esto; la validacion es para un LIVE_TTL_SECONDS puesto a mano
    # demasiado corto, que venceria en un tick lento sin que hubiera pasado nada y haria
    # parpadear la pantalla.
    if live.enabled and live.ttl_seconds < 2 * poll:
        raise ConfigError(
            f"LIVE_TTL_SECONDS={live.ttl_seconds} debe ser >= 2*POLL_SECONDS={2 * poll}"
        )

    monitor = MonitorConfig(
        host=_optional("MONITOR_HOST", "0.0.0.0"),
        port=_int("MONITOR_PORT", 8080, minimum=1),
        refresh_seconds=_float("MONITOR_REFRESH_SECONDS", 60.0, minimum=1.0),
        heartbeat_seconds=_float("MONITOR_HEARTBEAT_SECONDS", 5.0, minimum=1.0),
        queue_size=_int("MONITOR_QUEUE_SIZE", 200, minimum=1),
        stale_seconds=_float("MONITOR_STALE_SECONDS", 15.0, minimum=1.0),
    )

    return Config(
        db=db,
        redis=redis_config,
        live=live,
        monitor=monitor,
        frec_tag=_optional("TAG_FREC", "frec"),
        status_tag=_optional("TAG_STATUS", "status"),
        poll_seconds=poll,
        reassert_seconds=reassert,
        heartbeat_seconds=_float("HEARTBEAT_SECONDS", 60.0, minimum=1.0),
        status_grace_seconds=_float("STATUS_GRACE_SECONDS", 30.0, minimum=0.0),
        noria_conv=_float("NORIA_CONV", 4.23),
        speed_deadband_hz=_float("SPEED_DEADBAND_HZ", 0.2, minimum=0.0),
        speed_max_interval_seconds=_float("SPEED_MAX_INTERVAL_SECONDS", 60.0, minimum=1.0),
        persist_live_speed=_bool("PERSIST_LIVE_SPEED", False),
        edu_path=Path(_require("EDU_PATH")),
        stopfile_poll_seconds=_float("STOPFILE_POLL_SECONDS", 30.0, minimum=1.0),
        batch_max_events=_int("BATCH_MAX_EVENTS", 500, minimum=1),
        backoff_initial_seconds=_float("BACKOFF_INITIAL_SECONDS", 1.0, minimum=0.1),
        backoff_max_seconds=_float("BACKOFF_MAX_SECONDS", 30.0, minimum=1.0),
        shutdown_grace_seconds=_float("SHUTDOWN_GRACE_SECONDS", 20.0, minimum=1.0),
        log_level=_optional("LOG_LEVEL", "INFO").upper(),
    )
