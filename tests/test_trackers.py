"""Tests for the state machines that turn PLC readings into events.

Every tracker takes the observation instant as an argument, so time is scripted here --
no sleeps, no clock.
"""

from __future__ import annotations

import datetime as dt

import pytest

from paradas_faena.plc.trackers import (
    CounterTracker,
    HeartbeatTracker,
    InputBinding,
    InputTracker,
    NoriaStatusTracker,
    SpeedTracker,
)

IP = "172.30.10.8"
T0 = dt.datetime(2026, 9, 7, 6, 0, 0, tzinfo=dt.UTC)


def at(seconds: float) -> dt.datetime:
    return T0 + dt.timedelta(seconds=seconds)


# --------------------------------------------------------------------------------------
# CounterTracker
# --------------------------------------------------------------------------------------


def test_first_reading_seeds_without_emitting():
    tracker = CounterTracker(IP, {"a": 1})
    assert tracker.observe({"a": 7}, at(0), True, 42.0) == []


def test_counter_sitting_at_zero_then_incrementing_emits_once():
    """Zero must mean "reads zero", not "not yet seeded"."""
    tracker = CounterTracker(IP, {"a": 1})

    assert tracker.observe({"a": 0}, at(0), True, 42.0) == []   # seed
    assert tracker.observe({"a": 0}, at(1), True, 42.0) == []   # still zero, no event

    events = tracker.observe({"a": 1}, at(2), True, 42.0)
    assert len(events) == 1
    assert (events[0].old_value, events[0].new_value, events[0].dif) == (0, 1, 1)


def test_unchanged_counter_emits_nothing():
    tracker = CounterTracker(IP, {"a": 1})
    tracker.observe({"a": 5}, at(0), True, 42.0)
    assert tracker.observe({"a": 5}, at(1), True, 42.0) == []


def test_increment_carries_line_state_from_the_same_reading():
    tracker = CounterTracker(IP, {"a": 1})
    tracker.observe({"a": 5}, at(0), True, 42.0)
    (event,) = tracker.observe({"a": 8}, at(1), False, 0.0)
    assert event.dif == 3
    assert event.noria_running is False
    assert event.vel == 0.0
    assert event.ts == at(1)


def test_a_jump_of_more_than_one_is_reported_as_one_event():
    """Two stops between polls is one row with dif=2."""
    tracker = CounterTracker(IP, {"a": 1})
    tracker.observe({"a": 5}, at(0), True, 42.0)
    (event,) = tracker.observe({"a": 7}, at(1), True, 42.0)
    assert event.dif == 2


def test_counter_going_backwards_emits_nothing_and_reseeds():
    """A PLC-side reset or a DINT wrap is not a negative number of stops."""
    tracker = CounterTracker(IP, {"a": 1})
    tracker.observe({"a": 100}, at(0), True, 42.0)

    assert tracker.observe({"a": 0}, at(1), True, 42.0) == []

    (event,) = tracker.observe({"a": 1}, at(2), True, 42.0)
    assert (event.old_value, event.new_value, event.dif) == (0, 1, 1)


def test_reset_reseeds_so_a_gap_is_not_reported_as_a_burst():
    """A delta accumulated across a disconnection is not a stop count."""
    tracker = CounterTracker(IP, {"a": 1})
    tracker.observe({"a": 10}, at(0), True, 42.0)

    tracker.reset()
    assert tracker.observe({"a": 400}, at(3600), True, 42.0) == []

    (event,) = tracker.observe({"a": 401}, at(3601), True, 42.0)
    assert event.dif == 1


def test_counters_are_tracked_independently():
    tracker = CounterTracker(IP, {"a": 1, "b": 1})
    tracker.observe({"a": 1, "b": 1}, at(0), True, 42.0)
    events = tracker.observe({"a": 2, "b": 1}, at(1), True, 42.0)
    assert [e.tag for e in events] == ["a"]


# --------------------------------------------------------------------------------------
# InputTracker
# --------------------------------------------------------------------------------------

REASSERT = 60.0

# {input tag on the PLC: (counter tag of the puesto, config version)}
_DEFAULT_MAP = {
    "_IO_EM_DI_00": InputBinding("p1", 1),
    "_IO_EM_DI_02": InputBinding("p2", 1),
}


def make_inputs(mapping=None, reassert=REASSERT) -> InputTracker:
    return InputTracker(IP, _DEFAULT_MAP if mapping is None else mapping, reassert)


