-- 01_puesto_config.sql -- posicion de cada puesto a lo largo de la linea.
--
-- POR QUE EXISTE
-- Un garron tarda 20-25 min en completar el circuito, pero produccion.faena.horafaena se
-- registra en un punto fijo (tipificacion, al final de la linea). Atribuir una parada al
-- garron que paso por tipificacion en ese mismo instante puede errar por 22 minutos.
--
-- COMO SE LLENA (relevamiento de planta, una sola vez)
--   orden_linea = 1 para el primer puesto de la linea, N para el ultimo (el de
--   tipificacion, offset 0). Tiene que quedar completo y contiguo 1..N: N se toma como
--   max(orden_linea), y de ahi se interpola el offset de cada puesto sobre
--   circuito_minutos.
--   offset_min = override manual en minutos, si algun puesto no cae donde la
--   interpolacion lineal lo pone.
--
-- Mientras este vacia, todos los offsets valen 0 y la pagina de especie/raza del tablero
-- muestra el aviso de atribucion no confiable. El resto del tablero no depende de esto.
--
-- (ip, tag) tienen que existir en monitoreo_faena.counters_name; `version` es la
-- generacion de configuracion, ver migrations/006_counters_name_version.sql.

create or replace view reporting.v_puesto_config as
select ip, tag, version, orden_linea, offset_min
from (values
    -- Descomentar y completar. Ejemplo:
    -- ('172.30.10.9'::inet, 'Counter0'::varchar, 1, 1::integer, null::numeric),
    -- ('172.30.10.9'::inet, 'Counter1'::varchar, 1, 2::integer, null::numeric),
    (null::inet, null::varchar, null::integer, null::integer, null::numeric)
) as t(ip, tag, version, orden_linea, offset_min)
where ip is not null;

comment on view reporting.v_puesto_config is
  'Orden de cada puesto sobre la linea, cargado a mano. Vacia = todos los offsets en 0.';
