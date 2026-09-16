-- 00_parametros.sql -- el unico lugar donde se editan los numeros del tablero.
--
-- Es una vista y no una tabla a proposito: este POC no crea objetos con estado. Se
-- edita con CREATE OR REPLACE VIEW y se vuelve a correr, igual que el resto.
--
-- El precio de eso es que puede desincronizarse del .env del daemon sin que nada
-- avise, y `max_segment` mal puesto trunca tiempo de parada real en silencio -- ver
-- queries/tiempo_parada_por_puesto.sql:5-8. Por eso la pagina "Calidad de datos" del
-- tablero muestra esta vista tal cual, para que los valores vigentes esten a la vista.
--
-- Al cambiar REASSERT_SECONDS, POLL_SECONDS, HEARTBEAT_SECONDS o
-- SPEED_MAX_INTERVAL_SECONDS en el .env, hay que editar esto tambien.

create schema if not exists reporting;

create or replace view reporting.v_parametros as
select p.*,
       -- Config.max_segment() = reassert + poll + 1 (src/paradas_faena/config.py:133-156).
       -- Mientras un rele lee true el daemon reescribe la fila en el primer tick a los
       -- reassert_seconds o despues, asi que un segmento vivo mide reassert redondeado
       -- hacia arriba al siguiente multiplo de poll. El tope tiene que superar eso: si
       -- no, cada re-assert pierde la diferencia, siempre para abajo.
       make_interval(secs => (p.reassert_seconds + p.poll_seconds
                              + p.max_segment_extra_s)::double precision) as max_segment,
       -- Mismo razonamiento para velocidad: SpeedTracker persiste al menos cada
       -- speed_max_interval_seconds (trackers.py:220-242), asi que un hueco mas largo
       -- que eso es una caida y no un valor que haya que ponderar por su duracion.
       make_interval(secs => (p.speed_max_interval_seconds + p.poll_seconds
                              + p.max_segment_extra_s)::double precision) as max_segment_vel,
       make_interval(mins => p.franja_minutos)                            as franja_intervalo,
       (1440 / p.franja_minutos)                                          as franjas_por_dia,
       -- Los mismos topes en segundos: el tipo interval no viaja bien a Power BI, y la
       -- pagina de Calidad de datos los muestra como numero.
       (p.reassert_seconds + p.poll_seconds + p.max_segment_extra_s)       as max_segment_s,
       (p.speed_max_interval_seconds + p.poll_seconds
        + p.max_segment_extra_s)                                          as max_segment_vel_s
from (
    select
        -- === Copiado del .env de produccion ==========================================
        60::numeric    as reassert_seconds,            -- REASSERT_SECONDS
        1::numeric     as poll_seconds,                -- POLL_SECONDS  (!) ver nota abajo
        60::numeric    as heartbeat_seconds,           -- HEARTBEAT_SECONDS
        60::numeric    as speed_max_interval_seconds,  -- SPEED_MAX_INTERVAL_SECONDS
        1::numeric     as max_segment_extra_s,         -- el "+1.0" de Config.max_segment()

        -- (!) POLL_SECONDS vale 1 en .env pero 0.5 en .env.example y en el default del
        --     codigo. Entra directo en max_segment y por lo tanto en todos los totales
        --     de duracion: confirmar cual corre en produccion.

        -- === Proceso ==================================================================
        220::numeric   as objetivo_cabezas_hora,  -- ritmo objetivo de la linea
        5::numeric     as rampa_segundos,         -- rampa de frenado + arranque de la noria
        22.5::numeric  as circuito_minutos,       -- 20-25 min que tarda un garron en el circuito
        15::integer    as margen_jornada_min,     -- tolerancia para el flag en_jornada

        -- === Presentacion =============================================================
        15::integer    as franja_minutos,         -- granularidad del perfil horario

        -- === Filtros canonicos de produccion.faena ====================================
        '1920'::text   as est_fae,                -- establecimiento propio
        'America/Argentina/Buenos_Aires'::text as tz
) p;

comment on view reporting.v_parametros is
  'Constantes del tablero. Editar aca y volver a correr reporting/*.sql. '
  'reassert/poll/heartbeat/speed deben coincidir con el .env del daemon.';
