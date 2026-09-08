-- Data coverage: how much of a window the daemon was actually reading each PLC. Check
-- this before trusting a duration total -- a low ratio means gaps, and gaps mean the
-- max_segment cap was doing real work.
--
-- Parameters: %(desde)s, %(hasta)s, %(heartbeat)s (interval, HEARTBEAT_SECONDS).

select host(p.ip) as ip,
       p.nombre,
       count(h.ts) as latidos,
       round(count(h.ts)::numeric
             / nullif(extract(epoch from (%(hasta)s::timestamptz - %(desde)s::timestamptz))
                      / extract(epoch from %(heartbeat)s::interval), 0), 3) as cobertura,
       min(h.ts) as primer_latido,
       max(h.ts) as ultimo_latido
from paradas_faena.plcs p
left join paradas_faena.plc_heartbeat h
       on h.ip = p.ip
      and h.ts >= %(desde)s
      and h.ts <  %(hasta)s
group by p.ip, p.nombre
order by p.ip;
