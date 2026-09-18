/* RELOJES. Hay tres en juego: el de este browser, el del proceso monitor y el de la
 * base. El unico que manda es el del monitor: cada `hb` trae `server_epoch_ms`, de ahi
 * sale `offset`, y el cronometro mide `(Date.now() + offset) - desde_ms`. Asi una
 * pantalla de planta con la hora mal puesta por horas igual mide bien.
 */

const estado = {
    offset: 0, // reloj del servidor menos el de este browser
    objetivo: 220,
    noria: { running: null, vel: null, frec: null },
    puestos: new Map(), // puesto_key -> fila
    plcs: [],
    fuentes: {},
    ultimoHb: 0,
    vivo: false, // hay conexion con el daemon (Redis)
};

const $ = (id) => document.getElementById(id);
const ahora = () => Date.now() + estado.offset;

function reloj(ms) {
    if (ms == null || ms < 0) return "--:--";
    const total = Math.floor(ms / 1000);
    const s = total % 60,
        m = Math.floor(total / 60) % 60,
        h = Math.floor(total / 3600);
    const dosDigitos = (n) => String(n).padStart(2, "0");
    return h > 0
        ? `${h}:${dosDigitos(m)}:${dosDigitos(s)}`
        : `${m}:${dosDigitos(s)}`;
}

function horaDeReloj(ms) {
    const d = new Date(ms);
    return [d.getHours(), d.getMinutes(), d.getSeconds()]
        .map((n) => String(n).padStart(2, "0"))
        .join(":");
}

function aviso() {
    const el = $("aviso");
    const f = estado.fuentes;
    let texto = null,
        clase = "";

    if (!estado.vivo || f.redis === "down") {
        texto =
            "SIN CONEXION AL DAEMON — los datos en pantalla no se estan actualizando";
        clase = "bg-error text-error-content";
    } else if (f.daemon_alive === false) {
        texto = "EL DAEMON NO ESTA CORRIENDO — ultimo estado conocido";
        clase = "bg-error text-error-content";
    } else if (f.db === "down") {
        // A proposito no es un error rojo: la linea en vivo sigue siendo correcta, lo unico
        // congelado son los dos acumulados.
        texto = "ACUMULADOS DEL DIA DESACTUALIZADOS — la base no responde";
        clase = "bg-warning text-warning-content";
    }

    el.className = texto
        ? `sticky top-0 z-30 px-4 py-2 text-center font-bold tracking-wide ${clase}`
        : "hidden";
    if (texto) el.textContent = texto;
}

function pintarNoria() {
    const { running, vel, frec } = estado.noria;
    const desconocido =
        !estado.vivo || running === null || running === undefined;

    $("vel").textContent = desconocido || vel == null ? "--" : Math.round(vel);
    $("frec").textContent =
        desconocido || frec == null ? "-- Hz" : `${frec.toFixed(1)} Hz`;

    const badge = $("estado-noria");
    // Tres estados, no dos. "sin datos" nunca se pinta como "detenida": una pantalla que
    // reporta una parada que no ocurrio deja de usarse en una semana.
    if (desconocido) {
        badge.className =
            "badge badge-lg badge-ghost text-lg py-4 font-bold rayado";
        badge.textContent = "SIN DATOS";
    } else if (running) {
        badge.className = "badge badge-lg badge-success text-lg py-4 font-bold";
        badge.textContent = "EN MARCHA";
    } else {
        badge.className = "badge badge-lg badge-error text-lg py-4 font-bold";
        badge.textContent = "DETENIDA";
    }

    const alcanzado =
        desconocido || vel == null ? 0 : Math.min(vel / estado.objetivo, 1.15);
    $("barra").style.width = `${(alcanzado * 100).toFixed(1)}%`;
    $("objetivo").textContent = Math.round(estado.objetivo);
    $("cumplimiento").textContent =
        desconocido || vel == null
            ? ""
            : `${Math.round((vel / estado.objetivo) * 100)}%`;
}

