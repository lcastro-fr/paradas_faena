import asyncio
import contextlib
import logging
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django_app = get_asgi_application()

log = logging.getLogger(__name__)


async def _arrancar() -> list[asyncio.Task]:
    from espejo.factory import default_espejo, default_feed
    from monitor import services

    espejo, feed = default_espejo(), default_feed()
    try:
        services.cache.catalogo = await services.leer_catalogo()
    except Exception as exc:
        # Degradado, no fatal: el poller reintenta y la pantalla lo dice.
        log.error("no se pudo leer el catálogo al arrancar: %s", exc)

    await feed.start()
    return [
        asyncio.create_task(services.poller(feed), name="poller"),
        asyncio.create_task(services.heartbeat(espejo, feed), name="heartbeat"),
    ]


async def application(scope, receive, send):
    """Django no implementa el protocolo lifespan, así que las tareas de fondo del monitor
    (el tail del stream, el poller y el heartbeat) no tendrían dónde colgarse."""
    if scope["type"] != "lifespan":
        await django_app(scope, receive, send)
        return

    tareas: list[asyncio.Task] = []
    while True:
        mensaje = await receive()
        if mensaje["type"] == "lifespan.startup":
            try:
                tareas = await _arrancar()
            except Exception as exc:
                await send({"type": "lifespan.startup.failed", "message": str(exc)})
                return
            await send({"type": "lifespan.startup.complete"})
        elif mensaje["type"] == "lifespan.shutdown":
            for tarea in tareas:
                tarea.cancel()
            for tarea in tareas:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await tarea
            await send({"type": "lifespan.shutdown.complete"})
            return
