-- 22_jornada.sql -- la ventana de cada jornada de faena.
--
-- Reutiliza produccion.v_summary_faena tal cual (no la duplica): hora_inicio y hora_fin
-- son el primer y el ultimo garron del dia, que es la definicion operativa de "cuando se
-- estuvo faenando". Ojo que esa vista tiene est_fae = '1920' hardcodeado.
--
-- Es la ventana, no los KPIs: a proposito NO depende de las vistas de paradas, porque
-- v_parada la necesita para su flag `en_jornada` y v_jornada_kpi necesita a v_parada.
-- Partirla en dos es lo que evita la dependencia circular.

create or replace view reporting.v_jornada as
select s.fecha                                                as fecha_faena,
       s.hora_inicio,
       s.hora_fin,
       (s.fecha + s.hora_inicio)                              as inicio_local,
       (s.fecha + s.hora_fin)                                  as fin_local,
       -- Paso por el primer puesto un rato antes: el margen solo va al inicio.
       (s.fecha + s.hora_inicio) - make_interval(mins => p.margen_jornada_min)
                                                              as inicio_margen,
       (s.fecha + s.hora_fin)                                 as fin_margen,
       extract(epoch from s.hora_fin - s.hora_inicio)::numeric as duracion_jornada_s,
       s.cabezas,
       s.garrones
from produccion.v_summary_faena s
cross join reporting.v_parametros p;

comment on view reporting.v_jornada is
  'Ventana de la jornada por dia (desde produccion.v_summary_faena). Sin KPIs de parada: ver v_jornada_kpi.';
