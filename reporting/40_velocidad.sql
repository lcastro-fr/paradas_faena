-- 40_velocidad.sql -- velocidad de la noria por franja.
--
-- vel = frec * NORIA_CONV y esta EN CABEZAS/HORA, asi que es directamente comparable
-- contra objetivo_cabezas_hora y contra el ritmo real de v_produccion_hora. Esa
-- comparacion es la que separa dos problemas distintos: si la noria va a 220 y se
-- producen 198, la diferencia son paradas; si la noria va a 190, el problema es la
-- velocidad seteada.
--
-- PROMEDIO PONDERADO POR TIEMPO, no por filas. SpeedTracker persiste una muestra solo
-- cuando la frecuencia cambia mas que la banda muerta, o cada
-- SPEED_MAX_INTERVAL_SECONDS (trackers.py:220-242). Un AVG() plano le da el mismo peso a
-- una muestra que duro 2 s y a una que duro 60 s. Por eso la vista expone
-- `vel_x_segundos` y `segundos` como columnas ADITIVAS y el promedio se arma en DAX:
-- asi el numero sigue siendo correcto al enrollar de franja a hora, dia o mes.
--
-- El tope max_segment_vel acota el hueco por el mismo motivo que max_segment en las
-- paradas: si la ultima muestra tiene mas de un intervalo de refresco de antiguedad, el
-- daemon estuvo caido y esa velocidad no vale por todo el hueco.
--
-- Cada muestra se atribuye a la franja de su INICIO. Una muestra dura como maximo ~61 s,
-- asi que el error en el borde de una franja de 15 min es despreciable.
--
-- vel se castea a numeric antes de ponderar: es una columna aditiva que Power BI va a
-- sumar sobre todo el modelo, y no conviene arrastrar el error de un float por el camino.

create or replace view reporting.v_velocidad_franja as
with s as (
    select v.ts,
           v.vel,
           v.frec,
           v.status_noria,
           lead(v.ts) over (order by v.ts) as next_ts
    from monitoreo_faena.velocidad v
    where v.vel is not null
),
muestras as (
    select (s.ts at time zone p.tz)                                             as ts_local,
           extract(epoch from least(coalesce(s.next_ts, now()) - s.ts,
                                    p.max_segment_vel))::numeric                as segundos,
           s.vel,
           s.frec,
           s.status_noria,
           p.franja_minutos
    from s
    cross join reporting.v_parametros p
)
select ts_local::date                                                            as fecha_faena,
       ((extract(hour from ts_local) * 60
         + extract(minute from ts_local))::int / franja_minutos)                  as franja_id,
       extract(hour from ts_local)::int                                           as hora,
       -- Aditivas: el promedio ponderado se calcula en DAX como
       --   DIVIDE( SUM(vel_x_segundos), SUM(segundos) )
       sum(vel::numeric * segundos)                                               as vel_x_segundos,
       sum(segundos)                                                              as segundos,
       sum(vel::numeric * segundos) filter (where status_noria)                   as vel_x_segundos_en_marcha,
       sum(segundos)       filter (where status_noria)                            as segundos_en_marcha,
       sum(segundos)       filter (where status_noria is not true)                as segundos_detenida,
       (min(vel)  filter (where status_noria))::numeric                            as vel_min_en_marcha,
       (max(vel)  filter (where status_noria))::numeric                            as vel_max_en_marcha,
       (avg(frec) filter (where status_noria))::numeric                            as frec_promedio_en_marcha,
       count(*)                                                                   as muestras
from muestras
group by ts_local::date,
         ((extract(hour from ts_local) * 60 + extract(minute from ts_local))::int / franja_minutos),
         extract(hour from ts_local);

comment on view reporting.v_velocidad_franja is
  'Velocidad de noria (cabezas/hora) por franja, ponderada por tiempo. Promediar como SUM(vel_x_segundos)/SUM(segundos).';
