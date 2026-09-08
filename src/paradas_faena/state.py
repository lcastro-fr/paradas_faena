"""Line state shared between the PLC workers.

The frequency converter and the running flag live on one PLC, but a stop recorded on any
PLC is stamped with them. A reading older than grace_seconds reads back as None, so a
variador PLC going offline produces NULLs rather than indefinitely stale values.
"""

from __future__ import annotations

import datetime as dt
import threading


class LineState:
    def __init__(self, grace_seconds: float) -> None:
        self._grace = dt.timedelta(seconds=grace_seconds)
        self._lock = threading.Lock()
        self._running: bool | None = None
        self._running_at: dt.datetime | None = None
        self._vel: float | None = None
        self._vel_at: dt.datetime | None = None

    def set_running(self, running: bool, ts: dt.datetime) -> None:
        with self._lock:
            self._running = bool(running)
            self._running_at = ts

    def set_velocity(self, vel: float, ts: dt.datetime) -> None:
        with self._lock:
            self._vel = float(vel)
            self._vel_at = ts

    def invalidate(self) -> None:
        with self._lock:
            self._running = self._running_at = None
            self._vel = self._vel_at = None

    def snapshot(self, now: dt.datetime) -> tuple[bool | None, float | None]:
        with self._lock:
            running = self._running
            if self._running_at is None or now - self._running_at > self._grace:
                running = None
            vel = self._vel
            if self._vel_at is None or now - self._vel_at > self._grace:
                vel = None
            return running, vel
