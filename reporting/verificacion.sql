-- verificacion.sql -- invariantes de la capa reporting. Solo SELECT, no toca nada.
--
--   psql "$DSN" -f reporting/verificacion.sql
--
-- Todo lo que diga FALLA hay que mirarlo antes de publicar el tablero. El chequeo de
-- paridad contra queries/*.sql se corre aparte, con tools/report.py, porque esas
-- consultas son parametrizadas -- ver el README.

\echo '== 1. Retencion por puesto (comparar contra: report tiempo_parada_por_puesto) =='
select puesto, sum(duracion_s) as duracion_s, count(*) as paradas
from reporting.v_parada
group by puesto
order by duracion_s desc;

\echo ''
\echo '== 2. Partir en franjas no pierde ni inventa tiempo =='
select coalesce(round(pf.total, 6), 0)  as parada_franja_s,
       coalesce(round(seg.total, 6), 0) as input_segmento_s,
       case when coalesce(round(pf.total, 6), 0) = coalesce(round(seg.total, 6), 0)
            then 'OK' else 'FALLA' end  as resultado
from (select sum(duracion_s) as total from reporting.v_parada_franja) pf
cross join (select sum(extract(epoch from duracion))::numeric as total
            from reporting.v_input_segmento) seg;

\echo ''
\echo '== 3. Las islas de v_parada suman lo mismo que los segmentos crudos =='
select round(p.total, 6) as parada_s, round(s.total, 6) as segmento_s,
       case when round(p.total, 6) = round(s.total, 6) then 'OK' else 'FALLA' end as resultado
from (select sum(duracion_s) as total from reporting.v_parada) p
cross join (select sum(extract(epoch from duracion))::numeric as total
            from reporting.v_input_segmento) s;

\echo ''
\echo '== 4. Retencion por puesto >= retencion fusionada (nunca al reves) =='
select k.fecha_faena, k.retencion_s, k.retencion_fusionada_s,
       k.retencion_s - k.retencion_fusionada_s as simultaneidad_s,
       case when k.retencion_s >= k.retencion_fusionada_s then 'OK' else 'FALLA' end as resultado
from reporting.v_jornada_kpi k
order by k.fecha_faena;

\echo ''
\echo '== 4b. Retencion (reles) contra noria parada (variador) -- NO tienen que coincidir =='
\echo '   Es la comparacion que explica el impacto real. Si la diferencia es grande,'
\echo '   correr queries/retencion_vs_noria.sql para ver de que puesto sale.'
select k.fecha_faena,
       round(k.retencion_fusionada_s / 60, 1) as retencion_fusionada_min,
       round(k.noria_parada_s / 60, 1)        as noria_parada_min,
       round((k.retencion_fusionada_s - k.noria_parada_s) / 60, 1) as diferencia_min,
       round(k.pct_jornada_perdida * 100, 2)  as pct_jornada_perdida
from reporting.v_jornada_kpi k
order by k.fecha_faena;

\echo ''
\echo '== 5. Cabezas por hora y por franja cuadran con produccion.v_summary_faena =='
select s.fecha, s.cabezas as summary, h.cabezas as por_hora, f.cabezas as por_franja,
       case when s.cabezas = h.cabezas and s.cabezas = f.cabezas then 'OK' else 'FALLA' end as resultado
from produccion.v_summary_faena s
left join (select fecha_faena, sum(cabezas) as cabezas from reporting.v_produccion_hora
           group by fecha_faena) h on h.fecha_faena = s.fecha
left join (select fecha_faena, sum(cabezas) as cabezas from reporting.v_produccion_franja
           group by fecha_faena) f on f.fecha_faena = s.fecha
order by s.fecha;

\echo ''
\echo '== 6. Ninguna parada se reporta bajo una version con la que nunca se grabo =='
select case when count(*) = 0 then 'OK' else 'FALLA' end as resultado, count(*) as filas_huerfanas
from reporting.v_parada p
where not exists (
    select 1 from monitoreo_faena.input_status s
    where host(s.ip)  = split_part(p.puesto_key, '|', 1)   -- host(): inet se renderiza como CIDR
      and s.tag       = split_part(p.puesto_key, '|', 2)
      and s.version   = split_part(p.puesto_key, '|', 3)::int
      and s.value
);

\echo ''
\echo '== 7. Cobertura de datos: por debajo de 0.98 los totales de duracion no son confiables =='
select fecha_faena, plc, latidos, latidos_esperados, cobertura,
       case when cobertura >= 0.98 then 'OK' else 'REVISAR' end as resultado
from reporting.v_cobertura
order by fecha_faena, plc;

\echo ''
\echo '== 8. Cuanto trabajo hizo el tope max_segment (tiempo que NO se registro) =='
select fecha_faena, round(descartado_por_hueco_s) as descartado_s, paradas_con_hueco,
       round(descartado_por_hueco_s / nullif(retencion_s, 0), 4) as pct_sobre_retencion
from reporting.v_jornada_kpi
order by fecha_faena;

\echo ''
\echo '== 9. Pedidos de parada (contador del PLC) vs paradas detectadas (rele) =='
select coalesce(ped.puesto, par.puesto) as puesto, ped.pedidos, par.paradas,
       case when ped.pedidos is distinct from par.paradas then 'REVISAR' else 'OK' end as resultado
from (select puesto, sum(pedidos) as pedidos from reporting.v_pedido_parada group by puesto) ped
full join (select puesto, count(*) as paradas from reporting.v_parada group by puesto) par
       using (puesto)
order by 1;

\echo ''
\echo '== 10. Puestos sin orden_linea cargado (atribucion a especie/raza no confiable) =='
select count(*) filter (where offset_sin_configurar) as sin_configurar,
       count(*)                                       as puestos,
       case when count(*) filter (where offset_sin_configurar) = 0
            then 'OK' else 'PENDIENTE: relevar orden_linea' end as resultado
from reporting.v_dim_puesto
where es_version_vigente;
