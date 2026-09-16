-- 42_cobertura.sql -- cuanto del dia el daemon estuvo realmente leyendo cada PLC.
--
-- Misma logica que queries/cobertura_datos.sql, con la ventana fijada a la jornada en
-- vez de a un rango arbitrario. Es el chequeo previo a confiar en cualquier total de
-- duracion: con cobertura baja hubo huecos, y huecos significa que el tope max_segment
-- estuvo trabajando -- o sea que hay tiempo de parada real que no se registro.
--
-- La ventana es la jornada y no el dia entero porque el daemon corre continuo pero lo
-- unico que importa es haber estado leyendo mientras se faenaba.
--
-- El cross join a plcs es a proposito: un PLC que no escribio NI UN latido tiene que
-- aparecer con 0, no desaparecer de la grilla.

create or replace view reporting.v_cobertura as
select j.fecha_faena,
       host(pl.ip)                                                           as plc_ip,
       pl.nombre                                                             as plc,
       pl.variador,
       count(h.ts)                                                           as latidos,
       ceil(j.duracion_jornada_s / p.heartbeat_seconds)::int                 as latidos_esperados,
       round(count(h.ts)::numeric
             / nullif(ceil(j.duracion_jornada_s / p.heartbeat_seconds), 0), 3) as cobertura,
       (min(h.ts) at time zone p.tz)                                         as primer_latido,
       (max(h.ts) at time zone p.tz)                                         as ultimo_latido
from reporting.v_jornada j
cross join reporting.v_parametros p
cross join monitoreo_faena.plcs pl
left join monitoreo_faena.plc_heartbeat h
       on h.ip = pl.ip
      and (h.ts at time zone p.tz) >= j.inicio_local
      and (h.ts at time zone p.tz) <  j.fin_local
group by j.fecha_faena, pl.ip, pl.nombre, pl.variador,
         j.duracion_jornada_s, p.heartbeat_seconds, p.tz;

comment on view reporting.v_cobertura is
  'Cobertura de heartbeat por PLC y jornada. Cobertura baja = hay tiempo de parada que no se registro.';
