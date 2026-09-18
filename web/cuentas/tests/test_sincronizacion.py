from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from cuentas.signals import sincronizar_grupos

User = get_user_model()


def login_social(grupos):
    return SimpleNamespace(account=SimpleNamespace(extra_data={"groups": grupos}))


@pytest.fixture
def usuario(db):
    return User.objects.create_user(email="operario@rioplatense.com")


@pytest.mark.django_db
def test_los_grupos_de_keycloak_se_traducen_a_grupos_de_django(usuario):
    Group.objects.create(name="produccion")
    Group.objects.create(name="gerencia")

    sincronizar_grupos(None, None, usuario, sociallogin=login_social(["produccion"]))

    assert list(usuario.groups.values_list("name", flat=True)) == ["produccion"]


@pytest.mark.django_db
def test_un_grupo_que_no_existe_en_django_se_ignora(usuario):
    sincronizar_grupos(None, None, usuario, sociallogin=login_social(["inventado"]))
    assert usuario.groups.count() == 0


@pytest.mark.django_db
def test_keycloak_manda_sobre_lo_asignado_a_mano(usuario):
    """Una asignación desde el admin dura hasta el próximo login."""
    a_mano = Group.objects.create(name="a_mano")
    usuario.groups.add(a_mano)

    sincronizar_grupos(None, None, usuario, sociallogin=login_social([]))

    assert usuario.groups.count() == 0


@pytest.mark.django_db
def test_se_recorta_el_path_completo_del_grupo(usuario, settings):
    Group.objects.create(name="produccion")
    sincronizar_grupos(None, None, usuario, sociallogin=login_social(["/produccion"]))
    assert usuario.groups.count() == 1


@pytest.mark.django_db
def test_el_grupo_de_superusuario_da_acceso_al_admin(usuario, settings):
    sincronizar_grupos(None, None, usuario, sociallogin=login_social(["superuser"]))
    usuario.refresh_from_db()
    assert usuario.is_superuser and usuario.is_staff


@pytest.mark.django_db
def test_salir_del_grupo_de_superusuario_lo_saca_del_admin(usuario):
    usuario.is_superuser = usuario.is_staff = True
    usuario.save()

    sincronizar_grupos(None, None, usuario, sociallogin=login_social([]))

    usuario.refresh_from_db()
    assert not usuario.is_superuser and not usuario.is_staff


@pytest.mark.django_db
def test_un_login_local_no_toca_los_grupos(usuario):
    """El admin entra sin sociallogin: no hay claim que sincronizar."""
    grupo = Group.objects.create(name="a_mano")
    usuario.groups.add(grupo)

    sincronizar_grupos(None, None, usuario)

    assert usuario.groups.count() == 1


@pytest.mark.django_db
def test_un_claim_de_un_solo_grupo_llega_como_string(usuario):
    Group.objects.create(name="produccion")
    sincronizar_grupos(None, None, usuario, sociallogin=login_social("produccion"))
    assert usuario.groups.count() == 1
