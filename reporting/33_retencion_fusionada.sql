-- 33_retencion_fusionada.sql -- tiempo con AL MENOS UN PUESTO pidiendo la parada.
--
-- OJO CON EL NOMBRE: esta vista NO mide el tiempo que la noria estuvo parada. Sale entera
-- de input_status, o sea de los reles: mide cuanto tiempo hubo alguien PIDIENDO la parada.
-- Para el tiempo que la linea estuvo realmente detenida esta reporting.v_noria_parada,
-- que sale de noria_status. Los dos numeros difieren, y cuanto y por que lo dice
-- queries/retencion_vs_noria.sql.
--
-- (Hasta 2026-09-15 esta vista se llamaba v_linea_parada y el tablero la usaba para
-- calcular el porcentaje de jornada perdida. Estaba mal: nunca leyo noria_status.)
--
-- POR QUE HACE FALTA IGUAL
-- La decision del negocio es cobrarle a cada puesto su retencion COMPLETA, tambien cuando
-- dos puestos retienen a la vez. Eso mide el impacto individual de cada puesto, que es lo
-- que el ranking necesita, pero hace que la suma por puesto exceda la retencion real
-- (tests/test_queries.py:75-81). Esta vista es ese otro numero: los intervalos de
-- retencion FUSIONADOS, sin doble conteo entre puestos.

create or replace view reporting.v_retencion_fusionada as
with s as (
    select fecha_faena,
           puesto,
           puesto_key,
           ts_start_local              as a,
           ts_start_local + duracion   as b
    from reporting.v_input_segmento
    where duracion > interval '0'
),
ord as (
    -- El maximo `fin` de todo lo anterior: si el proximo intervalo arranca despues, hay
    -- un corte y empieza un tramo nuevo de linea parada.
    select s.*,
           max(s.b) over (partition by s.fecha_faena order by s.a
                          rows between unbounded preceding and 1 preceding) as fin_previo
    from s
),
marcado as (
    select ord.*, case when ord.fin_previo is null or ord.a > ord.fin_previo then 1 else 0 end as es_nuevo
    from ord
),
agrupado as (
    select marcado.*,
           sum(marcado.es_nuevo) over (partition by marcado.fecha_faena order by marcado.a) as grp
    from marcado
)
select agrupado.fecha_faena,
       grp                                                    as tramo,
       min(a)                                                 as inicio_local,
       max(b)                                                 as fin_local,
       extract(epoch from max(b) - min(a))::numeric           as duracion_s,
       ((extract(hour from min(a)) * 60 + extract(minute from min(a)))::int
         / p.franja_minutos)                                  as franja_id,
       extract(hour from min(a))::int                          as hora,
       count(distinct puesto)                                  as puestos_involucrados,
       string_agg(distinct puesto, ', ' order by puesto)       as puestos,
       -- Mismo criterio que v_parada.en_jornada: un tramo fuera de la ventana de faena
       -- no es demora de produccion.
       (j.fecha_faena is not null
        and min(a) >= j.inicio_margen
        and min(a) <  j.fin_margen)                            as en_jornada
from agrupado
cross join reporting.v_parametros p
left join reporting.v_jornada j on j.fecha_faena = agrupado.fecha_faena
group by agrupado.fecha_faena, grp, p.franja_minutos, j.fecha_faena, j.inicio_margen, j.fin_margen;

comment on view reporting.v_retencion_fusionada is
  'Tiempo con al menos un puesto pidiendo la parada, sin doble conteo. NO es tiempo de noria parada: para eso, v_noria_parada.';
