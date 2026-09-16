-- Producciones
create table faena
(
    tarjetaf    char(12) not null
        primary key,
    seqfae      char(3),
    tropa       text,
    pesjnc      real,
    espvac      char(3),
    confor      char,
    ggor        smallint,
    desjnc      text,
    marcaido    text,
    marcasell   text,
    codcal      text,
    nrousu      smallint,
    usuver      smallint,
    est_fae     char(4),
    fnrousu     smallint,
    fusuver     smallint,
    ph          numeric(5, 2),
    chip        text,
    raza        text,
    cod_dest    text,
    piezas      numeric(4, 1),
    fechaf      date,
    anio        smallint,
    numero      text,
    hccp1       smallint,
    horafaena   time,
    cod_et      text,
    marca       smallint,
    ex_ue       smallint,
    golpes      text,
    contucion   text,
    cod_angus   text,
    rito        text,
    dta         text,
    garron      smallint generated always as (("substring"((tarjetaf)::text, 9, 4))::smallint) stored,
    is_parcial  boolean generated always as ((("substring"((tarjetaf)::text, 9, 4))::smallint >= 9000)) stored,
    is_decomiso boolean generated always as ((desjnc = 'XD'::text)) stored
);

alter table faena
    owner to lcingolani;

create index idx_timestamp_faena
    on faena (fechaf, horafaena);

create index idx_establecimiento_faena
    on faena (est_fae);

grant select on faena to produccion_role_ro;

grant delete, insert, truncate, update on faena to produccion_role_rw;

create table caravana_garron
(
    tarjeta    char(12) not null
        primary key,
    fecha      date     not null,
    caravana   varchar(40),
    manual     boolean   default false,
    created_at timestamp default CURRENT_TIMESTAMP,
    updated_at timestamp default CURRENT_TIMESTAMP,
    procesado  boolean   default false
);

alter table caravana_garron
    owner to admin;

grant select on caravana_garron to produccion_role_ro;

grant delete, insert, truncate, update on caravana_garron to produccion_role_rw;

