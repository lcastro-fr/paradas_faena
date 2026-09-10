create schema if not exists monitoreo_faena;

create table monitoreo_faena.destinatarios_reporte
(
    email  varchar not null
        constraint destinatarios_reporte_pk
            primary key,
    active boolean default true
);

create table monitoreo_faena.generales
(
    fecha       date not null
        constraint generales_pk
            primary key,
    hora_inicio time,
    hora_fin    time,
    registros   bigint
);


create index idx_generales_range
    on monitoreo_faena.generales (fecha);

create table monitoreo_faena.inspeccion
(
    fecha        date    not null,
    garron       integer not null,
    hora_teorica time,
    hora_real    time,
    constraint inspeccion_pk
        primary key (fecha, garron)
);


create index idx_inspeccion_fecha
    on monitoreo_faena.inspeccion (fecha);

create table monitoreo_faena.plcs
(
    ip     inet not null
        primary key,
    nombre text
);

create table monitoreo_faena.counters_name
(
    ip          inet    not null
        constraint plcs_fk
            references monitoreo_faena.plcs,
    tag         varchar not null,
    name        varchar,
    orden_array integer,
    constraint counters_name_pk
        primary key (ip, tag)
);

create table monitoreo_faena.paradas
(
    id           serial
        constraint paradas_pk
            primary key,
    ip           inet
        constraint plcs_fk
            references monitoreo_faena.plcs,
    tag          varchar,
    old_value    bigint,
    new_value    bigint,
    dif          bigint,
    fecha        date default CURRENT_DATE,
    hora         time default CURRENT_TIME,
    status_noria boolean,
    vel          real,
    constraint counters_name_fk
        foreign key (ip, tag) references monitoreo_faena.counters_name
);

create index idx_paradas_range
    on monitoreo_faena.paradas (fecha, hora)
    where (status_noria IS NOT FALSE);

create table monitoreo_faena.velocidad
(
    id           serial,
    fecha        date default CURRENT_DATE,
    hora         time default CURRENT_TIME,
    frec         real,
    vel          real,
    status_noria boolean
);

create index idx_velocidad_fecha
    on monitoreo_faena.velocidad (fecha);

create index idx_velocidad_hora
    on monitoreo_faena.velocidad (hora);

create table monitoreo_faena.tiempos_parada
(
    id     serial,
    fecha  date,
    hora   time,
    name   varchar,
    tiempo integer
);

create unique index tiempos_parada_idx
    on monitoreo_faena.tiempos_parada (fecha, name);
