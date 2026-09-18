-- La base propia de Django (auth, sesiones, admin). La de dominio la crea POSTGRES_DB.
-- El entrypoint de postgres corre esto solo en la primera inicializacion del volumen: en
-- un volumen que ya existe hay que crearla a mano.
create database paradas_faena_app;