def reading(**stops) -> dict[str, bool]:
    """A PLC read dict built from logical stop states.

    The relays are normally closed, so a puesto that is *not* asking for a stop reads 1
    on the wire. `reading()` is everyone clear; `reading(_IO_EM_DI_00=True)` is that
    input holding the line.
    """
    raw = {"_IO_EM_DI_00": True, "_IO_EM_DI_02": True}
    for input_tag, stopping in stops.items():
        raw[input_tag] = not stopping
    return raw


def test_a_closed_relay_is_not_a_stop_and_an_open_one_is():
    """The inversion, stated directly in wiring terms: 1 = clear, 0 = pidiendo parada."""
    tracker = make_inputs()
    events = {
        e.tag: e.value
        for e in tracker.observe({"_IO_EM_DI_00": True, "_IO_EM_DI_02": False}, at(0))
    }
    assert events == {"p1": False, "p2": True}


def test_the_tracker_reports_which_tags_it_needs_read():
    assert make_inputs().input_tags == ("_IO_EM_DI_00", "_IO_EM_DI_02")
    assert make_inputs({}).input_tags == ()


def test_events_carry_the_counter_tag_not_the_input_tag():
    """input_status has a foreign key on counters_name (ip, tag)."""
    events = make_inputs().observe(reading(_IO_EM_DI_00=True), at(0))
    assert {e.tag for e in events} == {"p1", "p2"}


def test_first_observation_resyncs_every_mapped_input():
    """A resync row anchors the timeline; without one a query cannot know the state an
    input was in at the start of the window."""
    tracker = make_inputs()
    events = tracker.observe(reading(_IO_EM_DI_00=True), at(0))
    assert {(e.tag, e.value, e.reason) for e in events} == {
        ("p1", True, "resync"),
        ("p2", False, "resync"),
    }


def test_steady_state_emits_nothing():
    tracker = make_inputs()
    tracker.observe(reading(), at(0))
    assert tracker.observe(reading(), at(0.5)) == []


def test_flip_emits_a_change_row_for_only_that_input():
    tracker = make_inputs()
    tracker.observe(reading(), at(0))
    events = tracker.observe(reading(_IO_EM_DI_00=True), at(0.5))
    assert len(events) == 1
    assert (events[0].tag, events[0].value, events[0].reason) == ("p1", True, "change")


def test_held_input_is_reasserted_on_the_interval_and_not_before():
    """A live `true` segment is never longer than REASSERT_SECONDS, which is what lets
    the duration query cap anything longer as a data gap."""
    tracker = make_inputs()
    tracker.observe(reading(), at(0))
    tracker.observe(reading(_IO_EM_DI_00=True), at(1))

    assert tracker.observe(reading(_IO_EM_DI_00=True), at(30)) == []
    assert tracker.observe(reading(_IO_EM_DI_00=True), at(60.9)) == []

    (event,) = tracker.observe(reading(_IO_EM_DI_00=True), at(61))
    assert (event.reason, event.value) == ("reassert", True)

    # The clock restarts from the row just written, not from the original change.
    assert tracker.observe(reading(_IO_EM_DI_00=True), at(100)) == []
    (event,) = tracker.observe(reading(_IO_EM_DI_00=True), at(121))
    assert event.reason == "reassert"


def test_input_reading_false_is_never_reasserted():
    """It contributes nothing to the duration sum."""
    tracker = make_inputs()
    tracker.observe(reading(), at(0))
    for t in (30, 61, 120, 601):
        assert tracker.observe(reading(), at(t)) == []


def test_release_after_a_reassert_still_emits_the_change():
    tracker = make_inputs()
    tracker.observe(reading(_IO_EM_DI_00=True), at(0))
    tracker.observe(reading(_IO_EM_DI_00=True), at(61))
    (event,) = tracker.observe(reading(), at(70))
    assert (event.reason, event.value) == ("change", False)


def test_reset_restates_everything_so_a_gap_has_an_anchor_on_both_sides():
    tracker = make_inputs()
    tracker.observe(reading(_IO_EM_DI_00=True), at(0))

    tracker.reset()
    events = tracker.observe(reading(_IO_EM_DI_00=True), at(4000))
    assert {(e.tag, e.value, e.reason) for e in events} == {
        ("p1", True, "resync"),
        ("p2", False, "resync"),
    }


