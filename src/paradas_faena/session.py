from __future__ import annotations

import csv
import datetime as dt
import logging
import threading
from pathlib import Path

from .config import Config
from .events import SessionClosed
from .runner import ManagedThread
from .stream import EventStream

log = logging.getLogger(__name__)


class StopFileError(Exception):
    pass


def seconds_to_time(seconds: float) -> dt.time:
    total = int(seconds)
    if total < 0:
        raise StopFileError(f"tiempo negativo en el CSV: {seconds}")
    if total >= 86400:
        log.warning("tiempo %ds excede las 24 h, se toma el resto", total)
        total %= 86400
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return dt.time(hour=hours, minute=minutes, second=secs)


def parse_stop_file(path: Path) -> tuple[dt.time, dt.time, int]:
    """Extract (hora_inicio, hora_fin, registros) from the tipificador CSV.

    hora_inicio is the timestamp of the third carcass, matching the original: "se toma la
    hora de la tercer media".
    """
    tiempos: list[float] = []
    registros = 0
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise StopFileError(f"{path.name}: sin encabezado")
        missing = {"tiempo", "registro"} - set(reader.fieldnames)
        if missing:
            raise StopFileError(f"{path.name}: faltan columnas {sorted(missing)}")
        for row in reader:
            raw = (row.get("tiempo") or "").strip()
            if raw:
                try:
                    tiempos.append(float(raw))
                except ValueError:
                    log.warning("%s: tiempo ilegible %r, fila ignorada", path.name, raw)
            if (row.get("registro") or "").strip():
                registros += 1

    if not tiempos:
        raise StopFileError(f"{path.name}: ninguna fila con tiempo")

    if len(tiempos) >= 3:
        inicio = tiempos[2]
    else:
        log.warning(
            "%s: solo %d filas, se usa la primera como hora de inicio en lugar de la "
            "tercer media",
            path.name,
            len(tiempos),
        )
        inicio = tiempos[0]

    return seconds_to_time(inicio), seconds_to_time(tiempos[-1]), registros


class StopFileWatcher(ManagedThread):
    def __init__(
        self, stream: EventStream, config: Config, shutdown: threading.Event
    ) -> None:
        super().__init__("stop-file")
        self._stream = stream
        self._cfg = config
        self._shutdown = shutdown
        self._processed: set[dt.date] = set()
        self._sizes: dict[Path, int] = {}

    def _candidates(self, day: dt.date) -> list[Path]:
        # Written from Windows, where case does not matter; a CIFS mount may or may not
        # fold it.
        stem = day.strftime("%Y%m%d")
        return [self._cfg.edu_path / f"{stem}.CSV", self._cfg.edu_path / f"{stem}.csv"]

    def run(self) -> None:
        log.info("vigilando %s", self._cfg.edu_path)
        while not self._shutdown.is_set():
            try:
                self._check(dt.date.today())
            except Exception as exc:
                log.error("error revisando el archivo de stop: %s", exc)
            self._shutdown.wait(self._cfg.stopfile_poll_seconds)
        log.info("vigilancia del archivo de stop terminada")

    def _check(self, day: dt.date) -> None:
        if day in self._processed:
            return
        for path in self._candidates(day):
            if not path.exists():
                continue
            if not self._is_stable(path):
                return
            try:
                inicio, fin, registros = parse_stop_file(path)
            except StopFileError as exc:
                log.error("%s", exc)
                # A structurally broken file will not fix itself; retrying all day is
                # only noise.
                self._processed.add(day)
                return
            log.warning(
                "fin de faena %s: inicio=%s fin=%s registros=%d (desde %s)",
                day,
                inicio,
                fin,
                registros,
                path.name,
            )
            self._stream.publish(
                SessionClosed(
                    fecha=day, hora_inicio=inicio, hora_fin=fin, registros=registros
                )
            )
            self._processed.add(day)
            self._sizes.pop(path, None)
            return

    def _is_stable(self, path: Path) -> bool:
        """True once the size has not changed between two consecutive checks.

        The tipificador appends to this file as the shift runs, so reading it the moment
        it appears would capture a partial day.
        """
        size = path.stat().st_size
        previous = self._sizes.get(path)
        self._sizes[path] = size
        if previous is None:
            log.info("%s aparecio (%d bytes), esperando que se estabilice", path.name, size)
            return False
        if previous != size:
            log.info("%s sigue creciendo (%d -> %d bytes)", path.name, previous, size)
            return False
        return True
