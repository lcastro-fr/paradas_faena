-- 35_noria_parada.sql -- tiempo en que la noria estuvo REALMENTE detenida.
--
-- Esto es lo que v_retencion_fusionada NO es. Aquella sale entera de input_status: mide
-- cuanto tiempo hubo al menos un puesto PIDIENDO la parada. Esta sale de noria_status, que
-- es el estado que reporta el variador: mide cuanto tiempo la noria estuvo efectivamente
-- detenida. No son lo mismo y en datos reales difieren bastante -- ver
-- queries/retencion_vs_noria.sql, que descompone la diferencia puesto por puesto.
--
-- Para "% de jornada perdida" y "cabezas perdidas" hay que usar ESTA, no la retencion.
--
-- DOS SUTILEZAS DEL CALCULO
--
-- 1. noria_status guarda solo FLANCOS (NoriaStatusTracker emite unicamente cuando el valor
--    cambia). Por eso el lead() se calcula SIN filtrar por fecha: el estado vigente al
--    abrir la jornada puede venir de un flanco de horas antes, y filtrar primero lo
--    perderia. Recien despues se recorta a la ventana.
--
-- 2. No hay re-assert que acote un hueco, como si lo hay en input_status. Si el daemon
--    estuvo caido no hay flancos, y el ultimo estado conocido se estira por todo el hueco.
--    Contra eso no hay tope posible: "sin flancos" y "sin cambios" son indistinguibles.
--    Mirar reporting.v_cobertura antes de confiar en un total de esta vista.

create or replace view reporting.v_noria_parada as
with segmentos as (
    select n.ip,
           n.running,
           n.ts,
           lead(n.ts) over (partition by n.ip order by n.ts) as next_ts
    from monitoreo_faena.noria_status n
),
local as (
    select s.ip,
           s.running,
           (s.ts at time zone p.tz)                       as inicio,
           (coalesce(s.next_ts, now()) at time zone p.tz) as fin
    from segmentos s
    cross join reporting.v_parametros p
),
recortado as (
    select j.fecha_faena,
           l.ip,
           l.running,
           greatest(l.inicio, j.inicio_margen) as inicio,
           least(l.fin, j.fin_margen)          as fin
    from local l
    join reporting.v_jornada j
      on l.inicio < j.fin_margen
     and l.fin    > j.inicio_margen
),
detenida as (
    select fecha_faena, ip, inicio, fin
    from recortado
    where not running
      and fin > inicio
),
-- Fusionado por si alguna vez hubiera mas de un PLC escribiendo el estado.
ord as (
    select d.*,
           max(d.fin) over (partition by d.fecha_faena order by d.inicio
                            rows between unbounded preceding and 1 preceding) as fin_previo
    from detenida d
),
agrupado as (
    select ord.*,
           sum(case when ord.fin_previo is null or ord.inicio > ord.fin_previo then 1 else 0 end)
               over (partition by ord.fecha_faena order by ord.inicio) as tramo
    from ord
)
select a.fecha_faena,
       a.tramo,
       min(a.inicio)                                            as inicio_local,
       max(a.fin)                                               as fin_local,
       extract(epoch from max(a.fin) - min(a.inicio))::numeric  as duracion_s,
       ((extract(hour   from min(a.inicio)) * 60
         + extract(minute from min(a.inicio)))::int / p.franja_minutos) as franja_id,
       extract(hour from min(a.inicio))::int                    as hora
from agrupado a
cross join reporting.v_parametros p
group by a.fecha_faena, a.tramo, p.franja_minutos;

comment on view reporting.v_noria_parada is
  'Tramos en que la noria estuvo realmente detenida, segun noria_status. Es el numero para % de jornada perdida.';
