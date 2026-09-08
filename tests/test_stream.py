"""Tests for the Redis Streams transport.

The delivery guarantee is the reason Redis is here rather than an in-process queue, so
these run against a real server: un-acked entries must come back, acked ones must not,
and a reader must never be blocked or raised at by a Redis outage.
"""

from __future__ import annotations

import datetime as dt

import pytest
from tests.conftest import requires_redis

from paradas_faena.events import Heartbeat, InputEdge, SessionClosed
from paradas_faena.stream import EventStream, build_client

pytestmark = requires_redis

T0 = dt.datetime(2026, 9, 7, 6, 0, 0, tzinfo=dt.UTC)
IP = "172.30.10.8"


def beat(n: int) -> Heartbeat:
    return Heartbeat(ip=IP, ts=T0 + dt.timedelta(seconds=n))


def events_of(batch):
    return [event for _, event in batch]


def ids_of(batch):
    return [message_id for message_id, _ in batch]


def test_publish_and_read_round_trips_every_event_type(stream):
    published = [
        beat(0),
        InputEdge(ip=IP, tag="Cont_P1", ts=T0, value=True, reason="change", version=1),
        SessionClosed(fecha=dt.date(2026, 9, 7), hora_inicio=dt.time(6, 2),
                      hora_fin=dt.time(16, 14), registros=1200),
    ]
    stream.publish_many(published)
    assert events_of(stream.read_new(10, 50)) == published


def test_nothing_to_read_returns_empty_after_the_block_expires(stream):
    assert stream.read_new(10, 20) == []


def test_read_respects_the_batch_ceiling(stream):
    stream.publish_many([beat(n) for n in range(10)])
    assert len(stream.read_new(4, 50)) == 4
    assert len(stream.read_new(100, 50)) == 6


def test_an_unacked_batch_comes_back_from_read_pending(stream):
    """Not acknowledging is the writer's entire retry mechanism."""
    stream.publish_many([beat(0), beat(1)])
    first = stream.read_new(10, 50)
    assert len(first) == 2

    assert events_of(stream.read_pending(10)) == events_of(first)
    assert events_of(stream.read_pending(10)) == events_of(first)


def test_an_acked_batch_does_not_come_back(stream):
    stream.publish_many([beat(0), beat(1)])
    batch = stream.read_new(10, 50)
    stream.ack(ids_of(batch))

    assert stream.read_pending(10) == []
    assert stream.read_new(10, 20) == []


def test_a_partial_ack_leaves_only_the_rest_pending(stream):
    stream.publish_many([beat(n) for n in range(4)])
    batch = stream.read_new(10, 50)
    stream.ack(ids_of(batch)[:2])

    assert events_of(stream.read_pending(10)) == events_of(batch)[2:]


def test_pending_entries_survive_a_new_stream_over_the_same_group(stream, redis_client,
                                                                  stream_names):
    """What a daemon restart looks like: same group, same consumer name, new object.

    This is why REDIS_CONSUMER has to be stable -- under a different name these entries
    would be orphaned.
    """
    stream.publish_many([beat(0), beat(1)])
    stream.read_new(10, 50)

    restarted = EventStream(
        redis_client, stream_names, "writer", "writer", maxlen=10_000, buffer_size=100
    )
    restarted.ensure_group()
    assert events_of(restarted.read_pending(10)) == [beat(0), beat(1)]


def test_a_different_consumer_name_does_not_see_anothers_pending(stream, redis_client,
                                                                 stream_names):
    stream.publish_many([beat(0)])
    stream.read_new(10, 50)

    other = EventStream(
        redis_client, stream_names, "writer", "otro", maxlen=10_000, buffer_size=100
    )
    assert other.read_pending(10) == []


def test_ensure_group_is_idempotent(stream):
    stream.ensure_group()
    stream.ensure_group()
    stream.publish(beat(0))
    assert len(stream.read_new(10, 50)) == 1


def test_pending_count_tracks_unacked_entries(stream):
    assert stream.pending_count() == 0
    stream.publish_many([beat(0), beat(1)])
    batch = stream.read_new(10, 50)
    assert stream.pending_count() == 2
    stream.ack(ids_of(batch))
    assert stream.pending_count() == 0


def test_maxlen_bounds_the_stream(redis_client, stream_names):
    """Approximate trimming, so assert the order of magnitude rather than an exact cap."""
    ev = EventStream(
        redis_client, stream_names, "writer", "writer", maxlen=1000, buffer_size=100
    )
    ev.ensure_group()
    for n in range(3000):
        ev.publish(beat(n))
    assert redis_client.xlen(stream_names) < 3000


def test_an_unreadable_entry_is_acked_instead_of_blocking_the_group(stream, redis_client,
                                                                    stream_names):
    """A poison entry must not be redelivered forever with everything behind it."""
    redis_client.xadd(stream_names, {"d": "no es json"})
    stream.publish(beat(1))

    assert events_of(stream.read_new(10, 50)) == [beat(1)]
    assert stream.pending_count() == 1        # only the good one, still unacked


def test_rejected_events_are_parked_on_their_own_stream(stream, redis_client,
                                                        stream_names):
    stream.reject(beat(0), "viola una foreign key")
    entries = redis_client.xrange(f"{stream_names}:rejected")
    assert len(entries) == 1
    assert "foreign key" in entries[0][1]["reason"]


# --- Redis unreachable ----------------------------------------------------------------


@pytest.fixture
def offline_stream(stream_names):
    """A stream pointed at a port nothing is listening on."""
    return EventStream(
        build_client("redis://127.0.0.1:1/0"), stream_names, "writer", "writer",
        maxlen=10_000, buffer_size=5,
    )


def test_publish_buffers_instead_of_raising_when_redis_is_down(offline_stream):
    """A reader that raises or blocks stops polling its PLC, and a stop that happens
    meanwhile is lost outright."""
    offline_stream.publish(beat(0))
    assert offline_stream.buffered == 1
    assert offline_stream.dropped == 0


def test_the_buffer_drops_the_oldest_once_full_and_counts_it(offline_stream):
    for n in range(12):
        offline_stream.publish(beat(n))
    assert offline_stream.buffered == 5
    assert offline_stream.dropped == 7


def test_the_buffer_drains_once_redis_comes_back(redis_client, stream_names):
    """Modelled by pointing the stream at a dead port, buffering, then swapping in a
    live client -- which is what a reconnect amounts to."""
    ev = EventStream(
        build_client("redis://127.0.0.1:1/0"), stream_names, "writer", "writer",
        maxlen=10_000, buffer_size=100,
    )
    ev.publish(beat(0))
    ev.publish(beat(1))
    assert ev.buffered == 2

    ev._redis = redis_client
    ev.ensure_group()
    assert ev.flush_buffer() == 0
    ev.publish(beat(2))

    assert ev.buffered == 0
    assert ev.dropped == 0
    # Buffered events keep their order and precede the one that triggered the drain.
    assert events_of(ev.read_new(10, 50)) == [beat(0), beat(1), beat(2)]


def test_retries_are_throttled_so_a_reader_is_not_stalled_by_every_event(offline_stream):
    """Each retry costs up to the socket timeout, on the PLC poll thread. Publishing a
    burst while Redis is down must not mean one connection attempt per event."""
    attempts = []
    original = offline_stream._xadd

    def counting(event):
        attempts.append(event)
        return original(event)

    offline_stream._xadd = counting
    for n in range(20):
        offline_stream.publish(beat(n))

    assert len(attempts) <= 2
