-- 004_retire_timers.sql — retire the PLC-side stop-time accumulator.
--
-- tiempos_parada held one snapshot per day of CTimersArray, the accumulator maintained
-- by ladder logic. It is superseded by input_status, which the application records
-- itself at 0.5 s resolution. Nothing reads tiempos_parada: the only consumer of this
-- schema (zato-fr/Faena/reporteParadas.py) counts `paradas` events and never selects
-- durations.
--
-- Renamed rather than dropped so the cut-over is reversible for free. Drop it once the
-- new numbers have been trusted for a week:
--     drop table paradas_faena.tiempos_parada_old;
--
-- orden_array existed only to index CTimersArray, which is no longer read.

-- migrate:up

alter table monitoreo_faena.tiempos_parada rename to tiempos_parada_old;
alter index monitoreo_faena.tiempos_parada_idx rename to tiempos_parada_old_idx;

alter table monitoreo_faena.counters_name drop column orden_array;

-- migrate:down

alter table monitoreo_faena.tiempos_parada_old rename to tiempos_parada;
alter index monitoreo_faena.tiempos_parada_old_idx rename to tiempos_parada_idx;
alter table monitoreo_faena.counters_name add column if not exists orden_array integer;
