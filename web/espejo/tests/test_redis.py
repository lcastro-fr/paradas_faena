"""El espejo lo escribe el daemon con LiveMirror y lo lee el adapter.

Los tests usan las dos mitades reales en vez de payloads a mano: lo que tiene que quedar
protegido es que el daemon y la pantalla coincidan en el formato.
"""

from __future__ import annotations

import datetime as dt

import pytest

from espejo.redis import EspejoRedis, build_cliente
from paradas_faena.events import InputEdge, LiveSpeed

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db]

IP8 = "172.30.10.8"
IP9 = "172.30.10.9"


def flanco(valor: bool, cuando, *, reason="change", tag="Cont_P1") -> InputEdge:
    return InputEdge(ip=IP8, tag=tag, version=1, ts=cuando, value=valor, reason=reason)


def lectura(cuando, *, running: bool | None = True) -> LiveSpeed:
    return LiveSpeed(ip=IP9, ts=cuando, frec=49.8, vel=210.65, noria_running=running)


async def test_lee_exactamente_lo_que_el_daemon_espejo(espejo, mirror):
    ahora = dt.datetime.now(dt.UTC)
    mirror.tick(IP9, ahora, line=lectura(ahora))
    mirror.tick(IP8, ahora, input_edges=[flanco(True, ahora)])
    mirror.daemon_alive(ahora)

    estado = await espejo.leer([IP8, IP9])

    assert estado.ok
    assert estado.noria.running is True
    assert estado.noria.vel == pytest.approx(210.65)
    assert estado.noria.stale is False
    assert estado.reles[f"{IP8}|Cont_P1"].value is True
    assert estado.plcs_vivos == {IP8: True, IP9: True}
    assert estado.daemon_vivo is True


async def test_un_plc_que_dejo_de_contestar_no_esta_vivo(espejo, mirror, live_cfg):
    mirror.tick(IP8, dt.datetime.now(dt.UTC))
    mirror._redis.delete(live_cfg.plc_key(IP8))

    estado = await espejo.leer([IP8, IP9])

    assert estado.plcs_vivos == {IP8: False, IP9: False}


async def test_una_lectura_vieja_se_marca_rancia(espejo, mirror):
    vieja = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
    mirror.tick(IP9, vieja, line=lectura(vieja))
    assert (await espejo.leer([IP9])).noria.stale is True


async def test_un_estado_desconocido_sobrevive_como_none(espejo, mirror):
    """El único valor que jamás puede volverse False camino a la pantalla."""
    mirror.plc_down(IP9, dt.datetime.now(dt.UTC), variador=True)
    assert (await espejo.leer([IP9])).noria.running is None


async def test_redis_caido_se_reporta_y_no_revienta(live_cfg):
    cliente = build_cliente("redis://127.0.0.1:1/0", timeout=0.05)
    estado = await EspejoRedis(cliente, live_cfg, 15.0).leer([IP8])

    assert estado.ok is False
    assert estado.noria.running is None
    assert estado.plcs_vivos == {IP8: False}
    await cliente.aclose()


async def test_una_entrada_ilegible_no_se_lleva_al_resto(espejo, mirror, live_cfg):
    ahora = dt.datetime.now(dt.UTC)
    mirror.tick(IP8, ahora, input_edges=[flanco(True, ahora)])
    mirror._redis.hset(live_cfg.puestos_key, f"{IP8}|roto", "{no es json")

    estado = await espejo.leer([IP8])

    assert f"{IP8}|Cont_P1" in estado.reles
    assert f"{IP8}|roto" not in estado.reles
