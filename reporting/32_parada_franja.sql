-- 32_parada_franja.sql -- tiempo de parada por puesto y franja.
--
-- Esta es la vista ADITIVA del perfil horario. v_parada atribuye la parada completa a la
-- franja en que arranco; aca cada segmento ya topeado se PARTE en los limites de franja,
-- asi que una parada de 40 min reparte sus minutos entre las franjas que efectivamente
-- ocupo. Sin esto, el heatmap puesto x hora miente.
--
-- El intervalo que se parte es el TOPEADO [ts_start, ts_start + duracion), no el bruto:
-- de otro modo el hueco de datos volveria a entrar por la ventana.
--
-- fecha_faena aca es el dia de la FRANJA, no el de arranque de la parada, asi que una
-- retencion que cruza medianoche se reparte entre los dos dias. Es la diferencia
-- deliberada con v_parada.fecha_faena.

create or replace view reporting.v_parada_franja as
with s as (
    select puesto_key, puesto, ip, tag, version,
           ts_start_local              as a,
           ts_start_local + duracion   as b
    from reporting.v_input_segmento
),
acotado as (
    select s.*,
           p.franja_intervalo,
           p.franja_minutos,
           -- Inicio de la franja que contiene a `a`.
           date_trunc('day', s.a)
             + make_interval(secs => (floor(extract(epoch from s.a - date_trunc('day', s.a))
                                            / (p.franja_minutos * 60))
                                      * p.franja_minutos * 60)::double precision) as franja0
    from s
    cross join reporting.v_parametros p
    where s.b > s.a
),
partes as (
    select acotado.puesto_key,
           acotado.puesto,
           acotado.ip,
           acotado.tag,
           acotado.version,
           acotado.franja_minutos,
           f                                                as franja_inicio,
           greatest(acotado.a, f)                           as desde,
           least(acotado.b, f + acotado.franja_intervalo)   as hasta
    from acotado
    cross join generate_series(acotado.franja0, acotado.b, acotado.franja_intervalo) f
)
select puesto_key,
       puesto,
       ip,
       tag,
       version,
       franja_inicio::date                                        as fecha_faena,
       ((extract(hour from franja_inicio) * 60
         + extract(minute from franja_inicio))::int / franja_minutos) as franja_id,
       extract(hour from franja_inicio)::int                       as hora,
       sum(extract(epoch from hasta - desde))::numeric             as duracion_s,
       count(*)                                                    as segmentos,
       (j.fecha_faena is not null
        and partes.franja_inicio < j.fin_margen
        and partes.franja_inicio + make_interval(mins => partes.franja_minutos)
            > j.inicio_margen)                                     as en_jornada
from partes
left join reporting.v_jornada j on j.fecha_faena = partes.franja_inicio::date
where hasta > desde
group by puesto_key, puesto, ip, tag, version, franja_inicio, franja_minutos,
         j.fecha_faena, j.inicio_margen, j.fin_margen;

comment on view reporting.v_parada_franja is
  'Minutos de parada por puesto y franja, partiendo cada segmento en los limites de franja. Aditiva.';
