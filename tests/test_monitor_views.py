"""Tests for reporting.v_monitor_hoy, the only thing the live monitor asks the database.

Dos cosas se verifican aca. Una es la ventana: el monitor cuenta el TURNO, no el dia, y
contar de mas es exactamente como el numero deja de coincidir con el tablero. La otra es
que la logica de islas siga siendo la de v_input_segmento, porque la vista la repite en
lugar de seleccionar de ella (para que el filtro de fecha entre antes de la window
function).
"""

from __future__ import annotations

import pytest
from tests.conftest import (
    SCHEMA,
    con_inicio_de_turno,
    demasiado_temprano,
    requires_db,
)

pytestmark = requires_db

TZ = "America/Argentina/Buenos_Aires"
IP8 = "172.30.10.8"

@pytest.fixture
def reporting(reporting_db):
    return reporting_db


def seed(db, rows: list[tuple[str, str, bool]]) -> None:
    """rows are (tag, ts expression in SQL, value)."""
    with db.cursor() as cur:
        for tag, when, value in rows:
            cur.execute(
                f"insert into {SCHEMA}.input_status (ip, tag, version, ts, value) "
                f"values (%s, %s, 1, {when}, %s)",
                (IP8, tag, value),
            )


def rows_of(db) -> dict[str, tuple[float, int]]:
    with db.cursor() as cur:
        cur.execute(
            "select puesto_label, round(segundos_hoy, 1), paradas_hoy "
            "from reporting.v_monitor_hoy"
        )
        return {label: (float(secs), paradas) for label, secs, paradas in cur.fetchall()}


MEDIANOCHE = f"(date_trunc('day', now() at time zone '{TZ}') at time zone '{TZ}')"
TURNO = f"({MEDIANOCHE} + interval '6 hours')"


# --- la ventana del turno -------------------------------------------------------------


@demasiado_temprano
def test_a_stop_before_the_shift_starts_is_not_counted(reporting):
    """El caso real del 2026-09-18: un rele quedo apretado de 03:12 a 05:23 y aportaba
    7821 s, el 66% del total del dia, sin ser demora de faena."""
    con_inicio_de_turno(reporting, "06:00")
    seed(
        reporting,
        [
            ("Cont_P1", f"{MEDIANOCHE} + interval '3 hours'", True),
            ("Cont_P1", f"{MEDIANOCHE} + interval '3 hours 1 minute'", True),
            ("Cont_P1", f"{MEDIANOCHE} + interval '3 hours 2 minutes'", False),
        ],
    )
    assert rows_of(reporting)["Puesto 1"] == (0.0, 0)


@demasiado_temprano
def test_a_stop_inside_the_shift_is_counted_whole(reporting):
    con_inicio_de_turno(reporting, "06:00")
    seed(
        reporting,
        [
            ("Cont_P1", f"{TURNO} + interval '1 hour'", True),
            ("Cont_P1", f"{TURNO} + interval '1 hour 1 minute'", True),
            ("Cont_P1", f"{TURNO} + interval '1 hour 1 minute 10 seconds'", False),
        ],
    )
    assert rows_of(reporting)["Puesto 1"] == (70.0, 1)


@demasiado_temprano
def test_a_stop_that_was_already_held_when_the_shift_started_is_left_out(reporting):
    con_inicio_de_turno(reporting, "06:00")
    seed(
        reporting,
        [
            ("Cont_P1", f"{TURNO} - interval '10 minutes'", True),
            ("Cont_P1", f"{TURNO} + interval '10 minutes'", False),
        ],
    )
    assert rows_of(reporting)["Puesto 1"] == (0.0, 0)


@demasiado_temprano
def test_moving_the_shift_start_moves_the_numbers(reporting):
    seed(
        reporting,
        [
            ("Cont_P1", f"{MEDIANOCHE} + interval '4 hours'", True),
            ("Cont_P1", f"{MEDIANOCHE} + interval '4 hours 20 seconds'", False),
            ("Cont_P1", f"{TURNO} + interval '1 hour'", True),
            ("Cont_P1", f"{TURNO} + interval '1 hour 30 seconds'", False),
        ],
    )
    con_inicio_de_turno(reporting, "06:00")
    assert rows_of(reporting)["Puesto 1"] == (30.0, 1)

    con_inicio_de_turno(reporting, "00:00")
    assert rows_of(reporting)["Puesto 1"] == (50.0, 2)


