"""Guards against a whole class of bug the deployed Python found and the tests did not.

threading.Thread sets private instance attributes in __init__, and 3.13 added
`self._handle`. A subclass with a `_handle()` method had it silently replaced by a
`_thread._ThreadHandle`, so calling it raised TypeError -- at runtime, in the container,
on a Python version the local venv was not running.

These tests are version-independent: they assert the daemon's threads do not inherit
Thread's namespace at all, and that none of their methods collide with the private
attributes any Thread has.
"""

from __future__ import annotations

import threading

import pytest

from paradas_faena.plc.worker import PlcWorker
from paradas_faena.runner import ManagedThread
from paradas_faena.session import StopFileWatcher
from paradas_faena.writer import Writer

THREAD_CLASSES = [Writer, PlcWorker, StopFileWatcher]


@pytest.mark.parametrize("cls", THREAD_CLASSES, ids=lambda c: c.__name__)
def test_daemon_threads_do_not_subclass_thread(cls):
    assert issubclass(cls, ManagedThread)
    assert not issubclass(cls, threading.Thread)


@pytest.mark.parametrize("cls", THREAD_CLASSES, ids=lambda c: c.__name__)
def test_no_method_collides_with_a_thread_private_attribute(cls):
    """Belt and braces: even composed, a name clash would be confusing."""
    probe = threading.Thread(target=lambda: None)
    thread_attrs = {name for name in vars(probe) if name.startswith("_")}
    collisions = {
        name for name in dir(cls)
        if name in thread_attrs and callable(getattr(cls, name, None))
    }
    assert not collisions, f"{cls.__name__} choca con atributos de Thread: {collisions}"


@pytest.mark.parametrize("cls", THREAD_CLASSES, ids=lambda c: c.__name__)
def test_the_thread_api_the_supervisor_relies_on_is_present(cls):
    for method in ("start", "join", "is_alive", "run"):
        assert callable(getattr(cls, method, None)), f"{cls.__name__}.{method}"


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_an_unhandled_exception_is_logged_and_ends_the_thread(caplog):
    class Exploding(ManagedThread):
        def run(self):
            raise RuntimeError("boom")

    thread = Exploding("exploding")
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert "excepcion no controlada" in caplog.text


def test_a_managed_thread_runs_and_finishes():
    ran = threading.Event()

    class Worker(ManagedThread):
        def run(self):
            ran.set()

    thread = Worker("worker")
    thread.start()
    thread.join(timeout=5)

    assert ran.is_set()
    assert not thread.is_alive()
    assert thread.name == "worker"
