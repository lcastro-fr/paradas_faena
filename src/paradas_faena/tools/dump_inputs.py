"""Live view of a PLC's digital inputs, for mapping them to puestos.

counters_name.input_tag has to hold the tag name of each puesto's stop input, and
nothing in the PLC tells you which is which -- someone has to trigger each station's
stop and watch which input moves.

Run it, have someone press the stop at one puesto, and read off the tag that flips. The
direction matters too: if these are normally-closed contacts, a stop shows up as
1 -> 0, not 0 -> 1.

    python -m paradas_faena.tools.dump_inputs 172.30.10.8
    python -m paradas_faena.tools.dump_inputs 172.30.10.8 --filter DI_ --interval 0.25
    python -m paradas_faena.tools.dump_inputs 172.30.10.8 --all

Then record what you found:

    update paradas_faena.counters_name set input_tag = '_IO_EM_DI_00'
     where ip = '172.30.10.8' and tag = 'Counter0';
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time

from paradas_faena.config import ConfigError, load_config
from paradas_faena.db.repository import Repository
from paradas_faena.plc.client import PlcClient, PlcReadError


def _known_mapping(ip: str) -> dict[str, str]:
    """{input tag: puesto} already recorded, best-effort.

    This tool has to work before the database is configured, so a failure is a warning.
    """
    try:
        config = load_config()
        repo = Repository.connect(config.db)
    except (ConfigError, Exception) as exc:
        print(f"(no se pudo leer counters_name: {exc})", file=sys.stderr)
        return {}
    try:
        return {
            c.input_tag: c.name or c.tag
            for c in repo.load_counters()
            if c.ip == ip and c.input_tag
        }
    finally:
        repo.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dump_inputs",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("ip", help="direccion del PLC, ej. 172.30.10.8")
    parser.add_argument(
        "--filter",
        default="_DI_",
        help="subcadena que debe tener el nombre del tag (por defecto _DI_)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="mirar todos los BOOL, sin filtrar",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="intervalo de lectura en segundos",
    )
    args = parser.parse_args(argv)

    mapping = _known_mapping(args.ip)

    try:
        with PlcClient(args.ip) as client:
            driver = client._driver
            names = sorted(
                name
                for name, info in driver.tags.items()
                if info.get("data_type") == "BOOL"
                and not any(info.get("dimensions", []))
                and (args.all or args.filter in name)
            )
            if not names:
                hint = "" if args.all else " (probá --all o cambiá --filter)"
                print(
                    f"no se encontraron entradas BOOL en {args.ip}{hint}", file=sys.stderr
                )
                return 1

            print(f"PLC {args.ip} — {len(names)} entradas BOOL")
            assigned = [n for n in names if n in mapping]
            if assigned:
                print("\nya asignadas en counters_name:")
                for name in assigned:
                    print(f"  {name:22s} {mapping[name]}")
            unassigned = [n for n in names if n not in mapping]
            if unassigned:
                print(f"\nsin asignar ({len(unassigned)}):")
                print("  " + "  ".join(unassigned))

            print(
                f"\nleyendo cada {args.interval}s; Ctrl-C para salir. "
                "Si son contactos normalmente cerrados, la parada es 1 -> 0.\n"
            )

            previous: dict[str, bool] | None = None
            while True:
                try:
                    values = client.read(names)
                except PlcReadError as exc:
                    print(f"lectura fallida: {exc}", file=sys.stderr)
                    time.sleep(args.interval)
                    continue

                current = {name: bool(values[name]) for name in names}
                now = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]

                if previous is None:
                    ones = [n for n, v in current.items() if v]
                    zeros = [n for n, v in current.items() if not v]
                    print(f"{now}  estado inicial")
                    print(f"    en 1 ({len(ones)}): {'  '.join(ones) or 'ninguna'}")
                    print(f"    en 0 ({len(zeros)}): {'  '.join(zeros) or 'ninguna'}")
                else:
                    for name in names:
                        if previous[name] == current[name]:
                            continue
                        owner = mapping.get(name, "SIN ASIGNAR")
                        arrow = "0 -> 1" if current[name] else "1 -> 0"
                        print(f"{now}  {name:22s} {arrow}   {owner}")

                previous = current
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nfin")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
