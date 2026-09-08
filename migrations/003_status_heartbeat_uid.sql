-- 003_status_heartbeat_uid.sql
--
-- noria_status: the line-running boolean was read every second and never stored with a
-- timestamp, only stamped onto rows as a possibly-stale flag. Storing its transitions
-- lets a query ask, after the fact, whether the line was actually running while a
-- workstation held it -- so time spent "holding" an already-stopped line isn't charged
-- to that workstation.
--
-- plc_heartbeat: liveness. A missing run of heartbeats is how you tell "no stops
-- happened" apart from "the daemon was down".
--
-- event_uid: the writer spools events to disk when the database is unreachable and
-- replays them on reconnect. input_status, noria_status and plc_heartbeat have natural
-- keys, so replay is idempotent via ON CONFLICT DO NOTHING. paradas and velocidad have
-- only a serial id, so they get a unique event id assigned at read time to make replay
-- (and a crash between COMMIT and spool truncation) idempotent too.

begin;

create table paradas_faena.noria_status
(
    ip      inet        not null,
    ts      timestamptz not null,
    running boolean     not null,
    constraint noria_status_pk primary key (ip, ts)
);

create table paradas_faena.plc_heartbeat
(
    ip inet        not null,
    ts timestamptz not null,
    constraint plc_heartbeat_pk primary key (ip, ts)
);

alter table paradas_faena.paradas   add column if not exists event_uid uuid;
alter table paradas_faena.velocidad add column if not exists event_uid uuid;

-- Not partial: PostgreSQL already permits many NULLs in a unique index, so existing
-- rows (which all have event_uid NULL) are fine, and a non-partial index is what
-- ON CONFLICT (event_uid) can infer without repeating a predicate.
create unique index if not exists paradas_event_uid_uq
    on paradas_faena.paradas (event_uid);
create unique index if not exists velocidad_event_uid_uq
    on paradas_faena.velocidad (event_uid);

commit;
