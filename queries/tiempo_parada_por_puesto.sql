-- Total stop time per workstation over an arbitrary window.
--
--   %(desde)s        timestamptz, inclusive window start
--   %(hasta)s        timestamptz, exclusive window end
--   %(max_segment)s  interval, from Config.max_segment(). Do not hardcode it: it is
--                    derived from REASSERT_SECONDS, and a value below that interval
--                    silently truncates real stop time.
--
-- greatest/least charge a stop straddling a window boundary only for the part inside it.
-- least(segment, max_segment) bounds a data gap: while a relay reads true a row is
-- written every REASSERT_SECONDS, so a longer segment means the daemon was down, and
-- without the cap that downtime would be charged to the workstation.

-- The join carries `version`, so each row matches exactly the configuration it was
-- recorded under. On (ip, tag) alone a row pairs with every version of its puesto, and
-- the segment that survives takes an arbitrary one's name -- time recorded under the
-- old wiring then gets reported under the new puesto's name.
-- The window still partitions by (ip, tag) only: a relay held across a rewiring is one
-- continuous timeline, and the resync row the daemon writes on restart closes the last
-- segment of the old version.

with bounded as (
    select cn.name,
           s.value,
           greatest(s.ts, %(desde)s) as ts_start,
           least(coalesce(lead(s.ts) over (partition by s.ip, s.tag order by s.ts),
                          %(hasta)s),
                 %(hasta)s) as ts_end
    from monitoreo_faena.input_status s
    join monitoreo_faena.counters_name cn using (ip, tag, version)
    -- Reach back so a segment already open at `desde` is seen.
    where s.ts >= %(desde)s - interval '1 hour'
      and s.ts <  %(hasta)s
)
select name,
       sum(least(ts_end - ts_start, %(max_segment)s)) as tiempo_parada,
       count(*)                                       as segmentos
from bounded
where value
  and ts_end > ts_start
group by name
order by tiempo_parada desc;
