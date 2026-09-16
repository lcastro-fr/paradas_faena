-- 21_dim_produccion.sql -- dimensiones de producto.
--
-- Especie (espvac) y raza son las dos que pidio el negocio. cod_dest / ex_ue / cod_angus
-- / rito quedan deliberadamente afuera: se decidio no modelar "cuota" en este POC. Las
-- columnas siguen en produccion.faena si mas adelante hacen falta.
--
-- Tropa es de alta cardinalidad y solo tiene sentido dentro de un dia, asi que su grano
-- es (fecha_faena, tropa) y no tropa sola.

create or replace view reporting.v_dim_especie as
select distinct especie from reporting.v_produccion_garron where especie is not null;

create or replace view reporting.v_dim_raza as
select distinct raza from reporting.v_produccion_garron where raza is not null;

create or replace view reporting.v_dim_tropa as
with g as (select * from reporting.v_produccion_garron where tropa is not null),
tot as (
    select fecha_faena, tropa, count(*) as garrones,
           min(ts_local) as primer_garron, max(ts_local) as ultimo_garron
    from g group by fecha_faena, tropa
),
esp as (
    select distinct on (fecha_faena, tropa) fecha_faena, tropa, especie, count(*) as c
    from g group by fecha_faena, tropa, especie
    order by fecha_faena, tropa, count(*) desc, especie
),
rz as (
    select distinct on (fecha_faena, tropa) fecha_faena, tropa, raza, count(*) as c
    from g group by fecha_faena, tropa, raza
    order by fecha_faena, tropa, count(*) desc, raza
)
select t.fecha_faena || '|' || t.tropa as tropa_key,
       t.fecha_faena,
       t.tropa,
       t.garrones,
       t.garrones / 2.0                as cabezas,
       t.primer_garron,
       t.ultimo_garron,
       esp.especie                     as especie_dominante,
       rz.raza                         as raza_dominante
from tot t
left join esp using (fecha_faena, tropa)
left join rz  using (fecha_faena, tropa);
