-- 008_parametros_tabla.sql -- v_parametros y v_puesto_config pasan a apoyarse en tablas.
--
-- POR QUE
-- Son las dos vistas base de la capa reporting: v_parametros la lee todo lo demas, y
-- v_puesto_config la lee v_dim_puesto y por lo tanto todos los hechos. Mientras los
-- valores vivian dentro de la definicion de la vista, cambiar UN numero era un
-- CREATE OR REPLACE VIEW, y esa sentencia solo deja sumar columnas al final: intercalar
-- una da 'cannot change name of view column' y la unica salida es un DROP CASCADE que se
-- lleva puesta la capa entera -- y el tablero con ella -- hasta recrear todo en orden.
-- Ya paso al agregar monitor_hora_inicio.
--
-- Con una tabla, cambiar un valor es un UPDATE y no toca ningun objeto:
--
--     update reporting.parametros set monitor_hora_inicio = '05:30';
--
-- LAS VISTAS NO DESAPARECEN. reporting.v_parametros y reporting.v_puesto_config siguen
-- existiendo con exactamente las mismas columnas, en el mismo orden y con los mismos
-- tipos; lo unico que cambia es de donde sacan los valores. Por eso reporting/00 y /01 se
-- vuelven a aplicar con CREATE OR REPLACE y nada aguas abajo se entera.
--
-- ORDEN DE DESPLIEGUE: primero esta migracion, despues reporting/00_parametros.sql y
-- reporting/01_puesto_config.sql. Entre los dos pasos las vistas viejas siguen sirviendo
-- sus valores hardcodeados, asi que el tablero no se cae en el medio.
--
-- Los defaults de abajo son los valores que hoy estan escritos en reporting/00: la
-- migracion tiene que dejar la base comportandose igual que antes de correrla.

-- migrate:up

set local lock_timeout = '5s';

create schema if not exists reporting;

create table reporting.parametros (
    -- Singleton: una fila y nada mas. Sin esto, dos filas duplicarian en silencio cada
    -- hecho del modelo, porque toda la capa hace cross join contra esta vista.
    fila boolean primary key default true
        constraint parametros_una_sola_fila check (fila),

    -- Copiado del .env del daemon. Ver src/paradas_faena/config.py.
    reassert_seconds           numeric not null default 60,
    poll_seconds               numeric not null default 1,
    heartbeat_seconds          numeric not null default 60,
    speed_max_interval_seconds numeric not null default 60,
    max_segment_extra_s        numeric not null default 1,

    -- Proceso
    objetivo_cabezas_hora numeric not null default 220,
    rampa_segundos        numeric not null default 5,
    circuito_minutos      numeric not null default 22.5,
    margen_jornada_min    integer not null default 15,

    -- Presentacion
    franja_minutos integer not null default 15,

    -- Filtros canonicos de produccion.faena
    est_fae text not null default '1920',
    tz      text not null default 'America/Argentina/Buenos_Aires',

    -- Monitor en vivo: desde que hora cuenta la pantalla. Sin hora de corte.
    monitor_hora_inicio time not null default '06:00',

    -- Las mismas invariantes que valida el daemon al arrancar, ahora tambien aca: un
    -- reassert menor al poll hace que max_segment trunque tiempo de parada real, y una
    -- franja que no divide al dia rompe franjas_por_dia.
    constraint parametros_reassert_mayor_al_poll check (reassert_seconds > poll_seconds),
    constraint parametros_poll_positivo          check (poll_seconds > 0),
    constraint parametros_franja_divide_el_dia
        check (franja_minutos > 0 and 1440 % franja_minutos = 0)
);

comment on table reporting.parametros is
  'Constantes del tablero, una sola fila. Se editan con UPDATE; los valores derivados '
  '(max_segment, franjas_por_dia) los calcula reporting.v_parametros.';

insert into reporting.parametros default values;

create table reporting.puesto_config (
    ip      inet    not null,
    tag     varchar not null,
    version integer not null,
    -- 1 para el primer puesto de la linea, N para el de tipificacion. Tiene que quedar
    -- completo y contiguo 1..N: N sale de max(orden_linea) y de ahi se interpola el
    -- offset de cada puesto sobre circuito_minutos.
    orden_linea integer,
    -- Override manual en minutos, si algun puesto no cae donde la interpolacion lo pone.
    offset_min  numeric,

    constraint puesto_config_pk primary key (ip, tag, version),
    -- Lo que la vista solo podia pedir por comentario: un relevamiento con un tag mal
    -- tipeado se rechaza en vez de desaparecer silenciosamente del join de v_dim_puesto.
    constraint puesto_config_counters_name_fk
        foreign key (ip, tag, version)
        references monitoreo_faena.counters_name (ip, tag, version),
    constraint puesto_config_orden_positivo check (orden_linea is null or orden_linea > 0)
);

comment on table reporting.puesto_config is
  'Posicion de cada puesto sobre la linea, cargada a mano. Vacia = todos los offsets en 0.';

-- migrate:down
--
-- Deja la base sin las tablas, asi que las vistas que las leen se van con ellas. Volver
-- atras es esto y despues re-aplicar reporting/*.sql en orden, que es el mismo
-- procedimiento que para ir hacia adelante.

set local lock_timeout = '5s';

drop table if exists reporting.puesto_config cascade;
drop table if exists reporting.parametros cascade;
