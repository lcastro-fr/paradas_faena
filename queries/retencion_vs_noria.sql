-- Descompone la diferencia entre "retencion de los puestos" y "noria realmente detenida".
--
-- POR QUE HACE FALTA
-- Son dos mediciones distintas y se las confunde facil:
--   retencion       sale de input_status -- cuanto tiempo un puesto PIDIO la parada.
--   noria detenida  sale de noria_status -- cuanto tiempo la noria ESTUVO parada.
-- Si la primera es mucho mayor que la segunda, esta consulta dice de donde sale la
-- diferencia, que es lo que decide como se calcula el impacto de cada puesto.
--
-- COMO LEER EL RESULTADO
--   pct_en_marcha bajo y parejo entre puestos
--       -> el rele para la linea. La diferencia es el rato que el operario tarda en
--          soltarlo despues de que la noria rearranca. La retencion sirve para el ranking
--          (mide el impacto del puesto) pero NO para el tiempo perdido.
--   pct_en_marcha alto en UN puesto
--       -> mirar ese rele: probablemente quedo trabado o esta mal cableado. Un input
--          pegado en true suma retencion sin parar nada y corona el ranking sin motivo.
--          La columna mas_largo_en_marcha muestra el segmento peor.
--   pct_en_marcha alto en TODOS
--       -> el rele es un aviso y no detiene la linea. En ese caso el tiempo perdido hay
--          que tomarlo de noria_status y la retencion pasa a ser otra cosa.
--
-- `diferencia_min` = retencion - tiempo con la noria detenida. En las filas de puesto es
-- la retencion que transcurrio con la noria ANDANDO. En la fila TOTAL es otra cosa:
--   positiva -> los reles se sostienen mas de lo que la linea estuvo parada;
--   negativa -> la linea estuvo parada mas de lo que nadie retuvo, o sea que algo la paro
--               sin pasar por un rele, o hubo un hueco de datos (mirar v_cobertura).
--
-- La fila TOTAL compara la retencion FUSIONADA (sin doble conteo entre puestos) contra el
-- total de noria detenida. Es la comparacion que corresponde a nivel jornada; sumar la
-- columna de arriba da de mas porque dos puestos pueden retener a la vez.
--
-- Todo se recorta a la VENTANA DE LA JORNADA, no a la ventana que se pasa por parametro.
-- Sin eso, "noria detenida" incluye las horas en que la noria esta apagada porque no se
-- esta faenando, que no es tiempo perdido de nada y arruina la comparacion. Por eso esta
-- consulta depende de reporting.v_jornada -- a diferencia del resto de queries/, que corren
-- contra monitoreo_faena a secas.
--
-- Parametros: %(desde)s, %(hasta)s, %(max_segment)s -- ver tiempo_parada_por_puesto.sql.

