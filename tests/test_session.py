"""Tests for the tipificador CSV parser and the stop-file watcher.

hora_inicio is the timestamp of the third carcass, hora_fin that of the last row, and
registros the number of rows carrying a registro -- all as in the original.
"""

from __future__ import annotations

import datetime as dt
import threading

import pytest
from tests.conftest import requires_redis

from paradas_faena.session import (
    StopFileError,
    StopFileWatcher,
    parse_stop_file,
    seconds_to_time,
)

pytestmark = requires_redis


def write_csv(path, rows, header="tiempo,registro"):
    path.write_text(header + "\n" + "\n".join(rows) + "\n")
    return path


# --------------------------------------------------------------------------------------
# seconds_to_time
# --------------------------------------------------------------------------------------


def test_seconds_to_time_converts():
    assert seconds_to_time(0) == dt.time(0, 0, 0)
    assert seconds_to_time(21600) == dt.time(6, 0, 0)
    assert seconds_to_time(58496) == dt.time(16, 14, 56)


def test_seconds_to_time_wraps_past_a_day_instead_of_raising():
    assert seconds_to_time(86400) == dt.time(0, 0, 0)
    assert seconds_to_time(90000) == dt.time(1, 0, 0)


def test_seconds_to_time_rejects_negatives():
    with pytest.raises(StopFileError):
        seconds_to_time(-1)


# --------------------------------------------------------------------------------------
# parse_stop_file
# --------------------------------------------------------------------------------------


def test_parses_start_from_the_third_row_end_from_the_last(tmp_path):
    path = write_csv(tmp_path / "20260907.CSV", [
        "21600,1", "21660,2", "21720,3", "21780,4", "58496,5",
    ])
    inicio, fin, registros = parse_stop_file(path)
    assert inicio == dt.time(6, 2, 0)      # third row, 21720 s
    assert fin == dt.time(16, 14, 56)      # last row
    assert registros == 5


def test_rows_without_a_registro_are_not_counted(tmp_path):
    path = write_csv(tmp_path / "f.CSV", [
        "21600,1", "21660,", "21720,3", "21780,4",
    ])
    _, _, registros = parse_stop_file(path)
    assert registros == 3


def test_extra_columns_are_ignored(tmp_path):
    path = write_csv(
        tmp_path / "f.CSV",
        ["21600,1,a,b", "21660,2,c,d", "21720,3,e,f"],
        header="tiempo,registro,garron,tropa",
    )
    inicio, fin, registros = parse_stop_file(path)
    assert (inicio, fin, registros) == (dt.time(6, 2), dt.time(6, 2), 3)


def test_a_bom_from_a_windows_writer_is_tolerated(tmp_path):
    """With the wrong encoding the 'tiempo' column appears to be missing."""
    path = tmp_path / "f.CSV"
    path.write_bytes(b"\xef\xbb\xbftiempo,registro\r\n21600,1\r\n21660,2\r\n21720,3\r\n")
    inicio, _, registros = parse_stop_file(path)
    assert (inicio, registros) == (dt.time(6, 2), 3)


def test_fewer_than_three_rows_falls_back_to_the_first(tmp_path):
    path = write_csv(tmp_path / "f.CSV", ["21600,1", "21660,2"])
    inicio, fin, registros = parse_stop_file(path)
    assert (inicio, fin, registros) == (dt.time(6, 0), dt.time(6, 1), 2)


def test_illegible_time_skips_the_row_rather_than_failing(tmp_path):
    path = write_csv(tmp_path / "f.CSV", ["21600,1", "n/a,2", "21720,3", "21780,4"])
    inicio, fin, _ = parse_stop_file(path)
    assert (inicio, fin) == (dt.time(6, 3), dt.time(6, 3))


def test_missing_columns_is_an_error(tmp_path):
    path = write_csv(tmp_path / "f.CSV", ["21600"], header="tiempo")
    with pytest.raises(StopFileError, match="registro"):
        parse_stop_file(path)


