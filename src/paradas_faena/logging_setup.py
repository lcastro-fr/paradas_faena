"""Logging to stdout; the container runtime owns collection and rotation."""

from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)-7s %(threadName)-18s %(name)-28s %(message)s"


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))

    # pycomm3 logs every CIP request at DEBUG: at 0.5 s per PLC that is a firehose.
    logging.getLogger("pycomm3").setLevel(logging.WARNING)
