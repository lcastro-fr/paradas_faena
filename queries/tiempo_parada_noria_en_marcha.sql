-- Stop time per workstation, counting only time the line was actually running.
--
-- A workstation can hold its relay while the line is already stopped for another reason;
-- charging that to it overstates its impact. Intersects each input_status segment with
-- the noria_status intervals where the line was running.
--
-- Parameters: %(desde)s, %(hasta)s, %(max_segment)s -- see tiempo_parada_por_puesto.sql.

with paradas_seg as (
    select cn.name,
           s.value,
           greatest(s.ts, %(desde)s) as ts_start,
           least(coalesce(lead(s.ts) over (partition by s.ip, s.tag order by s.ts),
                          %(hasta)s),
                 %(hasta)s) as ts_end
    from paradas_faena.input_status s
    join paradas_faena.counters_name cn using (ip, tag)
    where s.ts >= %(desde)s - interval '1 hour'
      and s.ts <  %(hasta)s
),
noria_seg as (
    select n.running,
           greatest(n.ts, %(desde)s) as ts_start,
           least(coalesce(lead(n.ts) over (partition by n.ip order by n.ts),
                          %(hasta)s),
                 %(hasta)s) as ts_end
    from paradas_faena.noria_status n
    where n.ts >= %(desde)s - interval '1 hour'
      and n.ts <  %(hasta)s
)
select p.name,
       sum(least(least(p.ts_end, n.ts_end) - greatest(p.ts_start, n.ts_start),
                 %(max_segment)s)) as tiempo_parada_en_marcha
from paradas_seg p
join noria_seg n
  on n.running
 and n.ts_start < p.ts_end
 and n.ts_end   > p.ts_start
where p.value
  and p.ts_end > p.ts_start
group by p.name
order by tiempo_parada_en_marcha desc;