def test_no_usable_rows_is_an_error(tmp_path):
    path = write_csv(tmp_path / "f.CSV", [",", ","])
    with pytest.raises(StopFileError):
        parse_stop_file(path)


def test_empty_file_is_an_error(tmp_path):
    path = tmp_path / "f.CSV"
    path.write_text("")
    with pytest.raises(StopFileError, match="encabezado"):
        parse_stop_file(path)


# --------------------------------------------------------------------------------------
# StopFileWatcher
# --------------------------------------------------------------------------------------


def make_watcher(stream, config):
    return StopFileWatcher(stream, config, threading.Event()), stream


def published(stream):
    batch = stream.read_new(100, 50)
    stream.ack([message_id for message_id, _ in batch])
    return [event for _, event in batch]


def test_nothing_is_published_while_the_file_is_absent(tmp_path, stream, config):
    watcher, _ = make_watcher(stream, config)
    watcher._check(dt.date(2026, 9, 7))
    assert published(stream) == []


def test_the_file_must_be_size_stable_before_it_is_read(tmp_path, stream, config):
    """The tipificador appends as the shift runs, so reading the moment the file
    appears would capture a partial day."""
    day = dt.date(2026, 9, 7)
    watcher, _ = make_watcher(stream, config)
    path = write_csv(tmp_path / "20260907.CSV", ["21600,1", "21660,2", "21720,3"])

    watcher._check(day)                       # first sighting: record size only
    assert published(stream) == []

    write_csv(path, ["21600,1", "21660,2", "21720,3", "21780,4"])
    watcher._check(day)                       # still growing
    assert published(stream) == []

    watcher._check(day)                       # unchanged: now it is read
    (event,) = published(stream)
    assert event.registros == 4
    assert event.fecha == day


def test_each_day_is_published_only_once(tmp_path, stream, config):
    day = dt.date(2026, 9, 7)
    watcher, _ = make_watcher(stream, config)
    write_csv(tmp_path / "20260907.CSV", ["21600,1", "21660,2", "21720,3"])

    watcher._check(day)
    watcher._check(day)
    assert len(published(stream)) == 1

    for _ in range(5):
        watcher._check(day)
    assert published(stream) == []


def test_a_lowercase_extension_is_found_too(tmp_path, stream, config):
    """Written from Windows, where case does not matter; a CIFS mount may not fold it."""
    day = dt.date(2026, 9, 7)
    watcher, _ = make_watcher(stream, config)
    write_csv(tmp_path / "20260907.csv", ["21600,1", "21660,2", "21720,3"])
    watcher._check(day)
    watcher._check(day)
    assert len(published(stream)) == 1


def test_the_path_follows_the_date_so_the_daemon_survives_midnight(
    tmp_path, stream, config
):
    watcher, _ = make_watcher(stream, config)
    write_csv(tmp_path / "20260907.CSV", ["1,1", "2,2", "3,3"])
    write_csv(tmp_path / "20260908.CSV", ["4,1", "5,2", "6,3", "7,4"])

    for day in (dt.date(2026, 9, 7), dt.date(2026, 9, 8)):
        watcher._check(day)
        watcher._check(day)

    events = published(stream)
    assert [(e.fecha, e.registros) for e in events] == [
        (dt.date(2026, 9, 7), 3),
        (dt.date(2026, 9, 8), 4),
    ]


def test_a_structurally_broken_file_is_given_up_on_rather_than_retried_forever(
    tmp_path, stream, config
):
    """A file missing its columns will not fix itself; retrying all day is noise."""
    day = dt.date(2026, 9, 7)
    watcher, _ = make_watcher(stream, config)
    write_csv(tmp_path / "20260907.CSV", ["21600"], header="tiempo")

    watcher._check(day)
    watcher._check(day)
    assert published(stream) == []
    assert day in watcher._processed


def test_the_watcher_thread_shuts_down_on_the_event(tmp_path, stream, config):
    shutdown = threading.Event()
    watcher = StopFileWatcher(stream, config, shutdown)

    watcher.start()
    shutdown.set()
    watcher.join(timeout=5.0)
    assert not watcher.is_alive()
