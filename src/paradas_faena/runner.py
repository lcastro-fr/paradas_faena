from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)


class ManagedThread:
    def __init__(self, name: str) -> None:
        self.name = name
        self._thread = threading.Thread(target=self._guarded_run, name=name, daemon=True)

    def run(self) -> None:
        raise NotImplementedError

    def _guarded_run(self) -> None:
        try:
            self.run()
        except BaseException:
            # The supervisor in __main__ notices a dead thread and shuts down, but only
            # a logged traceback says why.
            log.exception("%s: excepcion no controlada, el hilo termina", self.name)
            raise

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread.is_alive()
