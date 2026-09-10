-- 006_counters_name_version.sql — version the tag configuration.
--
-- Which physical input belongs to which puesto can change: a relay gets rewired, a
-- station is repurposed. Without a version, correcting counters_name would retroactively
-- reinterpret every row already recorded under the old wiring.
--
-- So `version` joins the primary key, and the tables that reference it carry the version
-- they were recorded under. Rewiring becomes an INSERT of version N+1; history keeps
-- pointing at N.
--
-- The daemon always reads the highest version per (ip, tag) -- see
-- Repository.load_counters -- and stamps it on every event.

begin;

alter table monitoreo_faena.counters_name
    add column version integer not null default 1;

-- Children: backfilled to 1, matching the single version that existed until now.
alter table monitoreo_faena.paradas      add column version integer;
alter table monitoreo_faena.input_status add column version integer;
update monitoreo_faena.paradas      set version = 1 where version is null;
update monitoreo_faena.input_status set version = 1 where version is null;
alter table monitoreo_faena.paradas      alter column version set not null;
alter table monitoreo_faena.input_status alter column version set not null;

-- The foreign keys have to come off before the primary key they point at can move.
alter table monitoreo_faena.paradas      drop constraint counters_name_fk;
alter table monitoreo_faena.input_status drop constraint input_status_counters_name_fk;

alter table monitoreo_faena.counters_name drop constraint counters_name_pk;
alter table monitoreo_faena.counters_name
    add constraint counters_name_pk primary key (ip, tag, version);

alter table monitoreo_faena.paradas
    add constraint counters_name_fk foreign key (ip, tag, version)
        references monitoreo_faena.counters_name (ip, tag, version);
alter table monitoreo_faena.input_status
    add constraint input_status_counters_name_fk foreign key (ip, tag, version)
        references monitoreo_faena.counters_name (ip, tag, version);

-- An input tag maps to one puesto *within a version*. Across versions the same input
-- legitimately reappears -- that is the whole point -- so the old two-column unique
-- index would have blocked every rewiring.
drop index if exists monitoreo_faena.counters_name_input_tag_uq;
create unique index counters_name_input_tag_uq
    on monitoreo_faena.counters_name (ip, input_tag, version)
    where input_tag is not null;

-- The FK on the children is checked when a counters_name row is deleted or its version
-- changes; without this those checks are sequential scans.
create index idx_paradas_counter on monitoreo_faena.paradas (ip, tag, version);
create index idx_input_status_counter on monitoreo_faena.input_status (ip, tag, version);

commit;
