# legacy

`FaenaPLC.py` as it ran before the refactor, kept runnable on purpose.

Verification step 4 in the rollout is a shadow run: the new daemon writes to
`paradas_faena_shadow` while this script keeps writing production for one full faena
day, so the two can be compared without risking anything. Delete this directory once the
cut-over is done and the numbers have been trusted for a week.

It only runs against a database at migration **003 or earlier**, and has not been
runnable against current production since `004`: it selects `counters_name.orden_array`
(dropped in 004), writes `tiempos_parada` (renamed in 004), and omits `paradas.version`
(made `NOT NULL` with no default in 006). Migration `007` moved `paradas`/`velocidad` to
a single `ts timestamptz`, so when comparing the shadow run against production, compare
**instants, not wall clock**: the new daemon stores the true instant, while these rows
carry the *database server's* wall clock via the `CURRENT_DATE`/`CURRENT_TIME` defaults.
If that server is UTC the two differ by three hours and look like a bug when they are
not -- compare `shadow.ts` against `(prod.fecha + prod.hora) at time zone <server zone>`.

It needs `pycomm3`, `psycopg2`, `pandas`, `python-dotenv`, and a `.env` using the *old*
variable names (`HOST`, `DBNAME`, `SCHEMA`, `USER`, `PASSWORD`). Note that `USER` only
works on Windows -- on any POSIX host the shell's exported `USER` wins and the script
connects as the wrong database user.
