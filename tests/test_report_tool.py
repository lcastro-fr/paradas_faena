"""Tests for the reporting CLI.

The point of the tool is that max_segment comes from the same config the daemon uses,
so it cannot drift from REASSERT_SECONDS.
"""

from __future__ import annotations

import datetime as dt

import pytest

from paradas_faena.tools import report


def test_it_finds_the_query_files():
    names = report.available()
    assert "tiempo_parada_por_puesto" in names
    assert "cobertura_datos" in names


def test_only_the_parameters_a_query_asks_for_are_supplied(config):
    desde, hasta = dt.datetime(2026, 9, 7, 6), dt.datetime(2026, 9, 7, 18)

    duration_sql = (report.QUERIES_DIR / "tiempo_parada_por_puesto.sql").read_text()
    assert set(report.parameters(duration_sql, config, desde, hasta)) == {
        "desde", "hasta", "max_segment",
    }

    coverage_sql = (report.QUERIES_DIR / "cobertura_datos.sql").read_text()
    assert set(report.parameters(coverage_sql, config, desde, hasta)) == {
        "desde", "hasta", "heartbeat",
    }


def test_max_segment_comes_from_the_config_not_a_literal(config):
    """The coupling that must never be hardcoded: change REASSERT_SECONDS and the cap
    the query receives follows."""
    import dataclasses

    sql = (report.QUERIES_DIR / "tiempo_parada_por_puesto.sql").read_text()
    desde, hasta = dt.datetime(2026, 9, 7, 6), dt.datetime(2026, 9, 7, 18)

    at_60 = report.parameters(sql, config, desde, hasta)["max_segment"]
    at_30 = report.parameters(
        sql, dataclasses.replace(config, reassert_seconds=30.0), desde, hasta
    )["max_segment"]

    assert at_60 == config.max_segment()
    assert at_30 < at_60
    assert at_30 > dt.timedelta(seconds=30)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("06:00", dt.datetime(2026, 9, 7, 6, 0)),
        ("18:30:15", dt.datetime(2026, 9, 7, 18, 30, 15)),
        ("2026-09-08T04:00", dt.datetime(2026, 9, 8, 4, 0)),
    ],
)
def test_moments_parse_as_clock_times_or_full_timestamps(text, expected):
    got = report._moment(text, dt.date(2026, 9, 7))
    assert got.replace(tzinfo=None) == expected
    assert got.tzinfo is not None, "debe quedar con zona horaria"


def test_an_unparseable_moment_is_a_clean_error():
    with pytest.raises(SystemExit, match="no se entiende"):
        report._moment("manana", dt.date(2026, 9, 7))


def test_no_query_named_lists_them(capsys):
    assert report.main([]) == 0
    assert "tiempo_parada_por_puesto" in capsys.readouterr().out


def test_an_unknown_query_is_rejected(capsys):
    assert report.main(["no_existe"]) == 2
    assert "Disponibles" in capsys.readouterr().err


def test_a_backwards_window_is_rejected(capsys):
    assert report.main(["cobertura_datos", "--desde", "18:00", "--hasta", "06:00"]) == 2
    assert "al revés" in capsys.readouterr().err
