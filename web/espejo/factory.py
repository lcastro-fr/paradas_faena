from functools import lru_cache

from django.conf import settings

from espejo.redis import SOCKET_TIMEOUT, EspejoRedis, FeedRedis, build_cliente


@lru_cache(maxsize=1)
def default_espejo() -> EspejoRedis:
    return EspejoRedis(
        build_cliente(settings.REDIS_URL),
        settings.LIVE_CONFIG,
        settings.MONITOR_STALE_SECONDS,
    )


@lru_cache(maxsize=1)
def default_feed() -> FeedRedis:
    from monitor.services import traducir

    return FeedRedis(
        # El XREAD se queda esperando en el socket todo el bloque: el timeout tiene que
        # superarlo o toda lectura muere de un timeout que nadie causó.
        build_cliente(settings.REDIS_URL, SOCKET_TIMEOUT),
        settings.REDIS_STREAM,
        queue_size=settings.MONITOR_QUEUE_SIZE,
        translate=traducir,
    )
