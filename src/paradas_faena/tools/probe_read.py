"""Read exactly what the daemon reads from a PLC, and time it.

Reports every tag's value or error, and whether a full cycle fits in POLL_SECONDS. That
last part matters here: the controllers are Micro820s, which have no multi-service
packet, so pycomm3 sends one request per tag and the cycle grows linearly with the tag
count.

    python -m paradas_faena.tools.probe_read 172.30.10.8
    python -m paradas_faena.tools.probe_read 172.30.10.8 --rounds 10
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time

from paradas_faena.config import ConfigError, load_config
from paradas_faena.db.repository import Repository
from paradas_faena.plc.client import PlcClient, PlcReadError


def _plan(ip: str) -> tuple[list[str], dict[str, str], float]:
    """(tags the daemon would read, {input tag: puesto}, poll_seconds)."""
    config = load_config()
    repo = Repository.connect(config.db)
    try:
        counters = [c for c in repo.load_counters() if c.ip == ip]
        variador = any(p.ip == ip and p.variador for p in repo.load_plcs())
    finally:
        repo.close()

    inputs = {c.input_tag: (c.name or c.tag) for c in counters if c.input_tag}
    names = [c.tag for c in counters] + list(inputs)
    if variador:
        names += [config.frec_tag, config.status_tag]
    return list(dict.fromkeys(names)), inputs, config.poll_seconds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="probe_read",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("ip")
    parser.add_argument("--rounds", type=int, default=6, help="lecturas a cronometrar")
    args = parser.parse_args(argv)

    try:
        names, inputs, poll_seconds = _plan(args.ip)
    except (ConfigError, Exception) as exc:
        print(f"no se pudo armar el plan de lectura: {exc}", file=sys.stderr)
        return 2

    if not names:
        print(f"{args.ip} no tiene contadores en counters_name", file=sys.stderr)
        return 2

    print(f"PLC {args.ip}: {len(names)} tags")
    print(f"  entradas mapeadas: {len(inputs)}/{len(names)}")
    if not inputs:
        print("  (sin input_tag cargado: no se registrarian tiempos de parada)")

    with PlcClient(args.ip) as client:
        driver = client._driver
        micro800 = getattr(driver, "_micro800", False)
        print(f"  modelo           : {driver.info.get('product_name', '?')}")
        print(f"  connection_size  : {driver.connection_size}")
        print(f"  multi-servicio   : {'NO (un request por tag)' if micro800 else 'si'}\n")

        try:
            values = client.read(names)
        except PlcReadError as exc:
            print(f"LECTURA FALLIDA: {exc}", file=sys.stderr)
            return 1

        for name in names:
            role = f"  <- {inputs[name]}" if name in inputs else ""
            print(f"    {name:24s} {values[name]!s:8s}{role}")

        times = []
        for _ in range(max(1, args.rounds)):
            started = time.monotonic()
            client.read(names)
            times.append((time.monotonic() - started) * 1000)

    median = statistics.median(times)
    budget = poll_seconds * 1000
    print(
        f"\n  {len(times)} lecturas: min={min(times):.0f} med={median:.0f} "
        f"max={max(times):.0f} ms   ({median / len(names):.1f} ms/tag)"
    )
    print(f"  POLL_SECONDS={poll_seconds} -> presupuesto {budget:.0f} ms")
    if max(times) > budget:
        holgura = max(times) / 1000 * 1.3
        print(f"  EXCEDE. Subí POLL_SECONDS a ~{holgura:.1f} o leé menos tags.")
    elif max(times) > budget * 0.8:
        print("  AJUSTADO: queda menos del 20% de margen.")
    else:
        print("  OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
