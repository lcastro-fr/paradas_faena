-- 41_inspeccion.sql -- garrones desviados a inspeccion.
--
-- Envuelve produccion.v_inspecciones_faena, que ya hace el trabajo dificil: detecta el
-- salto en la secuencia de garrones (un garron que se va a inspeccion deja un hueco y
-- vuelve mas tarde, fuera de orden) y mide hora_real contra hora_teorica.
--
-- t_inspeccion_m negativo o absurdamente grande no es un tiempo de inspeccion: es la
-- heuristica de secuencia fallando (un garron reingresado a mano, un salto de numeracion).
-- Se marcan como outlier en vez de descartarlos en silencio, para que el tablero pueda
-- filtrarlos y para que se vea cuantos son.

create or replace view reporting.v_inspeccion as
select i.fecha_faena,
       i.garron,
       i.hora_real,
       i.hora_teorica,
       (i.fecha_faena + i.hora_real)                                as inicio_local,
       (i.fecha_faena + i.hora_teorica)                             as teorico_local,
       ((extract(hour from i.hora_real) * 60
         + extract(minute from i.hora_real))::int / p.franja_minutos) as franja_id,
       extract(hour from i.hora_real)::int                           as hora,
       i.t_inspeccion_m,
       (i.t_inspeccion_m < 0 or i.t_inspeccion_m > 240)              as es_outlier
from produccion.v_inspecciones_faena i
cross join reporting.v_parametros p;

comment on view reporting.v_inspeccion is
  'Garrones a inspeccion con su demora, desde produccion.v_inspecciones_faena. es_outlier marca la heuristica fallando.';
