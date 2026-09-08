-- Every individual stop, with its duration. Adjacent segments belonging to one
-- continuous stop (a transition plus its re-asserts) are collapsed into a single row.
--
-- A stop is attributed to the window it *started* in: the trailing HAVING drops a stop
-- already in progress at %(desde)s. Use tiempo_parada_por_puesto.sql when you want every
-- second inside the window regardless of when the stop began.
--
-- Parameters: %(desde)s, %(hasta)s, %(max_segment)s.

with seg as (
    select s.ip, s.tag, cn.name, s.value, s.ts,
           lead(s.ts) over (partition by s.ip, s.tag order by s.ts) as next_ts
    from paradas_faena.input_status s
    join paradas_faena.counters_name cn using (ip, tag)
    where s.ts >= %(desde)s - interval '1 hour'
      and s.ts <  %(hasta)s
),
marked as (
    -- A new stop begins where a true segment is not preceded by another true segment.
    select *,
           case when value and coalesce(lag(value) over (partition by ip, tag
                                                         order by ts), false)
                then 0 else 1 end as is_start
    from seg
),
grouped as (
    select *, sum(is_start) over (partition by ip, tag order by ts) as grp
    from marked
)
select name,
       min(ts)                                        as inicio,
       max(coalesce(next_ts, %(hasta)s))              as fin,
       sum(least(coalesce(next_ts, %(hasta)s) - ts,
                 %(max_segment)s))                    as duracion
from grouped
where value
group by ip, tag, name, grp
having min(ts) >= %(desde)s
order by inicio;
