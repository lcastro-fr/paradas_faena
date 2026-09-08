"""Tests for the shared line state, whose readings expire."""

from __future__ import annotations

import datetime as dt

from paradas_faena.state import LineState

T0 = dt.datetime(2026, 9, 7, 6, 0, 0, tzinfo=dt.UTC)


def at(seconds: float) -> dt.datetime:
    return T0 + dt.timedelta(seconds=seconds)


def test_unset_state_reads_as_unknown():
    assert LineState(30.0).snapshot(at(0)) == (None, None)


def test_fresh_readings_are_returned():
    state = LineState(30.0)
    state.set_running(True, at(0))
    state.set_velocity(42.3, at(0))
    assert state.snapshot(at(1)) == (True, 42.3)


def test_running_false_is_preserved_and_not_confused_with_unknown():
    state = LineState(30.0)
    state.set_running(False, at(0))
    running, _ = state.snapshot(at(1))
    assert running is False


def test_stale_readings_read_back_as_unknown():
    """A variador PLC that went away must not keep stamping stops with its last value."""
    state = LineState(30.0)
    state.set_running(True, at(0))
    state.set_velocity(42.3, at(0))

    assert state.snapshot(at(30)) == (True, 42.3)
    assert state.snapshot(at(31)) == (None, None)


def test_the_two_readings_age_independently():
    state = LineState(30.0)
    state.set_running(True, at(0))
    state.set_velocity(42.3, at(25))
    assert state.snapshot(at(40)) == (None, 42.3)


def test_invalidate_drops_everything_immediately():
    state = LineState(3600.0)
    state.set_running(True, at(0))
    state.set_velocity(42.3, at(0))
    state.invalidate()
    assert state.snapshot(at(1)) == (None, None)
