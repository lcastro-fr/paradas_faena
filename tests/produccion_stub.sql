-- produccion_stub.sql -- el schema `produccion` que las vistas de reporting leen.
--
-- No es de este repo: lo alimenta el tipificador y en produccion ya existe. Acá se recrea
-- el mínimo que reporting/*.sql necesita, extraído de schema.sql, para que la capa entera
-- se pueda aplicar y verificar en la base de test.

create schema if not exists produccion;
set local search_path = produccion;

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
