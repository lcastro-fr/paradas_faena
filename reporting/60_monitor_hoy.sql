-- 60_monitor_hoy.sql -- lo unico que el monitor en vivo le pide a la base.
--
-- El estado de ahora (velocidad, rele de cada puesto, hace cuanto esta pidiendo parada)
-- sale de Redis. De aca salen solo los dos acumulados, que toleran un refresco cada 60 s.
--
-- LA VENTANA ES EL TURNO, NO EL DIA, y arranca en v_parametros.monitor_hora_inicio. Sin
-- corte al final: nadie mira la pantalla despues de las 16. Contar desde medianoche
-- mezcla cosas que no son demora de faena -- el 2026-09-18 un rele quedo apretado de
-- 03:12 a 05:23 y aportaba 7821 s, el 66% del total del dia.
--
-- No se puede usar v_jornada, que seria lo exacto: sale del tipificador y la primera
-- media recien llega ~06:25, asi que no existiria durante la primera media hora.
--
-- Los dos numeros cuentan las paradas que ARRANCARON en la ventana, con su duracion
-- completa: misma atribucion que v_parada.fecha_faena, y asi hablan del mismo conjunto.
-- El precio es que un rele que venia apretado desde antes no suma nada.
--
-- Repite la logica de v_input_segmento en vez de seleccionar de ella porque aquella no
-- tiene predicado de fecha, y Postgres no empuja un `where ts >=` a traves de una window
-- function: reusarla escanearia input_status entera en cada refresco. La paridad la
-- sostiene reporting/verificacion.sql.

create or replace view reporting.v_monitor_hoy as
with ventana as (
    select (date_trunc('day', now() at time zone p.tz)
            + p.monitor_hora_inicio) at time zone p.tz                     as turno_inicio,
           -- No es opcional: sin el, una parada que venia de las 05:50 se veria
           -- arrancando a las 06:00 y se contaria como del turno.
           ((date_trunc('day', now() at time zone p.tz)
             + p.monitor_hora_inicio) at time zone p.tz)
             - interval '1 day'                                            as scan_desde,
           now()                                                           as ahora,
           p.max_segment
    from reporting.v_parametros p
),
raw as (
    select s.ip,
           s.tag,
           s.ts,
           s.value,
           lead(s.ts) over (partition by s.ip, s.tag order by s.ts) as next_ts
    from monitoreo_faena.input_status s
    cross join ventana v
    where s.ts >= v.scan_desde
),
marked as (
    -- Una parada empieza donde un segmento true no viene precedido por otro true.
    select r.*,
           case when r.value and coalesce(lag(r.value) over w, false)
                then 0 else 1 end as is_start
    from raw r
    window w as (partition by r.ip, r.tag order by r.ts)
),
grouped as (
    select m.*,
           sum(m.is_start) over (partition by m.ip, m.tag order by m.ts) as grp
    from marked m
),
islas as (
    select g.ip,
           g.tag,
           g.grp,
           min(g.ts) as inicio,
           -- Mismo tope que v_input_segmento: un segmento mas largo que max_segment no
           -- es una parada larga, es una caida del daemon.
           sum(least(coalesce(g.next_ts, v.ahora) - g.ts, v.max_segment)) as duracion
    from grouped g
    cross join ventana v
    where g.value
    group by g.ip, g.tag, g.grp
),
del_turno as (
    select i.ip,
           i.tag,
           extract(epoch from sum(i.duracion))::numeric as segundos_hoy,
           count(*)                                     as paradas_hoy
    from islas i
    cross join ventana v
    where i.inicio >= v.turno_inicio
    group by i.ip, i.tag
)
-- El left join contra v_dim_puesto es a proposito: un puesto sin una sola parada en el
-- turno tiene que aparecer en cero, no desaparecer de la pantalla.
select d.puesto_key,
       d.puesto_label,
       d.plc,
       d.plc_ip,
       d.tag,
       d.version,
       d.orden_linea,
       coalesce(t.segundos_hoy, 0) as segundos_hoy,
       coalesce(t.paradas_hoy, 0)  as paradas_hoy
from reporting.v_dim_puesto d
left join del_turno t on t.ip = d.ip and t.tag = d.tag
where d.es_version_vigente
order by d.orden_linea nulls last, d.puesto_label;

comment on view reporting.v_monitor_hoy is
  'Acumulados del turno por puesto (desde v_parametros.monitor_hora_inicio) para el '
  'monitor en vivo. El estado de ahora sale de Redis, no de aca.';
