-- 31_parada.sql -- una fila por parada individual.
--
-- Gaps-and-islands: los segmentos contiguos de una misma retencion (el flanco mas sus
-- re-asserts) se colapsan en una sola parada. Misma logica que
-- queries/paradas_individuales.sql:26-47.
--
-- fecha_faena es el dia en que la parada ARRANCO. Para repartir una parada larga entre
-- franjas usar v_parada_franja, que si la parte.
--
-- ATRIBUCION A PRODUCCION. `horafaena` se registra en tipificacion, al final del
-- circuito, y un garron tarda ~22 min en llegar ahi. Un garron que paso por tipificacion
-- a las T estuvo en un puesto con offset m a las T - m, asi que la parada que arranco en
-- `t` se atribuye a la produccion de la franja que contiene a `t + m`. Con
-- v_puesto_config vacia todos los offsets valen 0 y la atribucion no es confiable: el
-- flag offset_sin_configurar de v_dim_puesto es lo que el tablero muestra como aviso.

create or replace view reporting.v_parada as
with seg as (
    select s.ip, s.tag, s.version, cn.name as puesto, s.value, s.ts,
           lead(s.ts) over (partition by s.ip, s.tag order by s.ts) as next_ts
    from monitoreo_faena.input_status s
    join monitoreo_faena.counters_name cn using (ip, tag, version)
),
marked as (
    -- Una parada empieza donde un segmento true no viene precedido por otro true.
    select seg.*,
           case when seg.value and coalesce(lag(seg.value) over (partition by seg.ip, seg.tag
                                                                 order by seg.ts), false)
                then 0 else 1 end as is_start
    from seg
),
grouped as (
    select marked.*,
           sum(marked.is_start) over (partition by marked.ip, marked.tag order by marked.ts) as grp
    from marked
),
islas as (
    select g.ip,
           g.tag,
           g.grp,
           -- La version (y el nombre) del primer segmento: una parada que atraviesa un
           -- recableado se reporta bajo la configuracion con la que empezo.
           (array_agg(g.version order by g.ts))[1]                        as version,
           (array_agg(g.puesto  order by g.ts))[1]                        as puesto,
           min(g.ts)                                                      as inicio,
           max(coalesce(g.next_ts, now()))                                as fin,
           sum(least(coalesce(g.next_ts, now()) - g.ts, p.max_segment))   as duracion,
           sum(coalesce(g.next_ts, now()) - g.ts)                         as duracion_bruta,
           count(*)                                                      as segmentos,
           bool_or((coalesce(g.next_ts, now()) - g.ts) > p.max_segment)   as tuvo_hueco,
           bool_or(g.next_ts is null)                                     as abierta
    from grouped g
    cross join reporting.v_parametros p
    where g.value
    group by g.ip, g.tag, g.grp
),
local as (
    select i.*,
           host(i.ip) || '|' || i.tag || '|' || i.version as puesto_key,
           (i.inicio at time zone p.tz)                   as inicio_local,
           (i.fin    at time zone p.tz)                   as fin_local,
           (i.inicio at time zone p.tz)::date             as fecha_faena,
           extract(epoch from i.duracion)::numeric        as duracion_s,
           extract(epoch from i.duracion_bruta)::numeric  as duracion_bruta_s,
           p.rampa_segundos,
           p.franja_minutos
    from islas i
    cross join reporting.v_parametros p
),
atribuida as (
    select l.*,
           d.offset_min,
           d.offset_sin_configurar,
           d.plc,
           d.es_version_vigente,
           (l.inicio_local + make_interval(secs => (d.offset_min * 60)::double precision))
                                                          as ts_atribuido
    from local l
    join reporting.v_dim_puesto d on d.puesto_key = l.puesto_key
)
select a.puesto_key || '|' || to_char(a.inicio_local, 'YYYYMMDDHH24MISS') as parada_key,
       a.puesto_key,
       a.puesto,
       a.plc,
       a.version,
       a.es_version_vigente,
       a.fecha_faena,
       a.inicio_local,
       a.fin_local,
       ((extract(hour from a.inicio_local) * 60
         + extract(minute from a.inicio_local))::int / a.franja_minutos) as franja_id,
       extract(hour from a.inicio_local)::int                            as hora,
       a.duracion_s,
       a.duracion_bruta_s,
       -- La noria frena y arranca con una rampa de ~5 s que se pierde ademas de la
       -- retencion en si. Es el impacto real de la parada sobre la produccion.
       a.duracion_s + a.rampa_segundos                                   as tiempo_perdido_s,
       a.segmentos,
       a.tuvo_hueco,
       a.abierta,
       -- Un rele olvidado despues del cierre, o durante el almuerzo, no es demora de
       -- faena. Este flag es lo que evita que corone el ranking.
       (j.fecha_faena is not null
        and a.inicio_local >= j.inicio_margen
        and a.inicio_local <  j.fin_margen)                              as en_jornada,
       -- Atribucion a produccion, corregida por el desfase del circuito.
       a.offset_min,
       a.offset_sin_configurar,
       a.ts_atribuido,
       a.ts_atribuido::date                                              as fecha_atribuida,
       ((extract(hour from a.ts_atribuido) * 60
         + extract(minute from a.ts_atribuido))::int / a.franja_minutos) as franja_atribuida,
       pf.especie_dominante,
       pf.especie_share,
       pf.raza_dominante,
       pf.raza_share,
       pf.tropa_dominante,
       -- La identidad de una tropa es (fecha, tropa): Power BI necesita una sola columna.
       case when pf.tropa_dominante is not null
            then a.ts_atribuido::date || '|' || pf.tropa_dominante end    as tropa_key,
       pf.cabezas                                                        as cabezas_franja_atribuida
from atribuida a
left join reporting.v_jornada j on j.fecha_faena = a.fecha_faena
left join reporting.v_produccion_franja pf
       on pf.fecha_faena = a.ts_atribuido::date
      and pf.franja_id = ((extract(hour from a.ts_atribuido) * 60
                           + extract(minute from a.ts_atribuido))::int / a.franja_minutos);

comment on view reporting.v_parada is
  'Una fila por parada individual, con duracion topeada, flag en_jornada y atribucion de produccion.';
