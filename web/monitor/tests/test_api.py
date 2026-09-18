import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from django.urls import reverse

pytestmark = pytest.mark.django_db(databases=["default", "faena"])


def test_la_pantalla_abre_sin_sesion(client):
    assert client.get(reverse("monitor:index")).status_code == 200


def test_el_snapshot_abre_sin_sesion(client):
    respuesta = client.get(reverse("monitor:snapshot"))
    assert respuesta.status_code == 200
    payload = json.loads(respuesta.content)
    assert {"noria", "puestos", "plcs", "fuentes", "totales"} <= payload.keys()


def test_el_healthcheck_reporta_cada_fuente(client):
    payload = json.loads(client.get(reverse("monitor:healthz")).content)
    assert set(payload) == {"feed", "db", "clientes"}


def test_el_admin_si_exige_sesion(client):
    respuesta = client.get("/admin/")
    assert respuesta.status_code == 302
    assert "login" in respuesta.headers["Location"]


@pytest.fixture
def servidor(live_cfg, settings):
    """uvicorn de verdad: el test client de Django junta la respuesta entera y un SSE no
    termina nunca."""
    from config.asgi import django_app

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        puerto = s.getsockname()[1]

    server = uvicorn.Server(
        uvicorn.Config(django_app, host="127.0.0.1", port=puerto, log_config=None,
                       access_log=False)
    )
    hilo = threading.Thread(target=server.run, daemon=True)
    hilo.start()
    limite = time.monotonic() + 15
    while not server.started and time.monotonic() < limite:
        time.sleep(0.05)
    assert server.started
    try:
        yield f"http://127.0.0.1:{puerto}"
    finally:
        server.should_exit = True
        hilo.join(timeout=10)


def test_el_stream_arranca_con_el_snapshot_entero(servidor):
    """Eso reemplaza al Last-Event-ID: cada conexión empieza con el estado completo."""
    with httpx.stream("GET", f"{servidor}/stream", timeout=15) as respuesta:
        assert respuesta.status_code == 200
        assert respuesta.headers["content-type"].startswith("text/event-stream")
        assert respuesta.headers["x-accel-buffering"] == "no"

        frames = respuesta.iter_lines()
        assert next(frames).startswith("retry:")
        next(frames)
        assert next(frames) == "event: snapshot"
        payload = json.loads(next(frames).removeprefix("data: "))

    assert "puestos" in payload and "noria" in payload
