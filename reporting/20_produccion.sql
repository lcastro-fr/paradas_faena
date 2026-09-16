-- 20_produccion.sql -- produccion desde produccion.faena.
--
-- produccion.faena tiene UNA FILA POR MEDIA RES: cabezas = garrones / 2. Es lo que hace
-- produccion.v_summary_faena con count(*)/2, y equivocarse en ese factor duplica o
-- divide a la mitad todos los ritmos del tablero.
--
-- Filtros canonicos, los mismos de v_summary_faena y v_inspecciones_faena:
--   est_fae = <parametro>, is_parcial is false, is_decomiso is false.
--   is_parcial y is_decomiso son columnas generadas (garron >= 9000 y desjnc = 'XD').
--
-- horafaena se registra en un punto fijo de la linea (tipificacion). Todo lo que dependa
-- de "que se estaba procesando en el puesto X" tiene que corregir por el desfase del
-- circuito -- ver reporting/01_puesto_config.sql y v_parada.franja_atribuida.

create or replace view reporting.v_produccion_garron as
select f.tarjetaf,
       f.fechaf                                     as fecha_faena,
       f.horafaena,
       (f.fechaf + f.horafaena)                     as ts_local,
       ((extract(hour from f.horafaena) * 60
         + extract(minute from f.horafaena))::int / p.franja_minutos) as franja_id,
       extract(hour from f.horafaena)::int          as hora,
       f.garron,
       f.tropa,
       f.espvac                                     as especie,
       f.raza,
       f.seqfae,
       f.est_fae
from produccion.faena f
cross join reporting.v_parametros p
where f.est_fae = p.est_fae
  and f.is_parcial is false
  and f.is_decomiso is false
  and f.horafaena is not null;

comment on view reporting.v_produccion_garron is
  'Una fila por media res faenada (grano de produccion.faena), con hora local y franja.';


-- Mix por franja. `*_dominante` es la categoria mas frecuente de la franja y `share` que
-- tan homogenea es: con un share bajo, atribuir una parada a esa especie no dice mucho, y
-- el tablero puede filtrar por share minimo.
create or replace view reporting.v_produccion_franja as
with g as (select * from reporting.v_produccion_garron),
tot as (
    select fecha_faena, franja_id, count(*) as garrones,
           min(ts_local) as primer_garron, max(ts_local) as ultimo_garron
    from g group by fecha_faena, franja_id
),
esp as (
    select distinct on (fecha_faena, franja_id)
           fecha_faena, franja_id, especie, count(*) as c
    from g group by fecha_faena, franja_id, especie
    order by fecha_faena, franja_id, count(*) desc, especie
),
rz as (
    select distinct on (fecha_faena, franja_id)
           fecha_faena, franja_id, raza, count(*) as c
    from g group by fecha_faena, franja_id, raza
    order by fecha_faena, franja_id, count(*) desc, raza
),
tr as (
    select distinct on (fecha_faena, franja_id)
           fecha_faena, franja_id, tropa, count(*) as c
    from g group by fecha_faena, franja_id, tropa
    order by fecha_faena, franja_id, count(*) desc, tropa
)
select t.fecha_faena,
       t.franja_id,
       t.garrones,
       t.garrones / 2.0                            as cabezas,
       t.primer_garron,
       t.ultimo_garron,
       esp.especie                                 as especie_dominante,
       round(esp.c::numeric / t.garrones, 3)       as especie_share,
       rz.raza                                     as raza_dominante,
       round(rz.c::numeric / t.garrones, 3)        as raza_share,
       tr.tropa                                    as tropa_dominante,
       round(tr.c::numeric / t.garrones, 3)        as tropa_share
from tot t
left join esp using (fecha_faena, franja_id)
left join rz  using (fecha_faena, franja_id)
left join tr  using (fecha_faena, franja_id);

comment on view reporting.v_produccion_franja is
  'Produccion y mix dominante por franja. Es la vista contra la que se atribuyen las paradas.';


-- Por hora. `ritmo_cab_h` se calcula sobre el lapso real entre el primer y el ultimo
-- garron de la hora, no sobre 60 min: asi la primera y la ultima hora de la jornada, que
-- son parciales, no aparecen como una caida de ritmo que no existio.
create or replace view reporting.v_produccion_hora as
select fecha_faena,
       hora,
       count(*)                                            as garrones,
       count(*) / 2.0                                      as cabezas,
       min(ts_local)                                       as primer_garron,
       max(ts_local)                                       as ultimo_garron,
       extract(epoch from max(ts_local) - min(ts_local))   as lapso_s,
       round((count(*) / 2.0) * 3600
             / nullif(extract(epoch from max(ts_local) - min(ts_local)), 0), 1) as ritmo_cab_h,
       sum(count(*) / 2.0) over (partition by fecha_faena order by hora)        as cabezas_acum
from reporting.v_produccion_garron
group by fecha_faena, hora;

comment on view reporting.v_produccion_hora is
  'Cabezas y ritmo por hora. ritmo_cab_h usa el lapso real de la hora, no 60 min fijos.';

-- Cabezas por especie y por raza del dia. Es el DENOMINADOR de "min de parada por 100
-- cabezas": las cabezas reales de esa especie, contadas garron por garron.
--
-- No sirve sumar v_produccion_franja.cabezas agrupando por especie_dominante -- eso da
-- "cabezas de las franjas donde domino esa especie", que es otra cosa y sobrecuenta las
-- franjas mezcladas.
create or replace view reporting.v_produccion_especie as
select fecha_faena,
       especie,
       count(*)          as garrones,
       count(*) / 2.0    as cabezas
from reporting.v_produccion_garron
where especie is not null
group by fecha_faena, especie;

create or replace view reporting.v_produccion_raza as
select fecha_faena,
       raza,
       count(*)          as garrones,
       count(*) / 2.0    as cabezas
from reporting.v_produccion_garron
where raza is not null
group by fecha_faena, raza;

comment on view reporting.v_produccion_especie is
  'Cabezas por especie y dia. Denominador de min de parada por 100 cabezas.';
comment on view reporting.v_produccion_raza is
  'Cabezas por raza y dia. Denominador de min de parada por 100 cabezas.';
