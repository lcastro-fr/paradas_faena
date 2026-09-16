-- 50_jornada_kpi.sql -- una fila por jornada, con todos los KPIs de la portada.
--
-- Es la ultima vista de la cadena: depende de v_jornada (la ventana), v_parada,
-- v_retencion_fusionada, v_noria_parada, v_velocidad_franja y v_cobertura.
--
-- TRES MEDIDAS DE TIEMPO, Y NO MIDEN LO MISMO:
--   retencion_s            suma la retencion COMPLETA de cada puesto. Mide el impacto
--                          individual y ordena el ranking. Con dos puestos reteniendo a
--                          la vez, excede el tiempo de pared -- a proposito.
--   retencion_fusionada_s  los intervalos de retencion FUSIONADOS, sin doble conteo:
--                          cuanto tiempo hubo ALGUIEN pidiendo la parada.
--   noria_parada_s         cuanto tiempo la noria estuvo REALMENTE detenida, segun
--                          noria_status. Es el unico que sirve para "% de jornada
--                          perdida" y "cabezas perdidas".
--
-- Las dos primeras salen de input_status (los reles) y la tercera del variador. Que no
-- coincidan no es un error de cuadratura: un rele sostenido despues de que la noria
-- rearranca suma retencion y no suma parada. queries/retencion_vs_noria.sql descompone la
-- diferencia puesto por puesto y dice si es un desfasaje parejo o un rele trabado.
--
-- Todo se filtra por en_jornada: un rele olvidado despues del cierre no es demora de faena.

create or replace view reporting.v_jornada_kpi as
with par as (
    select fecha_faena,
           count(*)                                      as paradas,
           sum(duracion_s)                               as retencion_s,
           sum(tiempo_perdido_s)                          as tiempo_perdido_s,
           sum(duracion_bruta_s) - sum(duracion_s)        as descartado_por_hueco_s,
           count(*) filter (where tuvo_hueco)             as paradas_con_hueco,
           max(duracion_s)                                as parada_max_s,
           avg(duracion_s)                                as parada_media_s
    from reporting.v_parada
    where en_jornada
    group by fecha_faena
),
retencion as (
    select fecha_faena,
           sum(duracion_s)                                as retencion_fusionada_s,
           count(*)                                       as tramos_retencion
    from reporting.v_retencion_fusionada
    where en_jornada
    group by fecha_faena
),
noria as (
    -- Ya viene recortada a la ventana de la jornada, no hace falta filtrar.
    select fecha_faena,
           sum(duracion_s)                                as noria_parada_s,
           count(*)                                       as tramos_noria_parada
    from reporting.v_noria_parada
    group by fecha_faena
),
vel as (
    select fecha_faena,
           sum(vel_x_segundos_en_marcha)                  as vel_x_segundos_en_marcha,
           sum(segundos_en_marcha)                        as segundos_en_marcha
    from reporting.v_velocidad_franja
    group by fecha_faena
),
cob as (
    select fecha_faena,
           min(cobertura)                                 as cobertura_peor,
           count(*) filter (where cobertura < 0.98)       as plcs_con_cobertura_baja
    from reporting.v_cobertura
    group by fecha_faena
),
top as (
    -- El puesto que mas demora genero ese dia, para la tarjeta de la portada.
    select distinct on (fecha_faena) fecha_faena, puesto, sum(duracion_s) as duracion_s
    from reporting.v_parada
    where en_jornada
    group by fecha_faena, puesto
    order by fecha_faena, sum(duracion_s) desc, puesto
)
select j.fecha_faena,
       j.hora_inicio,
       j.hora_fin,
       j.inicio_local,
       j.fin_local,
       j.duracion_jornada_s,
       j.cabezas,
       j.garrones,

       -- Ritmo
       round(j.cabezas * 3600 / nullif(j.duracion_jornada_s, 0), 1)          as ritmo_real_cab_h,
       p.objetivo_cabezas_hora                                               as objetivo_cab_h,
       round(j.cabezas * 3600 / nullif(j.duracion_jornada_s, 0)
             / p.objetivo_cabezas_hora, 3)                                   as cumplimiento_ritmo,
       round(vel.vel_x_segundos_en_marcha
             / nullif(vel.segundos_en_marcha, 0), 1)                         as vel_noria_en_marcha_cab_h,

       -- Paradas
       coalesce(par.paradas, 0)                                              as paradas,
       coalesce(par.retencion_s, 0)                                          as retencion_s,
       coalesce(par.tiempo_perdido_s, 0)                                     as tiempo_perdido_s,
       coalesce(retencion.retencion_fusionada_s, 0)                          as retencion_fusionada_s,
       coalesce(retencion.tramos_retencion, 0)                               as tramos_retencion,
       coalesce(noria.noria_parada_s, 0)                                     as noria_parada_s,
       coalesce(noria.tramos_noria_parada, 0)                                as tramos_noria_parada,
       par.parada_max_s,
       round(par.parada_media_s, 1)                                          as parada_media_s,
       top.puesto                                                            as puesto_top,
       top.duracion_s                                                        as puesto_top_s,

       -- Impacto
       -- Sobre la noria realmente detenida, no sobre la retencion: la retencion no es
       -- tiempo perdido de produccion.
       round(coalesce(noria.noria_parada_s, 0)
             / nullif(j.duracion_jornada_s, 0), 4)                           as pct_jornada_perdida,
       round(coalesce(noria.noria_parada_s, 0) / 3600
             * p.objetivo_cabezas_hora, 0)                                   as cabezas_perdidas,
       round(coalesce(par.retencion_s, 0) / 60 / nullif(j.cabezas, 0) * 100, 2)
                                                                             as min_parada_por_100_cab,

       -- Calidad de datos
       cob.cobertura_peor,
       coalesce(cob.plcs_con_cobertura_baja, 0)                              as plcs_con_cobertura_baja,
       coalesce(par.descartado_por_hueco_s, 0)                               as descartado_por_hueco_s,
       coalesce(par.paradas_con_hueco, 0)                                    as paradas_con_hueco
from reporting.v_jornada j
cross join reporting.v_parametros p
left join par   on par.fecha_faena   = j.fecha_faena
left join retencion on retencion.fecha_faena = j.fecha_faena
left join noria     on noria.fecha_faena     = j.fecha_faena
left join vel   on vel.fecha_faena   = j.fecha_faena
left join cob   on cob.fecha_faena   = j.fecha_faena
left join top   on top.fecha_faena   = j.fecha_faena;

comment on view reporting.v_jornada_kpi is
  'KPIs por jornada. retencion_* sale de los reles y noria_parada_s del variador: no miden lo mismo. El impacto va sobre noria_parada_s.';
