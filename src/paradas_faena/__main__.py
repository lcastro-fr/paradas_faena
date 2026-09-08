"""Daemon entrypoint: wiring, signals, orderly shutdown."""

from __future__ import annotations

import collections
import logging
import signal
import sys
import threading
import time

import psycopg
from redis.exceptions import RedisError

from .backoff import Backoff
from .config import Config, ConfigError, load_config
from .db.repository import CounterConfig, PlcConfig, Repository
from .logging_setup import setup_logging
from .plc.worker import PlcWorker
from .runner import ManagedThread
from .session import StopFileWatcher
from .state import LineState
from .stream import EventStream, build_client
from .writer import Writer

log = logging.getLogger("paradas_faena")


def load_topology(
    config: Config, shutdown: threading.Event
) -> tuple[list[PlcConfig], dict[str, list[CounterConfig]]]:
    """Read the PLC and tag configuration, retrying until the database answers.

    Short-lived: the connection closes before the writer opens the long-lived one.
    """
    backoff = Backoff(config.backoff_initial_seconds, config.backoff_max_seconds)
    while not shutdown.is_set():
        repo = None
        try:
            repo = Repository.connect(config.db)
            plcs = repo.load_plcs()
            counters = repo.load_counters()
            break
        except psycopg.Error as exc:
            delay = backoff.sleep(shutdown)
            log.error("no se pudo leer la configuracion: %s; reintento en ~%.1fs",
                      exc, delay)
        finally:
            if repo is not None:
                repo.close()
    else:
        return [], {}

    by_ip: dict[str, list[CounterConfig]] = collections.defaultdict(list)
    for counter in counters:
        by_ip[counter.ip].append(counter)

    orphans = sorted(set(by_ip) - {plc.ip for plc in plcs})
    if orphans:
        log.error("counters_name referencia PLCs ausentes de la tabla plcs: %s", orphans)

    for plc in plcs:
        mapped = sum(1 for c in by_ip.get(plc.ip, []) if c.input_index is not None)
        log.info(
            "PLC %s (%s)%s: %d contadores, %d con input_index",
            plc.ip, plc.nombre or "sin nombre",
            " [variador]" if plc.variador else "",
            len(by_ip.get(plc.ip, [])), mapped,
        )

    variadores = [p.ip for p in plcs if p.variador]
    if not variadores:
        log.error(
            "ningun PLC marcado como variador en la tabla plcs: no se registrara "
            "velocidad ni estado de la noria (ver migrations/001_config.sql)"
        )
    elif len(variadores) > 1:
        log.warning(
            "mas de un PLC marcado como variador (%s); ambos publicaran estado de linea",
            variadores,
        )

    return plcs, dict(by_ip)


def build_stream(config: Config, shutdown: threading.Event) -> EventStream | None:
    backoff = Backoff(config.backoff_initial_seconds, config.backoff_max_seconds)
    stream = EventStream(
        build_client(config.redis.url),
        config.redis.stream,
        config.redis.group,
        config.redis.consumer,
        maxlen=config.redis.maxlen,
        buffer_size=config.redis.buffer_size,
    )
    while not shutdown.is_set():
        try:
            stream.ensure_group()
            log.info("redis listo: %s grupo=%s consumidor=%s",
                     config.redis.stream, config.redis.group, config.redis.consumer)
            return stream
        except RedisError as exc:
            delay = backoff.sleep(shutdown)
            log.error("redis no responde: %s; reintento en ~%.1fs", exc, delay)
    return None


def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"configuracion invalida: {exc}", file=sys.stderr)
        return 2

    setup_logging(config.log_level)
    log.info("iniciando paradas_faena; db=%r", config.db)

    shutdown = threading.Event()

    def handle_signal(signum: int, _frame: object) -> None:
        log.warning("recibida senal %s, cerrando", signal.Signals(signum).name)
        shutdown.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    stream = build_stream(config, shutdown)
    if stream is None:
        return 0

    plcs, counters_by_ip = load_topology(config, shutdown)
    if shutdown.is_set():
        return 0
    if not plcs:
        log.critical("la tabla plcs esta vacia, nada que hacer")
        return 3

    writer = Writer(stream, config, shutdown)
    # One shared instance: the variador PLC's worker writes it, every worker reads it.
    line_state = LineState(config.status_grace_seconds)
    workers = [
        PlcWorker(
            plc=plc,
            counters=counters_by_ip.get(plc.ip, []),
            stream=stream,
            line_state=line_state,
            config=config,
            shutdown=shutdown,
        )
        for plc in plcs
    ]
    watcher = StopFileWatcher(stream, config, shutdown)

    threads: list[ManagedThread] = [writer, *workers, watcher]
    for thread in threads:
        thread.start()

    # A thread that dies is a bug: log it and shut down so the container restarts clean.
    while not shutdown.wait(1.0):
        for thread in threads:
            if not thread.is_alive():
                log.critical("el hilo %s murio, cerrando el proceso", thread.name)
                shutdown.set()
                break

    deadline = time.monotonic() + config.shutdown_grace_seconds
    for worker in [*workers, watcher]:
        worker.join(timeout=max(0.5, deadline - time.monotonic()))
    # The writer goes last: it needs the readers stopped before it can drain.
    writer.join(timeout=config.shutdown_grace_seconds + 5.0)

    still_running = [t.name for t in threads if t.is_alive()]
    if still_running:
        log.warning("hilos que no terminaron a tiempo: %s", still_running)
    log.info("paradas_faena detenido")
    return 0


if __name__ == "__main__":
    sys.exit(main())
