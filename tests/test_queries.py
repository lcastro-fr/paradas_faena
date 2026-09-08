"""Tests for the reporting SQL, against real PostgreSQL.

The duration arithmetic is ours now, so it has to be pinned down: the two window clamps,
the gap cap, and the guarantee that re-assert rows do not double-count.
"""

from __future__ import annotations

import datetime as dt

import pytest
from tests.conftest import MAX_SEGMENT, SCHEMA, requires_db, ts

pytestmark = requires_db

IP8 = "172.30.10.8"
IP9 = "172.30.10.9"


def put(db, ip: str, tag: str, rows: list[tuple[dt.datetime, bool]]) -> None:
    with db.cursor() as cur:
        cur.executemany(
            f"insert into {SCHEMA}.input_status (ip, tag, ts, value) values (%s,%s,%s,%s)",
            [(ip, tag, when, value) for when, value in rows],
        )


def put_noria(db, rows: list[tuple[dt.datetime, bool]], ip: str = IP9) -> None:
    with db.cursor() as cur:
        cur.executemany(
            f"insert into {SCHEMA}.noria_status (ip, ts, running) values (%s,%s,%s)",
            [(ip, when, running) for when, running in rows],
        )


def totals(db, sql: str, desde, hasta, max_segment=MAX_SEGMENT) -> dict[str, float]:
    """Run a duration query and return {puesto: seconds}."""
    with db.cursor() as cur:
        cur.execute(sql, {"desde": desde, "hasta": hasta, "max_segment": max_segment})
        return {row[0]: row[1].total_seconds() for row in cur.fetchall()}


# --------------------------------------------------------------------------------------
# tiempo_parada_por_puesto
# --------------------------------------------------------------------------------------


def test_a_single_stop_is_its_own_duration(db, query):
    put(db, IP8, "Cont_P1", [(ts(10, 0, 0), True), (ts(10, 0, 30), False)])
    result = totals(db, query("tiempo_parada_por_puesto"), ts(6), ts(18))
    assert result == {"Puesto 1": 30.0}


def test_repeated_stops_are_summed(db, query):
    put(db, IP8, "Cont_P1", [
        (ts(10, 0, 0), True), (ts(10, 0, 30), False),
        (ts(11, 0, 0), True), (ts(11, 0, 45), False),
    ])
    assert totals(db, query("tiempo_parada_por_puesto"), ts(6), ts(18)) == {
        "Puesto 1": 75.0
    }


def test_workstations_are_independent(db, query):
    put(db, IP8, "Cont_P1", [(ts(10), True), (ts(10, 0, 10), False)])
    put(db, IP8, "Cont_P2", [(ts(10), True), (ts(10, 0, 25), False)])
    put(db, IP9, "Cont_P3", [(ts(10), False)])
    assert totals(db, query("tiempo_parada_por_puesto"), ts(6), ts(18)) == {
        "Puesto 1": 10.0,
        "Puesto 2": 25.0,
    }


def test_overlapping_stops_are_each_counted_in_full(db, query):
    """Two workstations holding the line at once each own their whole hold; the sum can
    legitimately exceed the wall-clock time the line was down."""
    put(db, IP8, "Cont_P1", [(ts(10, 0, 0), True), (ts(10, 0, 40), False)])
    put(db, IP8, "Cont_P2", [(ts(10, 0, 20), True), (ts(10, 0, 50), False)])
    assert totals(db, query("tiempo_parada_por_puesto"), ts(6), ts(18)) == {
        "Puesto 1": 40.0,
        "Puesto 2": 30.0,
    }


def test_relays_that_never_close_contribute_nothing(db, query):
    put(db, IP8, "Cont_P1", [(ts(10), False), (ts(11), False), (ts(12), False)])
    assert totals(db, query("tiempo_parada_por_puesto"), ts(6), ts(18)) == {}


def test_reassert_rows_do_not_double_count(db, query):
    """A three-minute stop written as a change plus two re-asserts must total 180 s, not
    three overlapping segments."""
    put(db, IP8, "Cont_P1", [
        (ts(10, 0, 0), True),      # change
        (ts(10, 1, 0), True),      # reassert
        (ts(10, 2, 0), True),      # reassert
        (ts(10, 3, 0), False),     # release
    ])
    assert totals(db, query("tiempo_parada_por_puesto"), ts(6), ts(18)) == {
        "Puesto 1": 180.0
    }


def test_a_stop_open_at_the_window_start_is_counted_only_from_the_start(db, query):
    """The relay was already held before the window opened. Charging the whole hold to
    this window would inflate it; ignoring the row entirely would lose real time."""
    put(db, IP8, "Cont_P1", [(ts(9, 59, 0), True), (ts(10, 0, 20), False)])
    assert totals(db, query("tiempo_parada_por_puesto"), ts(10), ts(18)) == {
        "Puesto 1": 20.0
    }


