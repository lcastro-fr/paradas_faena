from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, replace

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import connections

from catalogo.models import Parametros
from catalogo.models import Plc as PlcModel
from espejo.domain import Espejo, EstadoEspejo, Feed, Noria, now_ms, sse, to_ms
from monitor.domain import Catalogo, Fuentes, Plc, Puesto, Snapshot, Totales
from paradas_faena.backoff import Backoff
from paradas_faena.events import Event, InputEdge, LiveSpeed, NoriaStatusEdge

log = logging.getLogger(__name__)

ACUMULADOS = """
    select puesto_key, puesto_label, plc, plc_ip, tag, orden_linea,
           segundos_hoy, paradas_hoy
    from reporting.v_monitor_hoy
"""


@dataclass
class Cache:
    """Lo que el poller refresca y las vistas leen. Un proceso, una copia."""

    catalogo: Catalogo = field(default_factory=Catalogo)
    totales: Totales | None = None
    por_rele: dict[str, str] = field(default_factory=dict)
    db_ok: bool = False


cache = Cache()


@sync_to_async(thread_sensitive=True)
def leer_catalogo() -> Catalogo:
    plcs = tuple(
        Plc(ip=p.ip, nombre=p.nombre, variador=p.variador)
        for p in PlcModel.objects.all()
    )
    parametros = Parametros.objects.first()
    objetivo = float(parametros.objetivo_cabezas_hora) if parametros else 220.0
    with connections["faena"].cursor() as cursor:
        cursor.execute(
            "select count(*) from reporting.v_monitor_hoy where orden_linea is not null"
        )
        configurado = bool(cursor.fetchone()[0])
    return Catalogo(plcs=plcs, objetivo_cab_h=objetivo, orden_configurado=configurado)


@sync_to_async(thread_sensitive=True)
def leer_acumulados() -> Totales:
    with connections["faena"].cursor() as cursor:
        cursor.execute(ACUMULADOS)
        filas = cursor.fetchall()
    puestos = tuple(
        Puesto(
            puesto_key=f[0],
            label=f[1],
            plc=f[2],
            plc_ip=f[3],
            tag=f[4],
            orden=f[5],
            segundos_hoy=round(float(f[6]), 1),
            paradas_hoy=int(f[7]),
        )
        for f in filas
    )
    return Totales(puestos=puestos, at=time.monotonic())


async def refrescar() -> Totales:
    totales = await leer_acumulados()
    cache.totales = totales
    cache.por_rele = {f"{p.plc_ip}|{p.tag}": p.puesto_key for p in totales.puestos}
    cache.db_ok = True
    return totales


async def snapshot(espejo: Espejo) -> Snapshot:
    totales = cache.totales or Totales()
    ips = [p.ip for p in cache.catalogo.plcs]
    estado = await espejo.leer(ips)
    return componer(cache.catalogo, totales, estado, cache.db_ok)


def componer(
    catalogo: Catalogo, totales: Totales, estado: EstadoEspejo, db_ok: bool
) -> Snapshot:
    puestos = [_con_estado_vivo(p, estado) for p in totales.puestos]
    puestos.sort(key=lambda p: (p.orden is None, p.orden or 0, p.label or ""))

    # Si el variador no contesta, la noria es "no sé". Nunca "detenida".
    variador_vivo = any(
        estado.plcs_vivos.get(p.ip, False) for p in catalogo.plcs if p.variador
    )
    noria = estado.noria if variador_vivo else Noria(ts_ms=estado.noria.ts_ms)

    edad = None if totales.at == 0 else round(time.monotonic() - totales.at, 1)
    return Snapshot(
        objetivo_cab_h=catalogo.objetivo_cab_h,
        noria=noria,
        plcs=tuple(
            Plc(
                ip=p.ip,
                nombre=p.nombre,
                variador=p.variador,
                vivo=estado.plcs_vivos.get(p.ip, False),
            )
            for p in catalogo.plcs
        ),
        puestos=tuple(puestos),
        daemon_vivo=estado.daemon_vivo,
        fuentes=Fuentes(
            redis="ok" if estado.ok else "down",
            db="ok" if db_ok else "down",
            db_edad_s=edad,
            orden_configurado=catalogo.orden_configurado,
        ),
    )


def _con_estado_vivo(puesto: Puesto, estado: EstadoEspejo) -> Puesto:
    """Un PLC que no contesta deja a sus puestos en desconocido, no en libre: lo espejado
    es lo último que dijo antes de callarse."""
    rele = estado.reles.get(f"{puesto.plc_ip}|{puesto.tag}")
    if rele is None or not estado.plcs_vivos.get(puesto.plc_ip, False):
        return puesto
    return replace(
        puesto, activo=rele.value, desde_ms=rele.since_ms, exacto=rele.exact
    )


def traducir(evento: Event) -> dict | None:
    """Los contadores y los heartbeats son para la base, y el SpeedSample ralo pelearía
    con la lectura viva: ninguno llega a la pantalla."""
    if isinstance(evento, LiveSpeed):
        return {
            "t": "speed",
            "running": evento.noria_running,
            "vel": evento.vel,
            "frec": evento.frec,
            "ts_ms": to_ms(evento.ts),
        }
    if isinstance(evento, NoriaStatusEdge):
        return {"t": "noria", "running": evento.running, "ts_ms": to_ms(evento.ts)}
    if isinstance(evento, InputEdge):
        clave = cache.por_rele.get(f"{evento.ip}|{evento.tag}")
        if clave is None:
            return None
        # `reason` viaja para que el browser mantenga `desde_ms` igual que
        # LiveMirror._advance: un re-assert no reinicia el cronómetro.
        return {
            "t": "input",
            "puesto_key": clave,
            "v": evento.value,
            "reason": evento.reason,
            "ts_ms": to_ms(evento.ts),
        }
    return None


async def poller(feed: Feed) -> None:
    """Refresca los acumulados y avisa. El intervalo también esquiva una carrera: el
    writer commitea por lotes, así que recalcular en cada flanco leería antes del commit.
    """
    backoff = Backoff(1.0, 30.0)
    while True:
        try:
            totales = await refrescar()
            backoff.reset()
            feed.broadcast(
                sse(
                    "delta",
                    {
                        "t": "hoy",
                        "ts_ms": now_ms(),
                        "puestos": [
                            {
                                "puesto_key": p.puesto_key,
                                "segundos_hoy": p.segundos_hoy,
                                "paradas_hoy": p.paradas_hoy,
                            }
                            for p in totales.puestos
                        ],
                    },
                )
            )
            await asyncio.sleep(settings.MONITOR_REFRESH_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            cache.db_ok = False
            demora = backoff.next_delay()
            log.error("acumulados del turno: %s; reintento en ~%.1fs", exc, demora)
            await asyncio.sleep(demora)


async def heartbeat(espejo: Espejo, feed: Feed) -> None:
    """También es como se nota un PLC caído: el vencimiento de un TTL no avisa a nadie."""
    while True:
        try:
            estado = await espejo.leer([p.ip for p in cache.catalogo.plcs])
            edad = cache.totales and round(time.monotonic() - cache.totales.at, 1)
            feed.broadcast(
                sse(
                    "hb",
                    {
                        "server_epoch_ms": now_ms(),
                        "redis": "ok" if estado.ok else "down",
                        "db": "ok" if cache.db_ok else "down",
                        "db_edad_s": edad,
                        "daemon_alive": estado.daemon_vivo,
                        "plcs": dict(estado.plcs_vivos),
                    },
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("heartbeat: %s", exc)
        await asyncio.sleep(settings.MONITOR_HEARTBEAT_SECONDS)
