"""Run a reporting query from queries/ with the right parameters filled in.

The queries take `max_segment`, which must match REASSERT_SECONDS -- a value below the
re-assert interval silently truncates real stop time. This supplies it from the same
config the daemon uses, so the two cannot drift.

    python -m paradas_faena.tools.report                       # lista las consultas
    python -m paradas_faena.tools.report tiempo_parada_por_puesto
    python -m paradas_faena.tools.report paradas_individuales --dia 2026-09-07
    python -m paradas_faena.tools.report cobertura_datos --desde 06:00 --hasta 18:00
    python -m paradas_faena.tools.report tiempo_parada_por_puesto --csv > salida.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import pathlib
import sys

from paradas_faena.config import Config, ConfigError, load_config
from paradas_faena.db.repository import Repository

QUERIES_DIR = pathlib.Path(__file__).resolve().parents[3] / "queries"


def available() -> list[str]:
    if not QUERIES_DIR.is_dir():
        return []
    return sorted(p.stem for p in QUERIES_DIR.glob("*.sql"))


def _moment(text: str, day: dt.date) -> dt.datetime:
    """Parse `HH:MM`, `HH:MM:SS` or a full ISO timestamp, in local time."""
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        try:
            clock = dt.time.fromisoformat(text)
        except ValueError as exc:
            raise SystemExit(
                f"no se entiende el momento {text!r}; usá HH:MM o 2026-09-07T06:00"
            ) from exc
        parsed = dt.datetime.combine(day, clock)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def parameters(
    sql: str, config: Config, desde: dt.datetime, hasta: dt.datetime
) -> dict[str, object]:
    """Only what this query actually asks for, so a new query needs no code here."""
    known: dict[str, object] = {
        "desde": desde,
        "hasta": hasta,
        "max_segment": config.max_segment(),
        "heartbeat": config.heartbeat_interval(),
    }
    return {name: value for name, value in known.items() if f"%({name})s" in sql}


def _render(headers: list[str], rows: list[tuple], as_csv: bool) -> None:
    if as_csv:
        writer = csv.writer(sys.stdout)
        writer.writerow(headers)
        writer.writerows(rows)
        return

    if not rows:
        print("  (sin resultados)")
        return

    cells = [[("" if v is None else str(v)) for v in row] for row in rows]
    widths = [
        max(len(headers[i]), max(len(c[i]) for c in cells)) for i in range(len(headers))
    ]
    print("  " + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("  " + "  ".join("-" * w for w in widths))
    for row in cells:
        print("  " + "  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="report",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("query", nargs="?", help="nombre de un archivo de queries/")
    parser.add_argument("--dia", help="fecha a reportar (por defecto hoy)")
    parser.add_argument("--desde", default="00:00", help="HH:MM o ISO (por defecto 00:00)")
    parser.add_argument("--hasta", default="23:59:59", help="HH:MM o ISO")
    parser.add_argument("--csv", action="store_true", help="salida en CSV")
    args = parser.parse_args(argv)

    names = available()
    if args.query is None:
        print("consultas disponibles:")
        for name in names:
            print(f"  {name}")
        return 0
    if args.query not in names:
        print(f"no existe {args.query!r}. Disponibles: {', '.join(names)}", file=sys.stderr)
        return 2

    day = dt.date.fromisoformat(args.dia) if args.dia else dt.date.today()
    desde = _moment(args.desde, day)
    hasta = _moment(args.hasta, day)
    if hasta <= desde:
        print(f"la ventana está al revés: {desde} -> {hasta}", file=sys.stderr)
        return 2

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"configuracion invalida: {exc}", file=sys.stderr)
        return 2

    sql = (QUERIES_DIR / f"{args.query}.sql").read_text()
    params = parameters(sql, config, desde, hasta)

    if not args.csv:
        print(f"{args.query}  {desde:%Y-%m-%d %H:%M}  ->  {hasta:%Y-%m-%d %H:%M}")
        extra = {k: v for k, v in params.items() if k not in ("desde", "hasta")}
        if extra:
            print("  " + "  ".join(f"{k}={v}" for k, v in extra.items()))
        print()

    repo = Repository.connect(config.db)
    try:
        with repo._conn.cursor() as cur:
            cur.execute(sql, params)
            headers = [d.name for d in cur.description or []]
            rows = cur.fetchall()
    finally:
        repo.close()

    _render(headers, rows, args.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