def test_a_stop_open_at_the_window_end_is_counted_only_up_to_the_end(db, query):
    put(db, IP8, "Cont_P1", [(ts(10, 59, 50), True), (ts(11, 0, 30), False)])
    assert totals(db, query("tiempo_parada_por_puesto"), ts(6), ts(11)) == {
        "Puesto 1": 10.0
    }


def test_a_stop_spanning_the_whole_window_is_capped_by_the_window(db, query):
    """Held throughout, with re-asserts every 60 s as the daemon would write them."""
    rows = [(ts(9, 30, 0), True)]
    rows += [(ts(9, 30, 0) + dt.timedelta(seconds=60 * n), True) for n in range(1, 90)]
    put(db, IP8, "Cont_P1", rows)
    result = totals(db, query("tiempo_parada_por_puesto"), ts(10), ts(10, 30))
    assert result["Puesto 1"] == pytest.approx(1800.0, abs=1.0)


def test_a_still_open_stop_is_counted_to_the_window_end(db, query):
    put(db, IP8, "Cont_P1", [(ts(17, 59, 30), True)])
    assert totals(db, query("tiempo_parada_por_puesto"), ts(6), ts(18)) == {
        "Puesto 1": 30.0
    }


def test_a_data_gap_is_capped_instead_of_charged_in_full(db, query):
    """The load-bearing test for the whole design.

    The relay was held at 10:00, the daemon died, and it came back at 14:00 to find the
    relay released. Uncapped that reads as a four-hour stop.
    """
    put(db, IP8, "Cont_P1", [(ts(10, 0, 0), True), (ts(14, 0, 0), False)])
    result = totals(db, query("tiempo_parada_por_puesto"), ts(6), ts(18))
    assert result["Puesto 1"] == pytest.approx(MAX_SEGMENT.total_seconds())
    assert result["Puesto 1"] < 70.0          # not 14400


def test_a_real_long_stop_is_not_truncated_by_the_cap(db, query):
    """A long stop is a chain of 60 s re-asserts, each under the cap, so the cap only
    ever bites on missing data."""
    rows = [(ts(10, 0, 0) + dt.timedelta(seconds=60 * n), True) for n in range(30)]
    rows.append((ts(10, 30, 0), False))
    put(db, IP8, "Cont_P1", rows)
    assert totals(db, query("tiempo_parada_por_puesto"), ts(6), ts(18)) == {
        "Puesto 1": 1800.0
    }


def test_an_empty_window_returns_no_rows(db, query):
    put(db, IP8, "Cont_P1", [(ts(10), True), (ts(10, 0, 30), False)])
    assert totals(db, query("tiempo_parada_por_puesto"), ts(12), ts(13)) == {}


def test_segment_count_is_reported(db, query):
    put(db, IP8, "Cont_P1", [
        (ts(10, 0, 0), True), (ts(10, 1, 0), True), (ts(10, 1, 30), False),
    ])
    with db.cursor() as cur:
        cur.execute(query("tiempo_parada_por_puesto"),
                    {"desde": ts(6), "hasta": ts(18), "max_segment": MAX_SEGMENT})
        (name, tiempo, segmentos) = cur.fetchone()
    assert (name, tiempo.total_seconds(), segmentos) == ("Puesto 1", 90.0, 2)


# --------------------------------------------------------------------------------------
# tiempo_parada_noria_en_marcha
# --------------------------------------------------------------------------------------


def test_time_held_while_the_line_was_stopped_is_not_charged(db, query):
    """Line runs 10:00:00-10:00:20; the relay is held 10:00:00-10:00:40."""
    put(db, IP8, "Cont_P1", [(ts(10, 0, 0), True), (ts(10, 0, 40), False)])
    put_noria(db, [(ts(9, 0, 0), True), (ts(10, 0, 20), False)])

    plain = totals(db, query("tiempo_parada_por_puesto"), ts(6), ts(18))
    running_only = totals(db, query("tiempo_parada_noria_en_marcha"), ts(6), ts(18))

    assert plain == {"Puesto 1": 40.0}
    assert running_only == {"Puesto 1": 20.0}


def test_a_hold_entirely_while_stopped_counts_as_zero(db, query):
    put(db, IP8, "Cont_P1", [(ts(11, 0, 0), True), (ts(11, 0, 30), False)])
    put_noria(db, [(ts(9, 0, 0), False)])
    assert totals(db, query("tiempo_parada_noria_en_marcha"), ts(6), ts(18)) == {}


