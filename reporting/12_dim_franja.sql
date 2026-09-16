-- 12_dim_franja.sql -- dimension de franja intradiaria.
--
-- La granularidad sale de v_parametros.franja_minutos (15 por defecto): Power BI enrolla
-- de franja a hora sin problema, pero no puede desagregar lo que la vista ya sumo, asi
-- que conviene que esta sea la granularidad mas fina que el tablero necesite.

create or replace view reporting.v_dim_franja as
select g                                                            as franja_id,
       (g * p.franja_minutos)                                       as minuto_del_dia,
       (g * p.franja_minutos) / 60                                  as hora,
       to_char(make_interval(mins => g * p.franja_minutos), 'HH24:MI') as franja_hhmm,
       lpad(((g * p.franja_minutos) / 60)::text, 2, '0') || ':00'    as hora_hhmm,
       (make_time(((g * p.franja_minutos) / 60), ((g * p.franja_minutos) % 60), 0)) as hora_del_dia,
       case
           when (g * p.franja_minutos) / 60 <  5 then 'Madrugada'
           when (g * p.franja_minutos) / 60 < 12 then 'Manana'
           when (g * p.franja_minutos) / 60 < 14 then 'Mediodia'
           when (g * p.franja_minutos) / 60 < 20 then 'Tarde'
           else 'Noche'
       end                                                          as bloque
from reporting.v_parametros p
cross join generate_series(0, (1440 / p.franja_minutos) - 1) g;

comment on view reporting.v_dim_franja is 'Franjas intradiarias de franja_minutos. franja_id = minuto_del_dia / franja_minutos.';