def test_nothing_from_yesterday_leaks_in(reporting):
    con_inicio_de_turno(reporting, "00:00")
    seed(
        reporting,
        [
            ("Cont_P1", f"{MEDIANOCHE} - interval '2 hours'", True),
            ("Cont_P1", f"{MEDIANOCHE} - interval '2 hours' + interval '20 seconds'",
             False),
        ],
    )
    assert rows_of(reporting)["Puesto 1"] == (0.0, 0)


# --- el calculo -----------------------------------------------------------------------


@demasiado_temprano
def test_a_stop_still_held_keeps_growing(reporting):
    con_inicio_de_turno(reporting, "00:00")
    seed(reporting, [("Cont_P1", "now() - interval '45 seconds'", True)])
    segundos, paradas = rows_of(reporting)["Puesto 1"]
    assert paradas == 1
    assert 44.0 <= segundos <= 46.0


@demasiado_temprano
def test_the_cap_keeps_a_data_gap_from_being_billed_as_a_stop(reporting):
    con_inicio_de_turno(reporting, "00:00")
    seed(
        reporting,
        [
            ("Cont_P1", f"{TURNO} + interval '1 hour'", True),
            ("Cont_P1", f"{TURNO} + interval '2 hours'", False),
        ],
    )
    segundos, _ = rows_of(reporting)["Puesto 1"]
    with reporting.cursor() as cur:
        cur.execute("select max_segment_s from reporting.v_parametros")
        cap = float(cur.fetchone()[0])
    assert segundos == pytest.approx(cap, abs=0.1)


@demasiado_temprano
def test_consecutive_segments_of_one_stop_are_one_stop(reporting):
    con_inicio_de_turno(reporting, "00:00")
    seed(
        reporting,
        [("Cont_P1", f"{TURNO} + interval '{n} minutes'", True) for n in range(4)]
        + [("Cont_P1", f"{TURNO} + interval '3 minutes 30 seconds'", False)],
    )
    assert rows_of(reporting)["Puesto 1"][1] == 1


def test_a_puesto_with_no_stops_shows_up_at_zero(reporting):
    con_inicio_de_turno(reporting, "00:00")
    result = rows_of(reporting)
    assert result["Puesto 1"] == (0.0, 0)
    assert result["Puesto 2"] == (0.0, 0)
    assert result["Puesto 3"] == (0.0, 0)


@demasiado_temprano
def test_it_agrees_with_v_input_segmento_over_the_same_stops(reporting):
    """La paridad que habilita duplicar la logica -- seccion 11 de verificacion.sql."""
    con_inicio_de_turno(reporting, "00:00")
    seed(
        reporting,
        [
            ("Cont_P1", f"{TURNO} + interval '1 hour'", True),
            ("Cont_P1", f"{TURNO} + interval '1 hour 1 minute'", True),
            ("Cont_P1", f"{TURNO} + interval '1 hour 1 minute 10 seconds'", False),
            ("Cont_P2", f"{TURNO} + interval '2 hours'", True),
            ("Cont_P2", f"{TURNO} + interval '2 hours 25 seconds'", False),
        ],
    )
    with reporting.cursor() as cur:
        cur.execute(
            f"""
            select round(sum(extract(epoch from duracion))::numeric, 3)
            from reporting.v_input_segmento
            where ts_start_local >= (date_trunc('day', now() at time zone '{TZ}'))
            """
        )
        segmento = float(cur.fetchone()[0])
        cur.execute("select round(sum(segundos_hoy), 3) from reporting.v_monitor_hoy")
        monitor = float(cur.fetchone()[0])
    assert monitor == pytest.approx(segmento, abs=0.001)


def test_the_day_window_goes_through_an_index_and_not_a_full_scan(reporting):
    with reporting.cursor() as cur:
        cur.execute("explain (analyze, timing off) select * from reporting.v_monitor_hoy")
        plan = "\n".join(line for (line,) in cur.fetchall())
    assert "Seq Scan on input_status" not in plan, plan
