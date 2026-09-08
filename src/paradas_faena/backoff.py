"""Exponential backoff with jitter, interruptible by shutdown."""

from __future__ import annotations

import random
import threading


class Backoff:
    def __init__(self, initial: float, maximum: float, *, jitter: float = 0.25) -> None:
        self._initial = initial
        self._maximum = maximum
        self._jitter = jitter
        self._current = initial

    def reset(self) -> None:
        self._current = self._initial

    def next_delay(self) -> float:
        delay = self._current
        self._current = min(self._current * 2.0, self._maximum)
        # Jitter keeps workers that failed together from retrying in lockstep.
        return delay * (1.0 + random.uniform(-self._jitter, self._jitter))

    def sleep(self, shutdown: threading.Event) -> float:
        """Wait the next interval, returning early if shutdown is set."""
        delay = self.next_delay()
        shutdown.wait(delay)
        return delay
