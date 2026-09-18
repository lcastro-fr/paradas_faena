import datetime as dt

from espejo.domain import EstadoEspejo, Noria, Rele
from monitor.domain import Catalogo, Plc, Puesto, Totales
from monitor.services import componer, traducir
from paradas_faena.events import CounterIncrement, Heartbeat, InputEdge, LiveSpeed

IP8 = "172.30.10.8"
IP9 = "172.30.10.9"

CATALOGO = Catalogo(
    plcs=(
        Plc(ip=IP8, nombre="Redzone 1", variador=False),
        Plc(ip=IP9, nombre="Redzone 2", variador=True),
    ),
    objetivo_cab_h=220.0,
)

PUESTO = Puesto(
    puesto_key=f"{IP8}|Cont_P1|1",
    label="Descuereadora",
    plc="Redzone 1",
    plc_ip=IP8,
    tag="Cont_P1",
    orden=None,
    segundos_hoy=292.1,
    paradas_hoy=3,
)


def espejo_con(**kwargs) -> EstadoEspejo:
    base = {"ok": True, "plcs_vivos": {IP8: True, IP9: True}, "daemon_vivo": True}
    return EstadoEspejo(**(base | kwargs))


def test_el_estado_del_rele_sale_del_espejo():
    estado = espejo_con(
        reles={f"{IP8}|Cont_P1": Rele(value=True, ts_ms=1, since_ms=2, exact=True)}
    )
    snap = componer(CATALOGO, Totales(puestos=(PUESTO,), at=1.0), estado, db_ok=True)

    assert snap.puestos[0].activo is True
    assert snap.puestos[0].desde_ms == 2


def test_un_plc_callado_deja_sus_puestos_en_desconocido():
    """Sin esto, un daemon caído con el último flanco en true pinta una parada fantasma."""
    estado = espejo_con(
        plcs_vivos={IP8: False, IP9: True},
        reles={f"{IP8}|Cont_P1": Rele(value=True, ts_ms=1, since_ms=2, exact=True)},
    )
    snap = componer(CATALOGO, Totales(puestos=(PUESTO,), at=1.0), estado, db_ok=True)

    assert snap.puestos[0].activo is None
    # Los acumulados no dependen de que el PLC conteste.
    assert snap.puestos[0].segundos_hoy == 292.1


def test_un_variador_callado_deja_la_noria_en_desconocida_no_detenida():
    estado = espejo_con(
        plcs_vivos={IP8: True, IP9: False},
        noria=Noria(running=True, vel=210.0, frec=49.8, ts_ms=1, stale=False),
    )
    snap = componer(CATALOGO, Totales(), estado, db_ok=True)

    assert snap.noria.running is None


def test_redis_caido_no_se_lleva_los_acumulados():
    snap = componer(
        CATALOGO, Totales(puestos=(PUESTO,), at=1.0), EstadoEspejo(), db_ok=True
    )

    assert snap.fuentes.redis == "down"
    assert snap.fuentes.db == "ok"
    assert snap.puestos[0].segundos_hoy == 292.1
    assert snap.puestos[0].activo is None


def test_los_puestos_sin_orden_van_al_final_y_alfabeticos():
    puestos = (
        PUESTO,
        Puesto("k2", "Aaa", None, IP8, "t2", orden=None),
        Puesto("k3", "Zzz", None, IP8, "t3", orden=1),
    )
    snap = componer(CATALOGO, Totales(puestos=puestos, at=1.0), espejo_con(), db_ok=True)

    assert [p.label for p in snap.puestos] == ["Zzz", "Aaa", "Descuereadora"]


def test_los_totales_suman_lo_que_hay():
    snap = componer(CATALOGO, Totales(puestos=(PUESTO,), at=1.0), espejo_con(), db_ok=True)
    payload = snap.to_json()

    assert payload["totales"]["paradas_hoy"] == 3
    assert payload["totales"]["segundos_hoy"] == 292.1


AHORA = dt.datetime.now(dt.UTC)


def test_solo_llegan_a_la_pantalla_los_eventos_que_la_mueven(monkeypatch):
    from monitor import services

    monkeypatch.setitem(services.cache.por_rele, f"{IP8}|Cont_P1", "clave")

    lectura = LiveSpeed(ip=IP9, ts=AHORA, frec=49.8, vel=210.65, noria_running=True)
    assert traducir(lectura)["t"] == "speed"
    assert traducir(
        InputEdge(ip=IP8, tag="Cont_P1", version=1, ts=AHORA, value=True, reason="change")
    ) == {
        "t": "input",
        "puesto_key": "clave",
        "v": True,
        "reason": "change",
        "ts_ms": int(AHORA.timestamp() * 1000),
    }

    # Para la base, no para la pantalla.
    assert traducir(Heartbeat(ip=IP8, ts=AHORA)) is None
    assert traducir(
        CounterIncrement(
            ip=IP8, tag="Cont_P1", version=1, ts=AHORA, old_value=1, new_value=2,
            dif=1, noria_running=True, vel=1.0,
        )
    ) is None


def test_un_flanco_de_un_puesto_desconocido_se_descarta():
    from monitor import services

    services.cache.por_rele = {}
    assert traducir(
        InputEdge(ip=IP8, tag="Cont_P1", version=1, ts=AHORA, value=True, reason="change")
    ) is None
