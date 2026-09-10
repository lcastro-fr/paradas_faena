from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass

import psycopg
from psycopg import sql

from paradas_faena.config import DbConfig
from paradas_faena.events import (
    CounterIncrement,
    Heartbeat,
    InputEdge,
    NoriaStatusEdge,
    SessionClosed,
    SpeedSample,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PlcConfig:
    ip: str
    nombre: str | None
    variador: bool


@dataclass(frozen=True, slots=True)
class CounterConfig:
    ip: str
    tag: str
    name: str | None
    # The tag name of this puesto's stop input, e.g. '_IO_EM_DI_00'. The controllers are
    # Micro820s: each digital input is its own BOOL, not a position in an array.
    input_tag: str | None
    # Configuration generation. Rewiring inserts a new version; rows already recorded
    # keep pointing at the one that was true when they were read.
    version: int


class Repository:
    """Owns the single database connection. Used from one thread only."""

    def __init__(self, conn: psycopg.Connection, schema: str) -> None:
        self._conn = conn
        self._schema = schema

    @staticmethod
    def connect(db: DbConfig) -> Repository:
        conn = psycopg.connect(db.conninfo(), autocommit=False)
        return Repository(conn, db.schema)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._conn.close()

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        with contextlib.suppress(Exception):
            self._conn.rollback()

    def ping(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute("select 1")
        self._conn.commit()

    def _q(self, template: str) -> sql.Composed:
        return sql.SQL(template).format(schema=sql.Identifier(self._schema))

    # -- configuration ---------------------------------------------------------------

    def load_plcs(self) -> list[PlcConfig]:
        with self._conn.cursor() as cur:
            cur.execute(
                self._q("select host(ip), nombre, variador from {schema}.plcs order by ip")
            )
            rows = cur.fetchall()
        self._conn.commit()
        return [PlcConfig(ip=r[0], nombre=r[1], variador=r[2]) for r in rows]

    def load_counters(self) -> list[CounterConfig]:
        with self._conn.cursor() as cur:
            cur.execute(
                self._q(
                    # The highest version per (ip, tag) is the live configuration.
                    "select distinct on (ip, tag) "
                    "  host(ip), tag, name, input_tag, version "
                    "from {schema}.counters_name "
                    "order by ip, tag, version desc"
                )
            )
            rows = cur.fetchall()
        self._conn.commit()
        return [
            CounterConfig(ip=r[0], tag=r[1], name=r[2], input_tag=r[3], version=r[4])
            for r in rows
        ]

    # -- writes ----------------------------------------------------------------------

    def insert_counters(self, events: list[CounterIncrement]) -> None:
        if not events:
            return
        with self._conn.cursor() as cur:
            cur.executemany(
                self._q(
                    "insert into {schema}.paradas "
                    "(ip, tag, version, old_value, new_value, dif, ts, "
                    " status_noria, vel, event_uid) "
                    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "on conflict (event_uid) do nothing"
                ),
                [
                    (
                        e.ip,
                        e.tag,
                        e.version,
                        e.old_value,
                        e.new_value,
                        e.dif,
                        e.ts,
                        e.noria_running,
                        e.vel,
                        e.event_uid,
                    )
                    for e in events
                ],
            )

    def insert_input_edges(self, events: list[InputEdge]) -> None:
        """Write relay transitions.

        `reason` is not stored: the reporting queries only need (ts, value).
        """
        if not events:
            return
        with self._conn.cursor() as cur:
            cur.executemany(
                self._q(
                    "insert into {schema}.input_status (ip, tag, version, ts, value) "
                    "values (%s, %s, %s, %s, %s) "
                    "on conflict (ip, tag, ts) do nothing"
                ),
                [(e.ip, e.tag, e.version, e.ts, e.value) for e in events],
            )

    def insert_speed(self, events: list[SpeedSample]) -> None:
        if not events:
            return
        with self._conn.cursor() as cur:
            cur.executemany(
                self._q(
                    "insert into {schema}.velocidad "
                    "(ts, frec, vel, status_noria, event_uid) "
                    "values (%s, %s, %s, %s, %s) "
                    "on conflict (event_uid) do nothing"
                ),
                [(e.ts, e.frec, e.vel, e.noria_running, e.event_uid) for e in events],
            )

    def insert_noria_status(self, events: list[NoriaStatusEdge]) -> None:
        if not events:
            return
        with self._conn.cursor() as cur:
            cur.executemany(
                self._q(
                    "insert into {schema}.noria_status (ip, ts, running) "
                    "values (%s, %s, %s) on conflict (ip, ts) do nothing"
                ),
                [(e.ip, e.ts, e.running) for e in events],
            )

    def insert_heartbeats(self, events: list[Heartbeat]) -> None:
        if not events:
            return
        with self._conn.cursor() as cur:
            cur.executemany(
                self._q(
                    "insert into {schema}.plc_heartbeat (ip, ts) "
                    "values (%s, %s) on conflict (ip, ts) do nothing"
                ),
                [(e.ip, e.ts) for e in events],
            )

    def upsert_generales(self, events: list[SessionClosed]) -> None:
        if not events:
            return
        with self._conn.cursor() as cur:
            cur.executemany(
                self._q(
                    "insert into {schema}.generales "
                    "(fecha, hora_inicio, hora_fin, registros) "
                    "values (%s, %s, %s, %s) "
                    "on conflict (fecha) do update set "
                    "  hora_inicio = excluded.hora_inicio, "
                    "  hora_fin    = excluded.hora_fin, "
                    "  registros   = excluded.registros"
                ),
                [(e.fecha, e.hora_inicio, e.hora_fin, e.registros) for e in events],
            )
