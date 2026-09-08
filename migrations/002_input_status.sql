-- 002_input_status.sql — raw relay state, the source of truth for stop duration.
--
-- One row per observed transition of a workstation's stop relay, plus a re-assert row
-- every REASSERT_SECONDS while the relay reads true. The re-asserts are what bound the
-- damage from a data gap: because a live `true` segment is never longer than the
-- re-assert interval, any longer segment must be an outage, and the reporting query
-- caps it instead of charging the whole gap to that workstation.
--
-- `ts` is the instant the PLC was read, captured in the reader thread.

begin;

create table paradas_faena.input_status
(
    ip    inet        not null,
    tag   varchar     not null,
    ts    timestamptz not null,
    value boolean     not null,
    constraint input_status_pk
        primary key (ip, tag, ts),
    constraint input_status_counters_name_fk
        foreign key (ip, tag) references paradas_faena.counters_name
);

-- Reporting always slices by time, and the window function partitions by (ip, tag).
create index idx_input_status_ts on paradas_faena.input_status (ts);
create index idx_input_status_tag_ts on paradas_faena.input_status (ip, tag, ts);

commit;
