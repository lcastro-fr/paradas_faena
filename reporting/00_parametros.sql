-- 00_parametros.sql -- los valores derivados del tablero.
--
-- LOS VALORES YA NO SE EDITAN ACA: viven en la tabla reporting.parametros (migracion
-- 008) y se cambian con un UPDATE. Antes estaban escritos adentro de esta vista, y eso
-- hacia que tocar un numero fuera un CREATE OR REPLACE VIEW; esa sentencia solo deja
-- sumar columnas al final, asi que intercalar una obligaba a un DROP CASCADE que se
-- llevaba puesta toda la capa reporting.
--
-- Por lo mismo la proyeccion lista las columnas una por una en vez de usar p.*: una
-- columna nueva en la tabla entraria en el medio y correria todas las derivadas. Las
-- nuevas van al final de esta lista.
--
-- reassert/poll/heartbeat/speed tienen que coincidir con el .env del daemon. La base no
-- puede validarlo, asi que la pagina "Calidad de datos" muestra esta vista tal cual.

create or replace view reporting.v_parametros as
select p.reassert_seconds,
       p.poll_seconds,
       p.heartbeat_seconds,
       p.speed_max_interval_seconds,
       p.max_segment_extra_s,
       p.objetivo_cabezas_hora,
       p.rampa_segundos,
       p.circuito_minutos,
       p.margen_jornada_min,
       p.franja_minutos,
       p.est_fae,
       p.tz,
       -- Config.max_segment() = reassert + poll + 1 (src/paradas_faena/config.py).
       -- Mientras un rele lee true el daemon reescribe la fila en el primer tick a los
       -- reassert_seconds o despues, asi que un segmento vivo mide reassert redondeado
       -- hacia arriba al siguiente multiplo de poll. El tope tiene que superar eso: si
       -- no, cada re-assert pierde la diferencia, siempre para abajo.
       make_interval(secs => (p.reassert_seconds + p.poll_seconds
                              + p.max_segment_extra_s)::double precision) as max_segment,
       -- Mismo razonamiento para velocidad: SpeedTracker persiste al menos cada
       -- speed_max_interval_seconds (trackers.py), asi que un hueco mas largo que eso es
       -- una caida y no un valor que haya que ponderar por su duracion.
       make_interval(secs => (p.speed_max_interval_seconds + p.poll_seconds
                              + p.max_segment_extra_s)::double precision) as max_segment_vel,
       make_interval(mins => p.franja_minutos)                            as franja_intervalo,
       (1440 / p.franja_minutos)                                          as franjas_por_dia,
       -- Los mismos topes en segundos: el tipo interval no viaja bien a Power BI, y la
       -- pagina de Calidad de datos los muestra como numero.
       (p.reassert_seconds + p.poll_seconds + p.max_segment_extra_s)       as max_segment_s,
       (p.speed_max_interval_seconds + p.poll_seconds
        + p.max_segment_extra_s)                                          as max_segment_vel_s,
       -- Agregados despues: van al final para no correr las columnas de arriba.
       p.monitor_hora_inicio
from reporting.parametros p;

comment on view reporting.v_parametros is
  'Constantes del tablero (tabla reporting.parametros) mas los valores derivados. '
  'Para cambiar un valor: update reporting.parametros set ...';
