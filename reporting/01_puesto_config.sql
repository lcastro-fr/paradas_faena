-- 01_puesto_config.sql -- posicion de cada puesto a lo largo de la linea.
--
-- Los datos van a la tabla reporting.puesto_config (migracion 008), que ademas tiene una
-- foreign key contra counters_name: un tag mal tipeado en el relevamiento se rechaza en
-- vez de desaparecer en silencio del join de v_dim_puesto.
--
-- POR QUE EXISTE: un garron tarda 20-25 min en completar el circuito, pero horafaena se
-- registra en tipificacion, al final de la linea. Atribuir una parada al garron que paso
-- por ahi en ese mismo instante puede errar por 22 minutos.
--
-- orden_linea va 1..N, N = tipificacion. Tiene que quedar contiguo: N sale de
-- max(orden_linea) y de ahi se interpola el offset de cada puesto sobre circuito_minutos.
-- Vacia = todos los offsets en 0 y el tablero muestra el aviso de atribucion no confiable.

create or replace view reporting.v_puesto_config as
select ip, tag, version, orden_linea, offset_min
from reporting.puesto_config;

comment on view reporting.v_puesto_config is
  'Orden de cada puesto sobre la linea (tabla reporting.puesto_config). Vacia = todos los offsets en 0.';