def test_a_hold_entirely_while_running_matches_the_plain_total(db, query):
    put(db, IP8, "Cont_P1", [(ts(11, 0, 0), True), (ts(11, 0, 30), False)])
    put_noria(db, [(ts(9, 0, 0), True)])
    assert totals(db, query("tiempo_parada_noria_en_marcha"), ts(6), ts(18)) == {
        "Puesto 1": 30.0
    }


# --------------------------------------------------------------------------------------
# paradas_individuales
# --------------------------------------------------------------------------------------


def individuales(db, query, desde, hasta):
    with db.cursor() as cur:
        cur.execute(query("paradas_individuales"),
                    {"desde": desde, "hasta": hasta, "max_segment": MAX_SEGMENT})
        return [(r[0], r[1], r[2], r[3].total_seconds()) for r in cur.fetchall()]


def test_each_stop_is_one_row_with_its_own_duration(db, query):
    put(db, IP8, "Cont_P1", [
        (ts(10, 0, 0), True), (ts(10, 0, 30), False),
        (ts(11, 0, 0), True), (ts(11, 0, 45), False),
    ])
    rows = individuales(db, query, ts(6), ts(18))
    assert [(r[0], r[3]) for r in rows] == [("Puesto 1", 30.0), ("Puesto 1", 45.0)]
    assert rows[0][1] == ts(10, 0, 0)
    assert rows[0][2] == ts(10, 0, 30)


def test_reasserts_are_collapsed_into_the_stop_they_belong_to(db, query):
    """A three-minute stop is one stop, not four rows."""
    put(db, IP8, "Cont_P1", [
        (ts(10, 0, 0), True), (ts(10, 1, 0), True), (ts(10, 2, 0), True),
        (ts(10, 3, 0), False),
    ])
    rows = individuales(db, query, ts(6), ts(18))
    assert len(rows) == 1
    assert rows[0][3] == 180.0


def test_stops_from_different_workstations_are_not_merged(db, query):
    put(db, IP8, "Cont_P1", [(ts(10, 0, 0), True), (ts(10, 0, 30), False)])
    put(db, IP8, "Cont_P2", [(ts(10, 0, 10), True), (ts(10, 0, 20), False)])
    rows = individuales(db, query, ts(6), ts(18))
    assert sorted((r[0], r[3]) for r in rows) == [("Puesto 1", 30.0), ("Puesto 2", 10.0)]


def test_a_stop_already_in_progress_at_the_window_start_is_not_listed(db, query):
    """paradas_individuales attributes a stop to the window it started in; the plain
    total query is the one that counts every second inside the window."""
    put(db, IP8, "Cont_P1", [(ts(9, 30, 0), True), (ts(10, 0, 30), False)])
    assert individuales(db, query, ts(10), ts(18)) == []
    assert totals(db, query("tiempo_parada_por_puesto"), ts(10), ts(18)) == {
        "Puesto 1": 30.0
    }


# --------------------------------------------------------------------------------------
# cobertura_datos
# --------------------------------------------------------------------------------------


def cobertura(db, query, desde, hasta, heartbeat=dt.timedelta(seconds=60)):
    with db.cursor() as cur:
        cur.execute(query("cobertura_datos"),
                    {"desde": desde, "hasta": hasta, "heartbeat": heartbeat})
        return {row[0]: row for row in cur.fetchall()}


def test_full_coverage_reports_one(db, query):
    """Sixty beats a minute apart over the hour the window covers."""
    with db.cursor() as cur:
        cur.executemany(
            f"insert into {SCHEMA}.plc_heartbeat (ip, ts) values (%s, %s)",
            [(IP8, ts(10) + dt.timedelta(seconds=60 * n)) for n in range(60)],
        )
    result = cobertura(db, query, ts(10), ts(11))
    assert float(result[IP8][3]) == pytest.approx(1.0)
    assert result[IP8][2] == 60


def test_a_gap_shows_up_as_partial_coverage(db, query):
    """Distinguishes "no stops happened" from "we were not looking"."""
    with db.cursor() as cur:
        cur.executemany(
            f"insert into {SCHEMA}.plc_heartbeat (ip, ts) values (%s, %s)",
            [(IP8, ts(10) + dt.timedelta(seconds=60 * n)) for n in range(15)],
        )
    result = cobertura(db, query, ts(10), ts(11))
    assert float(result[IP8][3]) == pytest.approx(0.25)


def test_a_plc_that_never_reported_is_still_listed_with_zero(db, query):
    """A left join, so an unreachable PLC is visible rather than absent."""
    result = cobertura(db, query, ts(10), ts(11))
    assert set(result) == {IP8, IP9}
    assert result[IP8][2] == 0
    assert result[IP8][4] is None            # primer_latido
