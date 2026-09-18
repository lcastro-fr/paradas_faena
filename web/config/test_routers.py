import pytest
from django.contrib.auth import get_user_model
from django.db import router

from catalogo.models import Parametros, Plc, PuestoConfig

User = get_user_model()


@pytest.mark.parametrize("modelo", [Plc, Parametros, PuestoConfig])
def test_el_dominio_va_a_faena(modelo):
    assert router.db_for_read(modelo) == "faena"
    assert router.db_for_write(modelo) == "faena"


def test_lo_de_django_va_a_su_propia_base():
    assert router.db_for_read(User) == "default"


@pytest.mark.parametrize("app", ["catalogo", "monitor"])
def test_dbmate_es_dueno_del_dominio(app):
    """Django no debe crear ni tocar esas tablas en ninguna base."""
    assert router.allow_migrate("default", app) is False
    assert router.allow_migrate("faena", app) is False


def test_django_solo_migra_su_base():
    assert router.allow_migrate("default", "auth") is True
    assert router.allow_migrate("faena", "auth") is False


@pytest.mark.django_db(databases=["default", "faena"])
def test_el_usuario_y_los_plcs_viven_en_bases_distintas():
    User.objects.create_user(email="x@y.com")
    assert User.objects.db == "default"
    assert Plc.objects.count() == 2
    assert Plc.objects.db == "faena"
