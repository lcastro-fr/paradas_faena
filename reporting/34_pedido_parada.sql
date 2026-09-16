-- 34_pedido_parada.sql -- cantidad de pedidos de parada por puesto y hora.
--
-- monitoreo_faena.paradas cuenta EVENTOS, no duracion: `dif` es cuanto se movio el
-- contador del PLC entre dos lecturas (normalmente 1; mas de 1 significa varias
-- pulsaciones dentro de un mismo intervalo de poll). Ver plc/trackers.py:40-82.
--
-- `status_noria is not false` -- no `is true` -- porque el flag es NULL cuando la lectura
-- del variador tiene mas de STATUS_GRACE_SECONDS de antiguedad o el PLC del variador
-- esta caido (state.py:38-46). Descartar los NULL perderia pedidos reales. Es el mismo
-- criterio del indice idx_paradas_range.
--
-- Sirve para contrastar contra la cantidad de islas de v_parada: si difieren mucho, o el
-- contador del PLC y el rele no estan cableados al mismo puesto, o hubo pulsaciones que
-- no llegaron a retener la linea. La pagina de Calidad de datos muestra esa diferencia.
--
-- El join a v_dim_puesto deja afuera los contadores sin input_tag (no son puestos con
-- tiempo medible), y por eso pueden faltar pedidos de un contador no mapeado.

create or replace view reporting.v_pedido_parada as
select d.puesto_key,
       d.puesto,
       d.plc,
       pa.version,
       (pa.ts at time zone p.tz)::date                                as fecha_faena,
       extract(hour from (pa.ts at time zone p.tz))::int              as hora,
       ((extract(hour   from (pa.ts at time zone p.tz)) * 60
         + extract(minute from (pa.ts at time zone p.tz)))::int
        / p.franja_minutos)                                           as franja_id,
       sum(pa.dif)                                                    as pedidos,
       count(*)                                                       as eventos,
       count(*) filter (where pa.status_noria is null)                as eventos_status_desconocido,
       avg(pa.vel)                                                    as vel_promedio
from monitoreo_faena.paradas pa
join reporting.v_dim_puesto d
  on d.ip = pa.ip and d.tag = pa.tag and d.version = pa.version
cross join reporting.v_parametros p
where pa.status_noria is not false
group by d.puesto_key, d.puesto, d.plc, pa.version,
         (pa.ts at time zone p.tz)::date,
         extract(hour from (pa.ts at time zone p.tz)),
         ((extract(hour from (pa.ts at time zone p.tz)) * 60
           + extract(minute from (pa.ts at time zone p.tz)))::int / p.franja_minutos);

comment on view reporting.v_pedido_parada is
  'Pedidos de parada (eventos de contador) por puesto y hora. Contraste contra la cantidad de paradas.';
