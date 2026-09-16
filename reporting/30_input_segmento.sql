-- 30_input_segmento.sql -- el corazon del calculo: un segmento de retencion, topeado.
--
-- input_status.value = true significa "el puesto esta pidiendo parada". Ya viene
-- normalizado: los reles son normalmente cerrados y el PLC lee 1 con el puesto libre,
-- pero InputTracker invierte la senal antes de escribir (plc/trackers.py:91-95, :142).
-- Por eso todo lo que sigue suma `where value`.
--
-- EL TOPE (max_segment) NO ES OPCIONAL. Mientras un rele lee true el daemon reescribe la
-- fila cada REASSERT_SECONDS, asi que un segmento mas largo que eso no es una parada
-- larga: es una caida del daemon. Sin `least(..., max_segment)` ese hueco se le cobra
-- entero al puesto. Es exactamente el defecto de las vistas v_t_parada_puesto* que hay
-- hoy en la base. Ver queries/tiempo_parada_por_puesto.sql:9-12.
--
-- El join lleva `version` para que cada fila se lea con la configuracion con la que se
-- grabo, pero la window function particiona solo por (ip, tag): un rele retenido durante
-- un recableado es una linea de tiempo continua, y la fila `resync` que el daemon escribe
-- al reiniciar cierra el ultimo segmento de la version vieja.
-- Ver migrations/006_counters_name_version.sql y tests/test_queries.py:383-391.

create or replace view reporting.v_input_segmento as
with seg as (
    select s.ip,
           s.tag,
           s.version,
           cn.name as puesto,
           s.value,
           s.ts,
           lead(s.ts) over (partition by s.ip, s.tag order by s.ts) as next_ts
    from monitoreo_faena.input_status s
    join monitoreo_faena.counters_name cn using (ip, tag, version)
)
select host(seg.ip) || '|' || seg.tag || '|' || seg.version   as puesto_key,
       seg.ip,
       seg.tag,
       seg.version,
       seg.puesto,
       (seg.ts at time zone p.tz)                             as ts_start_local,
       (seg.ts at time zone p.tz)::date                       as fecha_faena,
       -- Duracion topeada: la que hay que sumar.
       least(coalesce(seg.next_ts, now()) - seg.ts, p.max_segment)      as duracion,
       -- Sin topear: solo para medir cuanto trabajo hizo el tope.
       (coalesce(seg.next_ts, now()) - seg.ts)                          as duracion_bruta,
       (coalesce(seg.next_ts, now()) - seg.ts) > p.max_segment          as es_hueco,
       seg.next_ts is null                                              as abierto
from seg
cross join reporting.v_parametros p
where seg.value;

comment on view reporting.v_input_segmento is
  'Segmentos de retencion con el tope max_segment aplicado. Base de v_parada y v_parada_franja.';
