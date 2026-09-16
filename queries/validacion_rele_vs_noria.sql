-- Validacion: cuanto de cada retencion de un puesto solapa con la noria EN MARCHA.
--
-- POR QUE ESTA CONSULTA EXISTE
-- queries/tiempo_parada_noria_en_marcha.sql parte de que un puesto puede retener su rele
-- mientras la linea ya esta parada por otro motivo, y por eso interseca la retencion con
-- los intervalos running de noria_status. Pero si el pedido de parada detiene la noria
-- casi de inmediato (en planta se reporta una rampa de 4-5 s), esa interseccion no mide
-- el impacto del puesto: mide la rampa.
--
-- COMO LEER EL RESULTADO
--   solape_por_parada_s ~ 4-5 s -> la premisa de la rampa es correcta. El impacto de un
--                                  puesto es su retencion completa, y "noria en marcha"
--                                  no sirve para excluir tiempo ajeno: para eso esta el
--                                  flag en_jornada de reporting.v_parada.
--   pct_solape ~ 1.0            -> la premisa esta invertida: el rele es un aviso y la
--                                  linea sigue andando. Habria que redefinir el KPI de
--                                  impacto antes de seguir.
--   pct_solape intermedio       -> casos mezclados; mirar puesto por puesto, puede haber
--                                  puestos cableados distinto.
--
-- El denominador es la cantidad de PARADAS, no de segmentos: con un re-assert por minuto,
-- una parada larga aporta muchas filas de una sola parada y diluiria la rampa.
--
-- Parametros: %(desde)s, %(hasta)s, %(max_segment)s -- ver tiempo_parada_por_puesto.sql.

with paradas_seg as (
    select cn.name,
           s.value,
           greatest(s.ts, %(desde)s) as ts_start,
           least(coalesce(lead(s.ts) over (partition by s.ip, s.tag order by s.ts),
                          %(hasta)s),
                 %(hasta)s) as ts_end
    from monitoreo_faena.input_status s
    join monitoreo_faena.counters_name cn using (ip, tag, version)
    where s.ts >= %(desde)s - interval '1 hour'
      and s.ts <  %(hasta)s
),
retencion as (
    select name,
           sum(least(ts_end - ts_start, %(max_segment)s)) as t_retencion
    from paradas_seg
    where value
      and ts_end > ts_start
    group by name
),
noria_seg as (
    select n.running,
           greatest(n.ts, %(desde)s) as ts_start,
           least(coalesce(lead(n.ts) over (partition by n.ip order by n.ts),
                          %(hasta)s),
                 %(hasta)s) as ts_end
    from monitoreo_faena.noria_status n
    where n.ts >= %(desde)s - interval '1 hour'
      and n.ts <  %(hasta)s
),
solape as (
    select p.name,
           sum(least(least(p.ts_end, n.ts_end) - greatest(p.ts_start, n.ts_start),
                     %(max_segment)s)) as t_en_marcha
    from paradas_seg p
    join noria_seg n
      on n.running
     and n.ts_start < p.ts_end
     and n.ts_end   > p.ts_start
    where p.value
      and p.ts_end > p.ts_start
    group by p.name
),
marcado as (
    -- Gaps-and-islands, igual que paradas_individuales.sql: hace falta en dos niveles
    -- porque una window function no puede ir dentro del argumento de otra.
    select cn.name, s.ip, s.tag, s.ts, s.value,
           case when s.value and coalesce(lag(s.value) over (partition by s.ip, s.tag
                                                             order by s.ts), false)
                then 0 else 1 end as is_start
    from monitoreo_faena.input_status s
    join monitoreo_faena.counters_name cn using (ip, tag, version)
    where s.ts >= %(desde)s
      and s.ts <  %(hasta)s
),
islas as (
    select name, count(distinct (ip, tag, grp)) as paradas
    from (
        select m.name, m.ip, m.tag, m.value,
               sum(m.is_start) over (partition by m.ip, m.tag order by m.ts) as grp
        from marcado m
    ) g
    where g.value
    group by name
)
select r.name                                                  as puesto,
       r.t_retencion,
       coalesce(s.t_en_marcha, interval '0')                    as t_en_marcha,
       round(extract(epoch from coalesce(s.t_en_marcha, interval '0'))
             / nullif(extract(epoch from r.t_retencion), 0), 4) as pct_solape,
       i.paradas,
       round(extract(epoch from coalesce(s.t_en_marcha, interval '0'))
             / nullif(i.paradas, 0), 2)                         as solape_por_parada_s
from retencion r
left join solape s using (name)
left join islas  i using (name)
order by r.t_retencion desc;