create view v_inspecciones_faena(fecha_faena, garron, hora_real, hora_teorica, t_inspeccion_m) as
WITH garrones AS MATERIALIZED (SELECT f.fechaf,
                                      f.tarjetaf,
                                      f.garron,
                                      f.horafaena
                               FROM produccion.faena f
                               WHERE 1 = 1
                                 AND f.est_fae = '1920'::bpchar
                                 AND f.is_parcial IS FALSE
                                 AND f.is_decomiso IS FALSE),
     secuencias AS MATERIALIZED (SELECT garrones.fechaf,
                                        garrones.garron,
                                        garrones.horafaena,
                                        max(garrones.garron)
                                        OVER (PARTITION BY garrones.fechaf ORDER BY garrones.fechaf, garrones.horafaena, garrones.garron ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS last_sq
                                 FROM garrones),
     saltos_positivos AS (SELECT s_1.fechaf,
                                 s_1.garron,
                                 s_1.last_sq,
                                 s_1.last_sq + 1 AS desde,
                                 s_1.garron - 1  AS hasta,
                                 g.horafaena     AS hora_teorica
                          FROM secuencias s_1
                                   JOIN garrones g ON s_1.fechaf = g.fechaf AND g.garron = s_1.last_sq
                          WHERE (s_1.garron - s_1.last_sq) > 1)
SELECT s.fechaf                                                        AS fecha_faena,
       s.garron,
       s.horafaena                                                     AS hora_real,
       sp.hora_teorica,
       EXTRACT(epoch FROM s.horafaena - sp.hora_teorica) / 60::numeric AS t_inspeccion_m
FROM secuencias s
         JOIN saltos_positivos sp ON sp.fechaf = s.fechaf AND sp.desde <= s.garron AND sp.hasta >= s.garron
WHERE (s.garron - s.last_sq) < 0;

alter table v_inspecciones_faena
    owner to admin;

create view v_summary_faena(fecha, hora_inicio, hora_fin, cabezas, garrones) as
SELECT fechaf         AS fecha,
       min(horafaena) AS hora_inicio,
       max(horafaena) AS hora_fin,
       count(*) / 2   AS cabezas,
       count(*)       AS garrones
FROM produccion.faena
WHERE is_parcial IS FALSE
  AND is_decomiso IS FALSE
  AND est_fae = '1920'::bpchar
GROUP BY fechaf
ORDER BY fechaf DESC;

alter table v_summary_faena
    owner to admin;

create function set_updated_at() returns trigger
    language plpgsql
as
$$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

alter function set_updated_at() owner to admin;



-- Monitoreo Faena

create sequence tiempos_parada_id_seq
    as integer;

alter sequence tiempos_parada_id_seq owner to daemons;

create table schema_migrations
(
    version varchar not null
        primary key
);

alter table schema_migrations
    owner to daemons;

create table plcs
(
    ip       inet                  not null
        primary key,
    nombre   text,
    variador boolean default false not null
);

alter table plcs
    owner to daemons;

create table counters_name
(
    ip        inet              not null
        constraint plcs_fk
            references plcs,
    tag       varchar           not null,
    name      varchar,
    input_tag varchar,
    version   integer default 1 not null,
    constraint counters_name_pk
        primary key (ip, tag, version)
);

alter table counters_name
    owner to daemons;

create unique index counters_name_input_tag_uq
    on counters_name (ip, input_tag, version)
    where (input_tag IS NOT NULL);

create table paradas
(
    id           serial
        constraint paradas_pk
            primary key,
    ip           inet
        constraint plcs_fk
            references plcs,
    tag          varchar,
    old_value    bigint,
    new_value    bigint,
    dif          bigint,
    status_noria boolean,
    vel          real,
    event_uid    uuid,
    version      integer                  not null,
    ts           timestamp with time zone not null,
    constraint counters_name_fk
        foreign key (ip, tag, version) references counters_name
);

alter table paradas
    owner to daemons;

create unique index paradas_event_uid_uq
    on paradas (event_uid);

create index idx_paradas_counter
    on paradas (ip, tag, version);

create index idx_paradas_range
    on paradas (ts)
    where (status_noria IS NOT FALSE);

create table velocidad
(
    id           serial,
    frec         real,
    vel          real,
    status_noria boolean,
    event_uid    uuid,
    ts           timestamp with time zone not null
);

alter table velocidad
    owner to daemons;

create unique index velocidad_event_uid_uq
    on velocidad (event_uid);

create index idx_velocidad_ts
    on velocidad (ts);

create table input_status
(
    ip      inet                     not null,
    tag     varchar                  not null,
    ts      timestamp with time zone not null,
    value   boolean                  not null,
    version integer                  not null,
    constraint input_status_pk
        primary key (ip, tag, ts),
    constraint input_status_counters_name_fk
        foreign key (ip, tag, version) references counters_name
);

alter table input_status
    owner to daemons;

create index idx_input_status_ts
    on input_status (ts);

create index idx_input_status_tag_ts
    on input_status (ip, tag, ts);

create index idx_input_status_counter
    on input_status (ip, tag, version);

create table noria_status
(
    ip      inet                     not null,
    ts      timestamp with time zone not null,
    running boolean                  not null,
    constraint noria_status_pk
        primary key (ip, ts)
);

alter table noria_status
    owner to daemons;

create table plc_heartbeat
(
    ip inet                     not null,
    ts timestamp with time zone not null,
    constraint plc_heartbeat_pk
        primary key (ip, ts)
);

alter table plc_heartbeat
    owner to daemons;

create view v_t_parada_puesto(puesto, inicio, fin, duracion) as
WITH seg AS (SELECT s.ip,
                    s.tag,
                    cn.name,
                    s.value,
                    s.ts,
                    lead(s.ts) OVER (PARTITION BY s.ip, s.tag ORDER BY s.ts) AS next_ts
             FROM monitoreo_faena.input_status s
                      JOIN monitoreo_faena.counters_name cn USING (ip, tag, version)),
     marked AS (SELECT seg.ip,
                       seg.tag,
                       seg.name,
                       seg.value,
                       seg.ts,
                       seg.next_ts,
                       CASE
                           WHEN seg.value AND
                                COALESCE(lag(seg.value) OVER (PARTITION BY seg.ip, seg.tag ORDER BY seg.ts), false)
                               THEN 0
                           ELSE 1
                           END AS is_start
                FROM seg),
     grouped AS (SELECT marked.ip,
                        marked.tag,
                        marked.name,
                        marked.value,
                        marked.ts,
                        marked.next_ts,
                        marked.is_start,
                        sum(marked.is_start) OVER (PARTITION BY marked.ip, marked.tag ORDER BY marked.ts) AS grp
                 FROM marked)
SELECT name                                                               AS puesto,
       min(ts)                                                            AS inicio,
       max(COALESCE(next_ts, CURRENT_TIMESTAMP))                          AS fin,
       EXTRACT(epoch FROM sum(COALESCE(next_ts, CURRENT_TIMESTAMP) - ts)) AS duracion
FROM grouped
WHERE value
GROUP BY ip, tag, name, grp
ORDER BY (min(ts));

alter table v_t_parada_puesto
    owner to admin;

create view v_t_parada_puesto_agg(puesto, fecha, tiempo_parada, segmentos) as
WITH bounded AS (SELECT cn.name,
                        s.value,
                        s.ts                                                                                  AS ts_start,
                        COALESCE(lead(s.ts) OVER (PARTITION BY s.ip, s.tag ORDER BY s.ts), CURRENT_TIMESTAMP) AS ts_end
                 FROM monitoreo_faena.input_status s
                          JOIN monitoreo_faena.counters_name cn USING (ip, tag, version))
SELECT name                                       AS puesto,
       ts_start::date                             AS fecha,
       EXTRACT(epoch FROM sum(ts_end - ts_start)) AS tiempo_parada,
       count(*)                                   AS segmentos
FROM bounded
WHERE value
  AND ts_end > ts_start
GROUP BY name, (ts_start::date)
ORDER BY (EXTRACT(epoch FROM sum(ts_end - ts_start))) DESC;

alter table v_t_parada_puesto_agg
    owner to admin;
