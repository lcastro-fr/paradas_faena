-- 99_deprecaciones.sql -- marca las vistas viejas que el tablero NO usa.
--
-- monitoreo_faena.v_t_parada_puesto y v_t_parada_puesto_agg existen solo en la base
-- (creadas a mano, owner admin, ausentes de migrations/ y por lo tanto de cualquier
-- rebuild desde cero: hoy solo quedan registradas en schema.sql, que ni siquiera esta
-- trackeado en git).
--
-- Calculan lo mismo que queries/tiempo_parada_por_puesto.sql y
-- queries/paradas_individuales.sql pero SIN el tope max_segment: cierran el segmento
-- abierto con CURRENT_TIMESTAMP y suman sin acotar, asi que cuando el daemon estuvo
-- caido le cargan el hueco entero al puesto y dan totales mas altos que los reales.
--
-- No se borran ni se redefinen: puede haber un consumidor externo
-- (zato-fr/Faena/reporteParadas.py, citado en migrations/004_retire_timers.sql:5-7) y
-- este POC no toca objetos de produccion que no creo. Solo se les deja el aviso.
--
-- PENDIENTE al graduar el POC: llevar estas dos vistas -- y las de reporting/ -- a una
-- migracion dbmate, para que dejen de vivir unicamente en la base.

do $$
begin
    if to_regclass('monitoreo_faena.v_t_parada_puesto') is not null then
        comment on view monitoreo_faena.v_t_parada_puesto is
          'DEPRECADA: no aplica el tope max_segment, sobrestima el tiempo de parada '
          'cuando hay huecos de datos. Usar reporting.v_parada.';
    end if;
    if to_regclass('monitoreo_faena.v_t_parada_puesto_agg') is not null then
        comment on view monitoreo_faena.v_t_parada_puesto_agg is
          'DEPRECADA: no aplica el tope max_segment y cuenta cada re-assert como un '
          'segmento. Usar reporting.v_parada_franja.';
    end if;
end $$;
