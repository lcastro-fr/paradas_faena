# legacy

`FaenaPLC.py` as it ran before the refactor, kept runnable on purpose.

Verification step 4 in the rollout is a shadow run: the new daemon writes to
`paradas_faena_shadow` while this script keeps writing production for one full faena
day, so the two can be compared without risking anything. Delete this directory once the
cut-over is done and the numbers have been trusted for a week.

It needs `pycomm3`, `psycopg2`, `pandas`, `python-dotenv`, and a `.env` using the *old*
variable names (`HOST`, `DBNAME`, `SCHEMA`, `USER`, `PASSWORD`). Note that `USER` only
works on Windows -- on any POSIX host the shell's exported `USER` wins and the script
connects as the wrong database user.
