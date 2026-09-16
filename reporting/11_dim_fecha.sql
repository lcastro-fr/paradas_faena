-- 11_dim_fecha.sql -- calendario, desde el primer dia con faena hasta hoy.
--
-- Vive en la base y no en DAX para que las mismas etiquetas en castellano las vea
-- cualquier consumidor, no solo Power BI. `es_dia_faena` distingue un dia sin faena de
-- un dia con faena y cero paradas -- son cosas distintas y el tablero no debe promediarlas.

create or replace view reporting.v_dim_fecha as
with p as (select * from reporting.v_parametros),
dias_faena as (
    select distinct f.fechaf as fecha
    from produccion.faena f
    cross join p
    where f.est_fae = p.est_fae
      and f.is_parcial is false
      and f.is_decomiso is false
),
rango as (
    select min(fecha) as desde, greatest(max(fecha), current_date) as hasta from dias_faena
)
select d::date                                                     as fecha,
       extract(year  from d)::int                                  as anio,
       extract(month from d)::int                                   as mes,
       (array['enero','febrero','marzo','abril','mayo','junio','julio','agosto',
              'septiembre','octubre','noviembre','diciembre'])[extract(month from d)::int]
                                                                    as mes_nombre,
       to_char(d, 'YYYY-MM')                                        as anio_mes,
       extract(isoyear from d)::int                                 as anio_iso,
       extract(week   from d)::int                                  as semana_iso,
       extract(isodow from d)::int                                  as dia_semana,
       (array['lunes','martes','miercoles','jueves','viernes','sabado','domingo'])
              [extract(isodow from d)::int]                         as dia_semana_nombre,
       extract(isodow from d)::int >= 6                             as es_fin_de_semana,
       exists (select 1 from dias_faena df where df.fecha = d::date) as es_dia_faena
from rango
cross join generate_series(rango.desde, rango.hasta, interval '1 day') d;

comment on view reporting.v_dim_fecha is 'Calendario. es_dia_faena separa "sin faena" de "faena sin paradas".';
