-- 001_config.sql — make the tag/PLC configuration data-driven.
--
-- `input_index` is the position of this workstation's stop relay inside the PLC's
-- InputStatus array, the same way `orden_array` used to index CTimersArray. It is
-- nullable so the migration can be applied before the mapping is known; a counter
-- with a NULL input_index is simply not polled for input state.
--
-- `plcs.variador` replaces the IP_PLC_08 / IP_PLC_09 env vars: the daemon now reads
-- the PLC list from the table and asks it which one carries the frequency converter,
-- so adding a third PLC needs no code change.

begin;

alter table monitoreo_faena.counters_name
    add column if not exists input_index integer;

create unique index if not exists counters_name_input_index_uq
    on monitoreo_faena.counters_name (ip, input_index)
    where input_index is not null;

alter table monitoreo_faena.plcs
    add column if not exists variador boolean not null default false;


commit;
