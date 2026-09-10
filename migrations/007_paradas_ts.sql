-- 007_paradas_ts.sql -- store instants, not wall clock, in paradas and velocidad.
--
-- The daemon has always captured a real instant: plc/worker.py takes one
-- `datetime.now(dt.UTC)` per poll tick and hands it to every tracker. input_status,
-- noria_status and plc_heartbeat keep it, as `ts timestamptz`. paradas and velocidad
-- did not: Repository.local_parts split it into a naive date + time and threw the
-- offset away, so the rows carried Buenos Aires wall clock with the zone recorded
-- nowhere but the container's TZ variable.
--
-- Three things that cost. A stop could not be joined to input_status on time without
-- re-applying an assumed offset. The CURRENT_DATE/CURRENT_TIME defaults render in the
-- *server's* TimeZone while the daemon wrote the plant's, so one column could hold two
-- different zones depending on who inserted the row. And nothing in the row said which.
--
-- The backfill reads every existing row as Buenos Aires wall clock, which is safe here
-- because every existing row was written by the daemon: only it sets event_uid, and
-- `count(*) filter (where event_uid is null)` is 0 in both tables. Legacy/FaenaPLC.py
-- rows -- the ones that would have carried the server's zone instead -- do not exist.
--
-- fecha/hora are dropped rather than kept as generated compatibility columns. The one
-- reader outside this repo, zato-fr/Faena/reporteParadas.py, moves to
-- `(ts at time zone 'America/Argentina/Buenos_Aires')::date` in the same deploy.
--
-- generales and inspeccion are knowingly left alone. generales.hora_inicio/hora_fin are
-- instants too, but they come from seconds-since-midnight in the tipificador CSV rather
-- than from a clock, and both tables are slated to be dropped outright.
--
-- ADD COLUMN appends, so `ts` lands at the end of both tables and the column order
-- changes. Anything doing a positional `select *` has to be updated even if it never
-- named fecha.

-- migrate:up

-- Fail fast rather than queue an ACCESS EXCLUSIVE lock behind a live daemon.
set local lock_timeout = '5s';

alter table monitoreo_faena.paradas   add column ts timestamptz;
alter table monitoreo_faena.velocidad add column ts timestamptz;

update monitoreo_faena.paradas
   set ts = (fecha + hora) at time zone 'America/Argentina/Buenos_Aires';
update monitoreo_faena.velocidad
   set ts = (fecha + hora) at time zone 'America/Argentina/Buenos_Aires';

-- No default. `ts` is the instant the PLC was *read*, and the writer spools to disk and
-- replays on reconnect -- a default would stamp write time and date a replayed event
-- silently late. NOT NULL with no default makes that a loud error instead.
alter table monitoreo_faena.paradas   alter column ts set not null;
alter table monitoreo_faena.velocidad alter column ts set not null;

-- Dropping the columns would take these with them; named so the intent is on the page.
drop index if exists monitoreo_faena.idx_paradas_range;
drop index if exists monitoreo_faena.idx_velocidad_fecha;
drop index if exists monitoreo_faena.idx_velocidad_hora;

alter table monitoreo_faena.paradas   drop column fecha, drop column hora;
alter table monitoreo_faena.velocidad drop column fecha, drop column hora;

create index idx_paradas_range on monitoreo_faena.paradas (ts)
    where status_noria is not false;
create index idx_velocidad_ts on monitoreo_faena.velocidad (ts);


-- migrate:down
--
-- Lossy on purpose: date + time cannot carry an offset, so the instant is discarded and
-- the plant's zone is baked back into the values.

set local lock_timeout = '5s';

drop index if exists monitoreo_faena.idx_paradas_range;
drop index if exists monitoreo_faena.idx_velocidad_ts;

alter table monitoreo_faena.paradas
    add column fecha date default CURRENT_DATE,
    add column hora  time default CURRENT_TIME;
alter table monitoreo_faena.velocidad
    add column fecha date default CURRENT_DATE,
    add column hora  time default CURRENT_TIME;

update monitoreo_faena.paradas
   set fecha = (ts at time zone 'America/Argentina/Buenos_Aires')::date,
       hora  = (ts at time zone 'America/Argentina/Buenos_Aires')::time;
update monitoreo_faena.velocidad
   set fecha = (ts at time zone 'America/Argentina/Buenos_Aires')::date,
       hora  = (ts at time zone 'America/Argentina/Buenos_Aires')::time;

create index idx_paradas_range on monitoreo_faena.paradas (fecha, hora)
    where status_noria is not false;
create index idx_velocidad_fecha on monitoreo_faena.velocidad (fecha);
create index idx_velocidad_hora  on monitoreo_faena.velocidad (hora);

alter table monitoreo_faena.paradas   drop column ts;
alter table monitoreo_faena.velocidad drop column ts;
