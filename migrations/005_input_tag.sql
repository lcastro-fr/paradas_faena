-- 005_input_tag.sql — map each puesto to its digital-input tag, not to an array index.
--
-- 001 created input_index as an integer, on the assumption that every stop relay landed
-- in an `InputStatus` array and the column held its position in it. No such tag exists
-- on either controller: they are Micro820s (2080-LC20-20QBB) exposing 20 individual
-- BOOLs each -- _IO_EM_DI_00..11, _IO_P1_DI_00..03, _IO_P2_DI_00..03 -- matching
-- Counter0..Counter19 one for one.
--
-- So the column becomes input_tag, holding the tag name of the input, e.g.
-- '_IO_EM_DI_00'.
--
-- The old integer values were positions into an array that does not exist; they carry
-- no meaning as tag names, so they are discarded rather than cast. The mapping has to
-- be established in the plant anyway (tools/dump_inputs.py).
--
-- Safe to re-run over a database where an earlier draft of this migration only changed
-- the type.

-- migrate:up

do $$
begin
    if exists (
        select 1 from information_schema.columns
        where table_schema = 'monitoreo_faena'
          and table_name = 'counters_name'
          and column_name = 'input_index'
    ) then
        alter table monitoreo_faena.counters_name
            alter column input_index type varchar using null;
        alter table monitoreo_faena.counters_name
            rename column input_index to input_tag;
        alter index monitoreo_faena.counters_name_input_index_uq
            rename to counters_name_input_tag_uq;
    end if;
end $$;

-- migrate:down

alter index monitoreo_faena.counters_name_input_tag_uq
    rename to counters_name_input_index_uq;
alter table monitoreo_faena.counters_name rename column input_tag to input_index;
alter table monitoreo_faena.counters_name
    alter column input_index type integer using null;
