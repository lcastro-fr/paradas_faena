"""Tests for the two configuration tables the reporting layer stands on.

reporting.parametros y reporting.puesto_config eran VALUES adentro de una vista. Cambiar
un numero era un CREATE OR REPLACE VIEW, y esa sentencia solo deja sumar columnas al
final: intercalar una obliga a un DROP CASCADE que se lleva la capa entera. Ahora son
tablas (migracion 008) y las vistas quedaron como lo que siempre fueron -- formulas.

Lo que se verifica aca es lo que la vista NO podia garantizar, mas el contrato de
columnas del que depende poder seguir usando CREATE OR REPLACE.
"""

from __future__ import annotations

import psycopg
import pytest
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def cfg(reporting_db):
    return reporting_db


def falla(db, sql: str, *params) -> str:
    with pytest.raises(psycopg.errors.IntegrityError) as exc, db.cursor() as cur:
        cur.execute(sql, params)
    db.rollback()
    return str(exc.value)


# --- lo que la vista no podia garantizar ----------------------------------------------


def test_a_second_row_of_parameters_is_rejected(cfg):
    assert "parametros_pkey" in falla(
        cfg, "insert into reporting.parametros default values"
    )


def test_a_reassert_shorter_than_the_poll_is_rejected(cfg):
    assert "parametros_reassert_mayor_al_poll" in falla(
        cfg, "update reporting.parametros set poll_seconds = 999"
    )


def test_a_franja_that_does_not_divide_the_day_is_rejected(cfg):
    assert "parametros_franja_divide_el_dia" in falla(
        cfg, "update reporting.parametros set franja_minutos = 7"
    )


def test_a_puesto_that_does_not_exist_is_rejected(cfg):
    assert "puesto_config_counters_name_fk" in falla(
        cfg,
        "insert into reporting.puesto_config (ip, tag, version, orden_linea) "
        "values ('172.30.10.8', 'NoExiste', 1, 1)",
    )


def test_a_negative_position_on_the_line_is_rejected(cfg):
    assert "puesto_config_orden_positivo" in falla(
        cfg,
        "insert into reporting.puesto_config (ip, tag, version, orden_linea) "
        "values ('172.30.10.8', 'Cont_P1', 1, -3)",
    )


# --- editar ya no recrea nada ---------------------------------------------------------


def test_changing_a_value_is_an_update_and_the_view_follows(cfg):
    with cfg.cursor() as cur:
        cur.execute("update reporting.parametros set monitor_hora_inicio = '05:30'")
        cur.execute("select monitor_hora_inicio from reporting.v_parametros")
        assert str(cur.fetchone()[0]) == "05:30:00"


def test_a_derived_value_recomputes_from_the_stored_one(cfg):
    with cfg.cursor() as cur:
        cur.execute("update reporting.parametros set reassert_seconds = 120")
        cur.execute("select max_segment_s from reporting.v_parametros")
        assert float(cur.fetchone()[0]) == 122.0


def test_the_survey_table_reaches_the_view(cfg):
    with cfg.cursor() as cur:
        cur.execute(
            "insert into reporting.puesto_config (ip, tag, version, orden_linea) "
            "values ('172.30.10.8', 'Cont_P1', 1, 1)"
        )
        cur.execute("select tag, orden_linea from reporting.v_puesto_config")
        assert cur.fetchall() == [("Cont_P1", 1)]


def test_an_empty_survey_leaves_every_offset_at_zero(cfg):
    """Es el estado de hoy en produccion y el tablero tiene que tolerarlo."""
    with cfg.cursor() as cur:
        cur.execute(
            "select count(*), count(*) filter (where offset_sin_configurar) "
            "from reporting.v_dim_puesto where es_version_vigente"
        )
        total, sin_configurar = cur.fetchone()
    assert total > 0
    assert sin_configurar == total


# --- el contrato del que depende CREATE OR REPLACE ------------------------------------


COLUMNAS = [
    "reassert_seconds",
    "poll_seconds",
    "heartbeat_seconds",
    "speed_max_interval_seconds",
    "max_segment_extra_s",
    "objetivo_cabezas_hora",
    "rampa_segundos",
    "circuito_minutos",
    "margen_jornada_min",
    "franja_minutos",
    "est_fae",
    "tz",
    "max_segment",
    "max_segment_vel",
    "franja_intervalo",
    "franjas_por_dia",
    "max_segment_s",
    "max_segment_vel_s",
    "monitor_hora_inicio",
]


def test_the_parameter_view_keeps_its_column_order(cfg):
    """CREATE OR REPLACE VIEW solo deja sumar columnas AL FINAL. Si alguien intercala
    una, aplicar reporting/00 en produccion falla y la unica salida es un DROP CASCADE
    que se lleva la capa entera. Este test es lo que hace que se entere antes.
    """
    with cfg.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns "
            "where table_schema = 'reporting' and table_name = 'v_parametros' "
            "order by ordinal_position"
        )
        assert [c for (c,) in cur.fetchall()] == COLUMNAS
