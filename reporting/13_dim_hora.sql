-- 13_dim_hora.sql -- dimension de hora del dia.
--
-- POR QUE EXISTE, si v_dim_franja ya tiene una columna `hora`
-- Los hechos que viven al grano de hora (v_produccion_hora) no tienen franja_id, asi que
-- no cuelgan de v_dim_franja. Sin una dimension de hora compartida, "ritmo por hora" y
-- "minutos de parada por hora" salen de tablas que no se conocen entre si: quedan en dos
-- ejes independientes y hacer clic en una hora de un grafico no filtra el otro.
--
-- Con esta dimension los dos graficos de la hoja "Perfil horario" comparten eje y se
-- filtran cruzado, que es como se leen juntos.

create or replace view reporting.v_dim_hora as
select g                                             as hora,
       lpad(g::text, 2, '0') || ':00'                as hora_hhmm,
       case
           when g <  5 then 'Madrugada'
           when g < 12 then 'Manana'
           when g < 14 then 'Mediodia'
           when g < 20 then 'Tarde'
           else 'Noche'
       end                                           as bloque
from generate_series(0, 23) g;

comment on view reporting.v_dim_hora is
  'Horas del dia 0-23. Eje compartido entre los hechos de grano hora y los de grano franja.';