with ventana as (
    select greatest((j.inicio_margen at time zone p.tz), %(desde)s) as ini,
           least((j.fin_margen at time zone p.tz), %(hasta)s)       as fin
    from reporting.v_jornada j
    cross join reporting.v_parametros p
    where (j.inicio_margen at time zone p.tz) < %(hasta)s
      and (j.fin_margen    at time zone p.tz) > %(desde)s
),
ret_seg as (
    select cn.name,
           s.value,
           greatest(s.ts, %(desde)s) as ini,
           least(coalesce(lead(s.ts) over (partition by s.ip, s.tag order by s.ts),
                          %(hasta)s),
                 %(hasta)s) as fin
    from monitoreo_faena.input_status s
    join monitoreo_faena.counters_name cn using (ip, tag, version)
    where s.ts >= %(desde)s - interval '1 hour'
      and s.ts <  %(hasta)s
),
ret as (
    -- El tope acota el hueco de datos, igual que en el resto de las consultas.
    select r.name,
           greatest(r.ini, v.ini)                        as ini,
           least(least(r.fin, r.ini + %(max_segment)s), v.fin) as fin
    from ret_seg r
    join ventana v
      on r.ini < v.fin
     and r.fin > v.ini
    where r.value
      and r.fin > r.ini
),
nor_seg as (
    -- SIN filtrar por fecha antes del lead: noria_status guarda solo flancos, y el estado
    -- vigente al inicio de la ventana puede venir de un flanco muy anterior. Si se filtra
    -- primero, la ventana arranca sin estado y se pierde la primera parada.
    select n.running,
           greatest(n.ts, %(desde)s) as ini,
           least(coalesce(lead(n.ts) over (partition by n.ip order by n.ts),
                          %(hasta)s),
                 %(hasta)s) as fin
    from monitoreo_faena.noria_status n
),
detenida as (
    select greatest(n.ini, v.ini) as ini,
           least(n.fin, v.fin)    as fin
    from nor_seg n
    join ventana v
      on n.ini < v.fin
     and n.fin > v.ini
    where not n.running
      and n.fin > n.ini
),
-- Retencion de cada puesto que cae dentro de una parada real de la noria.
solape as (
    select r.name,
           sum(least(r.fin, d.fin) - greatest(r.ini, d.ini)) as t_detenida
    from ret r
    join detenida d
      on d.ini < r.fin
     and d.fin > r.ini
    group by r.name
),
-- El peor segmento de retencion que transcurrio con la noria andando.
en_marcha as (
    select r.name,
           max(r.fin - r.ini
               - coalesce((select sum(least(r.fin, d.fin) - greatest(r.ini, d.ini))
                           from detenida d
                           where d.ini < r.fin and d.fin > r.ini), interval '0')) as peor
    from ret r
    group by r.name
),
por_puesto as (
    select r.name,
           sum(r.fin - r.ini) as t_retencion,
           count(*)           as segmentos
    from ret r
    where r.fin > r.ini
    group by r.name
),
-- Retencion fusionada entre puestos, para la fila TOTAL.
ret_ord as (
    select r.*,
           max(r.fin) over (order by r.ini rows between unbounded preceding and 1 preceding) as fin_prev
    from ret r
),
ret_grp as (
    select ret_ord.*,
           sum(case when fin_prev is null or ini > fin_prev then 1 else 0 end) over (order by ini) as g
    from ret_ord
),
ret_fusion as (
    select sum(b - a) as total from (select min(ini) a, max(fin) b from ret_grp group by g) x
)
-- Minutos con signo y no intervalos: la diferencia puede ser negativa, y un interval
-- negativo se lee pesimo ("-1 day, 23:51:50" por -8 min).
select 0                                  as orden,
       p.name                             as puesto,
       round(extract(epoch from p.t_retencion) / 60.0, 1)                     as retencion_min,
       round(extract(epoch from coalesce(s.t_detenida, interval '0')) / 60.0, 1)
                                                                              as con_noria_detenida_min,
       round(extract(epoch from p.t_retencion - coalesce(s.t_detenida, interval '0')) / 60.0, 1)
                                                                              as diferencia_min,
       round(extract(epoch from p.t_retencion - coalesce(s.t_detenida, interval '0'))
             / nullif(extract(epoch from p.t_retencion), 0), 3)               as pct_en_marcha,
       round(extract(epoch from e.peor))                                      as peor_en_marcha_s,
       p.segmentos
from por_puesto p
left join solape s using (name)
left join en_marcha e using (name)

union all

select 1,
       'TOTAL (fusionado)',
       round(extract(epoch from (select total from ret_fusion)) / 60.0, 1),
       round(extract(epoch from (select sum(fin - ini) from detenida)) / 60.0, 1),
       round(extract(epoch from (select total from ret_fusion)
                                - (select sum(fin - ini) from detenida)) / 60.0, 1),
       round(extract(epoch from (select total from ret_fusion)
                                - (select sum(fin - ini) from detenida))
             / nullif(extract(epoch from (select total from ret_fusion)), 0), 3),
       null,
       null
order by orden, diferencia_min desc nulls last;
