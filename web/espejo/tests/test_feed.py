import asyncio

import pytest

from espejo.domain import sse
from espejo.redis import RESYNC, FeedRedis

pytestmark = pytest.mark.asyncio


def feed(queue_size: int = 4) -> FeedRedis:
    return FeedRedis(None, "sin-uso", queue_size=queue_size, translate=lambda e: {"t": "x"})


async def test_todos_los_suscriptores_reciben_todo():
    f = feed()
    async with f.subscribe() as a, f.subscribe() as b:
        f.broadcast(sse("delta", {"t": "speed"}))
        assert "speed" in await a.get()
        assert "speed" in await b.get()


async def test_un_cliente_lento_recibe_resync_en_vez_de_que_lo_corten():
    """Cortarlo costaría un handshake y un snapshot igual; el resync es más barato."""
    f = feed(queue_size=2)
    async with f.subscribe() as lento:
        for i in range(10):
            f.broadcast(sse("delta", {"n": i}))
        assert lento.qsize() <= 2
        assert await lento.get() == RESYNC


async def test_un_cliente_lento_no_le_cuesta_nada_a_los_demas():
    f = feed(queue_size=2)
    async with f.subscribe() as lento, f.subscribe() as rapido:
        f.broadcast(sse("delta", {"n": 1}))
        await rapido.get()
        for i in range(10):
            f.broadcast(sse("delta", {"n": i}))
            async with asyncio.timeout(1):
                await rapido.get()
        assert lento.qsize() <= 2


async def test_el_que_se_va_deja_de_recibir():
    f = feed()
    async with f.subscribe():
        assert f.clients == 1
    assert f.clients == 0
    f.broadcast(sse("delta", {"t": "speed"}))


async def test_el_frame_es_sse_valido():
    """Un frame SSE termina en una línea en blanco; sin eso el browser no lo entrega."""
    frame = sse("delta", {"t": "speed"})
    assert frame.endswith("\n\n")
    head, body = frame.rstrip("\n").split("\n")
    assert head == "event: delta"
    assert body == 'data: {"t":"speed"}'
