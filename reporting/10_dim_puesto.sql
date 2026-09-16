-- 10_dim_puesto.sql -- dimension Puesto.
--
-- La identidad de un puesto es (ip, tag, version) y Power BI no soporta relaciones de
-- mas de una columna, asi que la clave que viaja a los hechos es `puesto_key`.
--
-- `puesto` (el nombre) agrupa entre versiones: es la etiqueta para el ranking. `version`
-- distingue el cableado con el que se grabo cada fila -- ver
-- migrations/006_counters_name_version.sql: recablear un rele es un INSERT de version
-- N+1, y la historia sigue apuntando a N.
--
-- Solo los contadores con input_tag: sin entrada digital no hay tiempo de parada que
-- medir (plc/worker.py:69-74).

create or replace view reporting.v_dim_puesto as
with base as (
    select cn.ip,
           cn.tag,
           cn.version,
           cn.name      as puesto,
           cn.input_tag,
           pl.nombre    as plc,
           pl.variador,
           pc.orden_linea,
           pc.offset_min as offset_override,
           cn.version = max(cn.version) over (partition by cn.ip, cn.tag) as es_version_vigente
    from monitoreo_faena.counters_name cn
    join monitoreo_faena.plcs pl using (ip)
    left join reporting.v_puesto_config pc
           on pc.ip = cn.ip and pc.tag = cn.tag and pc.version = cn.version
    where cn.input_tag is not null
),
linea as (
    -- N = el puesto de tipificacion, el ultimo del circuito. Ver 01_puesto_config.sql.
    select max(orden_linea) as n_puestos from base
)
select host(b.ip) || '|' || b.tag || '|' || b.version as puesto_key,
       b.puesto,
       coalesce(b.puesto, b.tag)                      as puesto_label,
       b.ip,
       host(b.ip)                                     as plc_ip,
       b.plc,
       b.variador,
       b.tag,
       b.input_tag,
       b.version,
       b.es_version_vigente,
       b.orden_linea,
       coalesce(
           b.offset_override,
           case when b.orden_linea is not null and l.n_puestos > 1
                then p.circuito_minutos * (l.n_puestos - b.orden_linea)
                     / (l.n_puestos - 1)::numeric
           end,
           0
       )::numeric                                     as offset_min,
       -- Para el aviso de la pagina de especie/raza y para Calidad de datos.
       (b.offset_override is null and b.orden_linea is null) as offset_sin_configurar
from base b
cross join linea l
cross join reporting.v_parametros p;

comment on view reporting.v_dim_puesto is
  'Dimension Puesto, una fila por (ip, tag, version). puesto_key es la clave para Power BI.';
