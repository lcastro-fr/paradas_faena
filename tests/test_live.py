"""Tests for the ephemeral mirror the live monitor reads.

Against real Redis: what is being verified is which keys exist, what they hold and which
ones expire, and none of that is worth checking against a mock. The failure path gets as
much attention as the happy one -- the whole point of this module is that a Redis that
stops answering must not cost the poll loop a single tick.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
from tests.conftest import requires_redis, ts

from paradas_faena.events import InputEdge, LiveSpeed
from paradas_faena.live import (
    LiveMirror,
    LiveSpeedTracker,
    build_live_client,
    puesto_field,
)

pytestmark = requires_redis

IP8 = "172.30.10.8"
IP9 = "172.30.10.9"


def edge(value: bool, at: dt.datetime, *, reason: str = "change", tag: str = "Cont_P1"):
    return InputEdge(ip=IP8, tag=tag, version=1, ts=at, value=value, reason=reason)


def speed(at: dt.datetime, *, running: bool | None = True, frec: float = 49.8):
    return LiveSpeed(ip=IP9, ts=at, frec=frec, vel=frec * 4.23, noria_running=running)


def stored_puesto(redis_client, config, tag: str = "Cont_P1") -> dict:
    raw = redis_client.hget(config.live.puestos_key, puesto_field(IP8, tag))
    return json.loads(raw)


def stored_noria(redis_client, config) -> dict:
    return json.loads(redis_client.get(config.live.noria_key))


# --- what a tick publishes ------------------------------------------------------------


def test_a_tick_publishes_the_line_and_the_liveness_key(mirror, redis_client, config):
    mirror.tick(IP9, ts(10, 0, 0), line=speed(ts(10, 0, 0)))

    noria = stored_noria(redis_client, config)
    assert noria["running"] is True
    assert noria["frec"] == 49.8
    assert noria["vel"] == pytest.approx(49.8 * 4.23)
    assert json.loads(redis_client.get(config.live.plc_key(IP9)))["up"] is True


def test_the_snapshot_reflects_every_relay_without_waiting_for_an_edge(
    mirror, redis_client, config
):
    mirror.tick(
        IP8,
        ts(10, 0, 0),
        input_edges=[
            edge(True, ts(10, 0, 0), tag="Cont_P1"),
            edge(False, ts(10, 0, 0), tag="Cont_P2"),
        ],
    )
    assert redis_client.hlen(config.live.puestos_key) == 2
    assert stored_puesto(redis_client, config, "Cont_P1")["value"] is True
    assert stored_puesto(redis_client, config, "Cont_P2")["value"] is False


def test_an_unknown_running_state_is_mirrored_as_null_and_never_as_false(
    mirror, redis_client, config
):
    mirror.tick(IP9, ts(10, 0, 0), line=speed(ts(10, 0, 0), running=None))
    assert stored_noria(redis_client, config)["running"] is None


def test_a_puesto_carries_its_version_in_the_payload_not_in_the_field(
    mirror, redis_client, config
):
    mirror.tick(IP8, ts(10, 0, 0), input_edges=[edge(True, ts(10, 0, 0))])
    assert redis_client.hkeys(config.live.puestos_key) == [f"{IP8}|Cont_P1"]
    assert stored_puesto(redis_client, config)["version"] == 1


# --- since: where the current stop started --------------------------------------------


def test_a_flip_starts_the_clock_and_is_exact(mirror, redis_client, config):
    mirror.tick(IP8, ts(10, 0, 0), input_edges=[edge(True, ts(10, 0, 0))])
    payload = stored_puesto(redis_client, config)
    assert payload["since"] == ts(10, 0, 0).isoformat()
    assert payload["exact"] is True


def test_a_reassert_does_not_restart_since(mirror, redis_client, config):
    mirror.tick(IP8, ts(10, 0, 0), input_edges=[edge(True, ts(10, 0, 0))])
    mirror.tick(
        IP8, ts(10, 1, 0), input_edges=[edge(True, ts(10, 1, 0), reason="reassert")]
    )
    assert stored_puesto(redis_client, config)["since"] == ts(10, 0, 0).isoformat()


def test_since_survives_a_resync_with_the_same_value(redis_client, config):
    first = LiveMirror(redis_client, config.live)
    first.tick(IP8, ts(10, 0, 0), input_edges=[edge(True, ts(10, 0, 0))])

    restarted = LiveMirror(redis_client, config.live)
    restarted.seed([(IP8, "Cont_P1")])
    restarted.tick(
        IP8, ts(10, 5, 0), input_edges=[edge(True, ts(10, 5, 0), reason="resync")]
    )

    payload = stored_puesto(redis_client, config)
    assert payload["since"] == ts(10, 0, 0).isoformat()
    assert payload["exact"] is True


def test_a_resync_with_a_different_value_marks_it_inexact(redis_client, config):
    first = LiveMirror(redis_client, config.live)
    first.tick(IP8, ts(10, 0, 0), input_edges=[edge(False, ts(10, 0, 0))])

    restarted = LiveMirror(redis_client, config.live)
    restarted.seed([(IP8, "Cont_P1")])
    restarted.tick(
        IP8, ts(10, 5, 0), input_edges=[edge(True, ts(10, 5, 0), reason="resync")]
    )

    payload = stored_puesto(redis_client, config)
    assert payload["since"] == ts(10, 5, 0).isoformat()
    assert payload["exact"] is False


def test_a_resync_with_nothing_mirrored_is_inexact(mirror, redis_client, config):
    mirror.tick(
        IP8, ts(10, 0, 0), input_edges=[edge(True, ts(10, 0, 0), reason="resync")]
    )
    assert stored_puesto(redis_client, config)["exact"] is False


def test_seed_prunes_puestos_that_are_no_longer_configured(redis_client, config):
    first = LiveMirror(redis_client, config.live)
    first.tick(
        IP8,
        ts(10, 0, 0),
        input_edges=[
            edge(True, ts(10, 0, 0), tag="Cont_P1"),
            edge(True, ts(10, 0, 0), tag="Cont_VIEJO"),
        ],
    )

    LiveMirror(redis_client, config.live).seed([(IP8, "Cont_P1")])
    assert redis_client.hkeys(config.live.puestos_key) == [f"{IP8}|Cont_P1"]


# --- liveness -------------------------------------------------------------------------


def test_a_disconnect_marks_the_plc_down_immediately(mirror, redis_client, config):
    mirror.tick(IP9, ts(10, 0, 0), line=speed(ts(10, 0, 0)))
    mirror.plc_down(IP9, ts(10, 0, 1), variador=True)

    assert json.loads(redis_client.get(config.live.plc_key(IP9)))["up"] is False
    assert stored_noria(redis_client, config)["running"] is None


def test_the_liveness_keys_expire_and_the_state_keys_do_not(
    mirror, redis_client, config
):
    """If the daemon is down ten minutes you want the last known state with its
    timestamp, not a blank screen. Expiry is the signal; state is not the signal."""
    mirror.tick(
        IP9,
        ts(10, 0, 0),
        line=speed(ts(10, 0, 0)),
        input_edges=[edge(True, ts(10, 0, 0))],
    )
    mirror.daemon_alive(ts(10, 0, 0))

    assert redis_client.ttl(config.live.plc_key(IP9)) > 0
    assert redis_client.ttl(config.live.daemon_key) > 0
    assert redis_client.ttl(config.live.noria_key) == -1
    assert redis_client.ttl(config.live.puestos_key) == -1


def test_the_daemon_announces_its_own_exit(mirror, redis_client, config):
    mirror.daemon_alive(ts(10, 0, 0))
    mirror.daemon_down()
    assert json.loads(redis_client.get(config.live.daemon_key))["up"] is False


# --- cost and failure -----------------------------------------------------------------


class RecordingPipeline:
    def __init__(self, sink: list) -> None:
        self._sink = sink

    def set(self, *args, **kwargs):
        self._sink.append("set")

    def hset(self, *args, **kwargs):
        self._sink.append("hset")

    def hdel(self, *args, **kwargs):
        self._sink.append("hdel")

    def execute(self):
        self._sink.append("execute")


class RecordingRedis:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def pipeline(self, transaction=True):
        return RecordingPipeline(self.calls)


def test_a_tick_with_no_news_is_a_single_round_trip(config):
    """Two PLCs at one hertz for twelve hours: it has to stay this cheap."""
    fake = RecordingRedis()
    LiveMirror(fake, config.live).tick(IP8, ts(10, 0, 0))
    assert fake.calls == ["set", "execute"]


def test_the_mirror_never_raises_when_redis_is_down(config):
    offline = LiveMirror(build_live_client("redis://127.0.0.1:1/0", 0.05), config.live)
    offline.tick(IP9, ts(10, 0, 0), line=speed(ts(10, 0, 0)))
    offline.plc_down(IP9, ts(10, 0, 1), variador=True)
    offline.daemon_alive(ts(10, 0, 1))
    offline.seed([(IP8, "Cont_P1")])


def test_a_failure_is_not_retried_until_the_breaker_expires(config, monkeypatch):
    client = build_live_client("redis://127.0.0.1:1/0", 0.05)
    attempts = []
    original = client.pipeline

    def counting(*args, **kwargs):
        attempts.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(client, "pipeline", counting)

    offline = LiveMirror(client, config.live)
    for _ in range(20):
        offline.tick(IP8, ts(10, 0, 0))
    assert len(attempts) == 1


def test_an_unserialisable_payload_is_swallowed_like_any_other_failure(config):
    exploding = LiveMirror(RecordingRedis(), config.live)

    def boom(pipe):
        raise TypeError("no serializable")

    exploding._flush(boom)


# --- the live speed throttle ----------------------------------------------------------


def test_live_speed_is_emitted_without_a_deadband(config):
    tracker = LiveSpeedTracker(IP9, 4.23, interval_seconds=1.0)
    assert len(tracker.observe(49.8, ts(10, 0, 0), True)) == 1
    assert len(tracker.observe(49.81, ts(10, 0, 1), True)) == 1


def test_live_speed_is_throttled_to_the_interval(config):
    tracker = LiveSpeedTracker(IP9, 4.23, interval_seconds=1.0)
    tracker.observe(49.8, ts(10, 0, 0), True)
    assert tracker.observe(49.8, ts(10, 0, 0), True) == []


def test_a_change_of_running_state_bypasses_the_throttle(config):
    tracker = LiveSpeedTracker(IP9, 4.23, interval_seconds=60.0)
    tracker.observe(49.8, ts(10, 0, 0), True)
    emitted = tracker.observe(0.0, ts(10, 0, 0), False)
    assert len(emitted) == 1
    assert emitted[0].noria_running is False


def test_live_speed_carries_velocity_in_heads_per_hour(config):
    tracker = LiveSpeedTracker(IP9, 4.23, interval_seconds=1.0)
    assert tracker.observe(50.0, ts(10, 0, 0), True)[0].vel == pytest.approx(211.5)