def test_unmapped_tags_in_the_reading_are_ignored():
    """The same read carries the counters and the variador tags."""
    tracker = make_inputs({"_IO_EM_DI_00": InputBinding("p1", 1)})
    events = tracker.observe(
        {"_IO_EM_DI_00": True, "_IO_EM_DI_01": False, "Cont_P1": 5, "frec": 10.0},
        at(0),
    )
    assert [(e.tag, e.value) for e in events] == [("p1", False)]


def test_a_missing_input_is_survived_rather_than_crashing():
    """A PLC program change that renames an input must not take the reader down."""
    tracker = make_inputs(
        {"_IO_EM_DI_00": InputBinding("p1", 1), "_NO_EXISTE": InputBinding("p9", 1)}
    )
    events = tracker.observe({"_IO_EM_DI_00": False}, at(0))
    assert [(e.tag, e.value) for e in events] == [("p1", True)]


def test_a_missing_input_is_reported_once_not_every_tick():
    tracker = make_inputs({"_NO_EXISTE": InputBinding("p9", 1)})
    for t in range(5):
        tracker.observe({}, at(t))
    # Nothing to emit, and the tracker keeps working for the tags that do arrive.
    assert tracker.observe({}, at(9)) == []


def test_no_mapped_inputs_emits_nothing():
    """Lets the daemon be deployed before the inputs are mapped in the plant."""
    tracker = make_inputs({})
    assert tracker.observe(reading(_IO_EM_DI_00=True), at(0)) == []


# --------------------------------------------------------------------------------------
# NoriaStatusTracker
# --------------------------------------------------------------------------------------


def test_status_emits_first_reading_then_only_transitions():
    tracker = NoriaStatusTracker(IP)
    (event,) = tracker.observe(True, at(0))
    assert event.running is True

    assert tracker.observe(True, at(1)) == []

    (event,) = tracker.observe(False, at(2))
    assert event.running is False


def test_status_reset_re_anchors_after_a_reconnect():
    tracker = NoriaStatusTracker(IP)
    tracker.observe(True, at(0))
    tracker.reset()
    (event,) = tracker.observe(True, at(500))
    assert event.running is True


# --------------------------------------------------------------------------------------
# SpeedTracker
# --------------------------------------------------------------------------------------


def make_speed(deadband=0.2, max_interval=60.0, conv=4.23) -> SpeedTracker:
    return SpeedTracker(IP, conv, deadband, max_interval)


def test_speed_converts_frequency_to_line_velocity():
    tracker = make_speed(conv=4.23)
    (event,) = tracker.observe(10.0, at(0), True)
    assert event.frec == 10.0
    assert event.vel == pytest.approx(42.3)


def test_speed_within_the_deadband_is_not_persisted():
    tracker = make_speed(deadband=0.2)
    tracker.observe(10.0, at(0), True)
    assert tracker.observe(10.1, at(0.5), True) == []
    assert tracker.observe(9.9, at(1.0), True) == []


def test_speed_beyond_the_deadband_is_persisted():
    tracker = make_speed(deadband=0.2)
    tracker.observe(10.0, at(0), True)
    (event,) = tracker.observe(10.2, at(0.5), True)
    assert event.frec == pytest.approx(10.2)


def test_deadband_is_measured_against_the_last_persisted_value_not_the_last_reading():
    """Otherwise a slow ramp of sub-deadband steps is never recorded at all."""
    tracker = make_speed(deadband=0.5, max_interval=1e9)
    tracker.observe(10.0, at(0), True)
    assert tracker.observe(10.2, at(1), True) == []
    assert tracker.observe(10.4, at(2), True) == []
    assert tracker.observe(10.5, at(3), True) != []


def test_a_steady_line_still_leaves_a_trace_on_the_max_interval():
    tracker = make_speed(deadband=0.2, max_interval=60.0)
    tracker.observe(10.0, at(0), True)
    assert tracker.observe(10.0, at(59), True) == []
    assert tracker.observe(10.0, at(60), True) != []


# --------------------------------------------------------------------------------------
# HeartbeatTracker
# --------------------------------------------------------------------------------------


def test_heartbeat_emits_on_the_interval():
    """Missing heartbeats are what distinguish a quiet period from an outage."""
    tracker = HeartbeatTracker(IP, 60.0)
    assert len(tracker.observe(at(0))) == 1
    assert tracker.observe(at(30)) == []
    assert len(tracker.observe(at(60))) == 1
    assert tracker.observe(at(90)) == []
    assert len(tracker.observe(at(120))) == 1
