from __future__ import annotations

import asyncio

from django.contrib.auth.decorators import login_not_required
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render

from espejo.domain import sse
from espejo.factory import default_espejo, default_feed
from monitor import services


@login_not_required
def index(request):
    return render(request, "monitor/monitor.html")


@login_not_required
async def api_snapshot(request) -> JsonResponse:
    return JsonResponse((await services.snapshot(default_espejo())).to_json())


@login_not_required
async def stream(request) -> StreamingHttpResponse:
    feed = default_feed()

    async def frames():
        async with feed.subscribe() as cola:
            # Suscribir primero y mandar el snapshot después: al revés se pierde el
            # flanco que caiga en el medio.
            yield "retry: 3000\n\n"
            yield sse("snapshot", (await services.snapshot(default_espejo())).to_json())
            while True:
                try:
                    yield await asyncio.wait_for(cola.get(), timeout=30.0)
                except TimeoutError:
                    yield ": keepalive\n\n"

    respuesta = StreamingHttpResponse(frames(), content_type="text/event-stream")
    respuesta["Cache-Control"] = "no-cache"
    # nginx no debe bufferear el stream.
    respuesta["X-Accel-Buffering"] = "no"
    return respuesta


@login_not_required
def healthz(request) -> JsonResponse:
    feed = default_feed()
    vivo = feed.connected or services.cache.db_ok
    return JsonResponse(
        {"feed": feed.connected, "db": services.cache.db_ok, "clientes": feed.clients},
        status=200 if vivo else 503,
    )
