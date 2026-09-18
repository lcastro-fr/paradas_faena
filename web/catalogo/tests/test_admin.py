import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse

from catalogo.models import Parametros, PuestoConfig

User = get_user_model()


@pytest.fixture
def admin_cliente(db, client):
    user = User.objects.create_superuser(email="admin@rioplatense.com", password="x")
    client.force_login(user)
    return client


@pytest.mark.django_db(databases=["default", "faena"])
@pytest.mark.parametrize(
    "vista", ["catalogo_plc", "catalogo_parametros", "catalogo_puestoconfig"]
)
def test_el_listado_abre(admin_cliente, vista):
    assert admin_cliente.get(reverse(f"admin:{vista}_changelist")).status_code == 200


@pytest.mark.django_db(databases=["default", "faena"])
def test_no_se_puede_agregar_una_segunda_fila_de_parametros(admin_cliente):
    """Toda la capa hace cross join contra v_parametros: dos filas duplican cada hecho."""
    assert Parametros.objects.count() == 1
    assert admin_cliente.get(reverse("admin:catalogo_parametros_add")).status_code == 403


@pytest.mark.django_db(databases=["default", "faena"])
def test_los_parametros_no_se_borran(admin_cliente):
    fila = Parametros.objects.get()
    respuesta = admin_cliente.get(
        reverse("admin:catalogo_parametros_delete", args=[fila.pk])
    )
    assert respuesta.status_code == 403


@pytest.mark.django_db(databases=["default", "faena"])
def test_un_puesto_inexistente_es_error_de_formulario(db):
    """La foreign key lo rechazaría igual, pero con un 500 en vez de un mensaje."""
    with pytest.raises(ValidationError, match="counters_name"):
        PuestoConfig(ip="172.30.10.8", tag="NoExiste", version=1).full_clean()


@pytest.mark.django_db(databases=["default", "faena"])
def test_un_puesto_real_pasa_la_validacion(db):
    PuestoConfig(ip="172.30.10.8", tag="Cont_P1", version=1, orden_linea=1).full_clean()


@pytest.mark.django_db(databases=["default", "faena"])
def test_editar_los_parametros_se_ve_en_la_vista(admin_cliente):
    fila = Parametros.objects.get()
    fila.monitor_hora_inicio = "05:30"
    fila.save()

    from django.db import connections

    with connections["faena"].cursor() as cur:
        cur.execute("select monitor_hora_inicio from reporting.v_parametros")
        assert str(cur.fetchone()[0]) == "05:30:00"
