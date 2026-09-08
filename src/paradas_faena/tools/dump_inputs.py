"""Live view of a PLC's InputStatus array, for mapping relays to workstations.

counters_name.input_index has to say which position in the InputStatus array carries each
workstation's stop relay, and nothing in the PLC tells you that -- someone has to trigger
each station's stop and watch which bit moves.

    python -m paradas_faena.tools.dump_inputs 172.30.10.8
    python -m paradas_faena.tools.dump_inputs 172.30.10.8 --size 32 --tag InputStatus

Then record what you found:

    update paradas_faena.counters_name set input_index = 4
     where ip = '172.30.10.8' and tag = 'Cont_Puesto_5';
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time

from ..config import ConfigError, load_config
from ..db.repository import Repository
from ..plc.client import PlcClient, PlcReadError


def _known_mapping(ip: str) -> dict[int, str]:
    """Whatever input_index values are already recorded, best-effort.

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
            c.input_index: c.tag
            for c in repo.load_counters()
            if c.ip == ip and c.input_index is not None
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
        "--tag", default=None,
        help="nombre del array de relays (por defecto TAG_INPUT_ARRAY o InputStatus)",
    )
    parser.add_argument(
        "--size", type=int, default=32,
        help="cuantos elementos pedir (por defecto 32); subilo si el array es mas grande",
    )
    parser.add_argument(
        "--interval", type=float, default=0.5, help="intervalo de lectura en segundos",
    )
    args = parser.parse_args(argv)

    tag_name = args.tag
    if tag_name is None:
        try:
            tag_name = load_config().input_array_tag
        except ConfigError:
            tag_name = "InputStatus"

    request = f"{tag_name}{{{args.size}}}"
    mapping = _known_mapping(args.ip)
    if mapping:
        print("input_index ya asignados en counters_name:")
        for index in sorted(mapping):
            print(f"  [{index:3d}] {mapping[index]}")
    else:
        print("(sin input_index asignados todavia para este PLC)")
    print(f"\nleyendo {request} de {args.ip} cada {args.interval}s; Ctrl-C para salir\n")

    previous: list[bool] | None = None
    try:
        with PlcClient(args.ip) as client:
            while True:
                try:
                    values = client.read([request])[request]
                except PlcReadError as exc:
                    print(f"lectura fallida: {exc}", file=sys.stderr)
                    time.sleep(args.interval)
                    continue

                current = [bool(v) for v in values]
                now = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]

                if previous is None:
                    activos = [i for i, v in enumerate(current) if v]
                    print(
                        f"{now}  estado inicial: {len(current)} elementos, "
                        f"en 1: {activos or 'ninguno'}"
                    )
                else:
                    # The snapshots can differ in length if the PLC program changed.
                    for index, (was, is_now) in enumerate(
                        zip(previous, current, strict=False)
                    ):
                        if was == is_now:
                            continue
                        owner = mapping.get(index, "SIN ASIGNAR")
                        arrow = "0 -> 1" if is_now else "1 -> 0"
                        print(f"{now}  [{index:3d}] {arrow}   {owner}")

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