function pintarPlcs() {
    $("plcs").innerHTML = estado.plcs
        .map((p) => {
            const clase = p.alive ? "badge-success" : "badge-error";
            const nombre = p.nombre || p.ip;
            return `<span class="badge ${clase} badge-sm gap-1" title="${p.ip}">${nombre}</span>`;
        })
        .join("");
}

function claseTile(p) {
    if (p.activo === null || p.activo === undefined) {
        return "bg-base-300 opacity-70 rayado";
    }
    return p.activo
        ? "bg-error text-error-content ring-4 ring-error latiendo"
        : "bg-base-200 border-l-8 border-success";
}

function pintarGrilla() {
    const filas = [...estado.puestos.values()];
    $("grilla").innerHTML = filas
        .map(
            (p) => `
    <article class="rounded-box p-3 ${claseTile(p)}" data-key="${p.puesto_key}">
      <div class="font-bold uppercase text-sm tracking-wide truncate">${p.label}</div>
      <div class="cronometro font-black tabular-nums leading-none my-1"
           style="font-size: clamp(2rem, 4vw, 3.25rem)">&middot;&middot;&middot;</div>
      <div class="text-sm opacity-80 tabular-nums">
        hoy ${reloj(p.segundos_hoy * 1000)} &middot; ${p.paradas_hoy}
        ${p.paradas_hoy === 1 ? "parada" : "paradas"}
      </div>
    </article>`,
        )
        .join("");
    tick();
}

/* El cronometro no se pide al servidor: se calcula aca, cada 250 ms, contra el reloj
   corregido. Si no hay conexion se congela en vez de seguir contando sobre datos viejos. */
function tick() {
    const t = ahora();
    $("reloj").textContent = horaDeReloj(t);

    for (const art of $("grilla").children) {
        const p = estado.puestos.get(art.dataset.key);
        const el = art.querySelector(".cronometro");
        if (!p) continue;
        const enCurso = p.activo && p.desde_ms != null && estado.vivo;
        el.classList.toggle("opacity-40", !enCurso);
        el.style.fontSize = enCurso ? "" : "clamp(1.25rem, 2vw, 1.75rem)";
        if (p.activo === null || p.activo === undefined) {
            el.textContent = "S/D";
        } else if (!p.activo || p.desde_ms == null) {
            // "libre" y no un guion: a tamano de cronometro un guion se lee como una barra
            // maciza, que es justo lo que no queremos que llame la atencion.
            el.textContent = "libre";
        } else if (!estado.vivo) {
            el.textContent = reloj(Math.max(0, estado.ultimoHb - p.desde_ms));
        } else {
            // exacto=false: el flanco cayo dentro de un corte del daemon, asi que lo que se
            // sabe es una cota inferior.
            el.textContent =
                (p.exacto === false ? "≥ " : "") +
                reloj(Math.max(0, t - p.desde_ms));
        }
    }
}

function pintarTotales() {
    const filas = [...estado.puestos.values()];
    $("total-activos").textContent = filas.filter((p) => p.activo).length;
    $("total-tiempo").textContent = reloj(
        filas.reduce((a, p) => a + p.segundos_hoy, 0) * 1000,
    );
    $("total-paradas").textContent = filas.reduce(
        (a, p) => a + p.paradas_hoy,
        0,
    );
}

function pintarPie() {
    const partes = [];
    if (estado.fuentes.db_edad_s != null) {
        partes.push(
            `acumulados hace ${Math.round(estado.fuentes.db_edad_s)} s`,
        );
    }
    partes.push(`${estado.puestos.size} puestos`);
    $("pie").textContent = partes.join(" · ");
}

function pintarTodo() {
    aviso();
    pintarNoria();
    pintarPlcs();
    pintarGrilla();
    pintarTotales();
    pintarPie();
}

