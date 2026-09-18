import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections

VISTAS = 26


def vistas_de_reporting() -> int:
    with connections["faena"].cursor() as cur:
        cur.execute(
            "select count(*) from information_schema.views where table_schema = 'reporting'"
        )
        return cur.fetchone()[0]


@pytest.mark.django_db(transaction=True, databases=["default", "faena"])
def test_aplica_toda_la_capa():
    call_command("apply_reporting")
    assert vistas_de_reporting() == VISTAS


@pytest.mark.django_db(transaction=True, databases=["default", "faena"])
def test_es_idempotente():
    call_command("apply_reporting")
    call_command("apply_reporting")
    assert vistas_de_reporting() == VISTAS


@pytest.mark.django_db(transaction=True, databases=["default", "faena"])
def test_check_no_deja_nada():
    call_command("apply_reporting")
    with connections["faena"].cursor() as cur:
        cur.execute("drop view reporting.v_monitor_hoy")

    call_command("apply_reporting", check=True)

    assert vistas_de_reporting() == VISTAS - 1
    call_command("apply_reporting")


@pytest.mark.django_db(transaction=True, databases=["default", "faena"])
def test_el_error_dice_que_archivo_fallo(settings, tmp_path):
    (tmp_path / "reporting").mkdir()
    (tmp_path / "reporting" / "77_roto.sql").write_text("select * from no_existe;")
    settings.BASE_DIR = tmp_path / "web"

    with pytest.raises(CommandError, match="77_roto.sql"):
        call_command("apply_reporting")


def test_sin_archivos_falla(settings, tmp_path):
    (tmp_path / "reporting").mkdir()
    settings.BASE_DIR = tmp_path / "web"

    with pytest.raises(CommandError, match="No hay vistas"):
        call_command("apply_reporting")
