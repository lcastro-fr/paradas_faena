from __future__ import annotations

import contextlib
import logging
from collections.abc import Sequence
from typing import Any

from pycomm3 import LogixDriver
from pycomm3.exceptions import CommError

__all__ = ["CommError", "PlcClient", "PlcReadError"]

log = logging.getLogger(__name__)


class PlcReadError(Exception):
    """A read completed but one or more tags came back unusable."""


class PlcClient:
    def __init__(self, ip: str) -> None:
        self.ip = ip
        self._driver: LogixDriver | None = None

    def __enter__(self) -> PlcClient:
        self._driver = LogixDriver(self.ip)
        self._driver.open()
        log.info("conectado a %s", self.ip)
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._driver is not None:
            with contextlib.suppress(Exception):
                self._driver.close()
            self._driver = None

    def read(self, names: Sequence[str]) -> dict[str, Any]:
        """Read every tag in one batched request"""
        if self._driver is None:
            raise PlcReadError(f"{self.ip}: cliente no conectado")
        if not names:
            return {}

        results = self._driver.read(*names)
        # LogixDriver.read returns a bare Tag for a single request, a list otherwise.
        if not isinstance(results, list):
            results = [results]

        if len(results) != len(names):
            raise PlcReadError(
                f"{self.ip}: se pidieron {len(names)} tags y volvieron {len(results)}"
            )

        values: dict[str, Any] = {}
        failed: list[str] = []
        for name, tag in zip(names, results, strict=True):
            if tag is None or tag.error or tag.value is None:
                failed.append(f"{name}: {getattr(tag, 'error', 'sin respuesta')}")
                continue
            values[name] = tag.value

        if failed:
            raise PlcReadError(f"{self.ip}: " + "; ".join(failed))
        return values