function aplicarSnapshot(s) {
    estado.offset = s.server_epoch_ms - Date.now();
    estado.ultimoHb = s.server_epoch_ms;
    estado.objetivo = s.objetivo_cab_h || 220;
    estado.noria = s.noria;
    estado.plcs = s.plcs;
    estado.fuentes = { ...s.fuentes, daemon_alive: s.daemon_alive };
    estado.vivo = s.fuentes.redis === "ok";
    estado.puestos = new Map(s.puestos.map((p) => [p.puesto_key, p]));
    pintarTodo();
}

function aplicarDelta(d) {
    switch (d.t) {
        case "speed":
            estado.noria = { running: d.running, vel: d.vel, frec: d.frec };
            pintarNoria();
            break;

        case "noria":
            estado.noria = { ...estado.noria, running: d.running };
            pintarNoria();
            break;

        case "input": {
            const p = estado.puestos.get(d.puesto_key);
            if (!p) break;
            // Misma regla que LiveMirror._advance en el daemon: un flanco arranca el
            // cronometro, un re-assert no lo reinicia, y un resync que cambia el valor deja la
            // duracion como cota inferior.
            if (d.reason === "change") {
                p.desde_ms = d.ts_ms;
                p.exacto = true;
            } else if (p.activo !== d.v) {
                p.desde_ms = d.ts_ms;
                p.exacto = d.reason !== "resync";
            }
            p.activo = d.v;
            pintarGrilla();
            pintarTotales();
            break;
        }

        case "hoy":
            for (const fila of d.puestos) {
                const p = estado.puestos.get(fila.puesto_key);
                if (p) {
                    p.segundos_hoy = fila.segundos_hoy;
                    p.paradas_hoy = fila.paradas_hoy;
                }
            }
            pintarGrilla();
            pintarTotales();
            break;

        case "resync":
            // La cola de este cliente se lleno: en vez de cortarlo, se le pide el estado
            // entero, que sale de cache y no toca la base.
            fetch("/api/snapshot")
                .then((r) => r.json())
                .then(aplicarSnapshot);
            break;
    }
}

function aplicarHb(h) {
    estado.offset = h.server_epoch_ms - Date.now();
    estado.ultimoHb = h.server_epoch_ms;
    const eraVivo = estado.vivo;
    estado.vivo = h.redis === "ok";
    estado.fuentes = { ...estado.fuentes, ...h };

    // El vencimiento de un TTL no notifica a nadie: la salud de cada PLC llega aca.
    let cambio = estado.vivo !== eraVivo;
    for (const plc of estado.plcs) {
        const vivo = h.plcs[plc.ip] ?? false;
        if (plc.alive !== vivo) {
            plc.alive = vivo;
            cambio = true;
        }
    }
    if (cambio) {
        // Un PLC que dejo de contestar deja a sus puestos en desconocido, no en libre.
        for (const p of estado.puestos.values()) {
            const plc = estado.plcs.find((x) => x.ip === p.plc_ip);
            if (plc && !plc.alive) p.activo = null;
        }
        pintarTodo();
    } else {
        aviso();
        pintarPie();
    }
}

function conectar() {
    const es = new EventSource("/stream");
    es.addEventListener("snapshot", (e) => aplicarSnapshot(JSON.parse(e.data)));
    es.addEventListener("delta", (e) => aplicarDelta(JSON.parse(e.data)));
    es.addEventListener("hb", (e) => aplicarHb(JSON.parse(e.data)));
    es.onerror = () => {
        estado.vivo = false;
        aviso();
    };
}

setInterval(tick, 250);
// Acota las fugas de un tab abierto doce horas, pero nunca en plena incidencia.
setInterval(
    () => {
        const alguienRetiene = [...estado.puestos.values()].some(
            (p) => p.activo,
        );
        if (!alguienRetiene) location.reload();
    },
    6 * 60 * 60 * 1000,
);

conectar();
