"""Recoleccion de tweets/replies de un perfil via la API GraphQL (twscrape).

Por cada tab (posts / replies):
  - paginamos el timeline con el metodo _raw correspondiente,
  - cada pagina es un payload GraphQL igual al que antes capturaba el
    interceptor, asi que lo parseamos con `parse_responses` sin cambios,
  - deduplicamos y filtramos por tipo y por rango de fechas (since/until),
  - cortamos por --max-tweets o al pasar la fecha de corte inferior (--since).

twscrape maneja internamente los rate-limits (espera/rota cuentas), por lo que
ya no hace falta backoff manual.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from twscrape import API

import config
from parser import parse_responses, parse_search_responses

# Todos los tipos que clasifica el parser.
ALL_TYPES = frozenset({"tweet", "retweet", "reply", "quote"})


class ConexionXError(RuntimeError):
    """No se pudo obtener datos de X.

    Se lanza cuando la API no responde o responde vacio por un problema de
    sesion/entorno (cookies vencidas, twscrape desactualizado, red), en vez de
    devolver 0 registros en silencio: sin esto, un fallo de conexion se
    confunde con un "no hay tweets nuevos".
    """


AYUDA_CONEXION = (
    "Causas frecuentes:\n"
    "  1) Cookies vencidas: pega un auth_token y un ct0 nuevos (DevTools > "
    "Application > Cookies en x.com).\n"
    "  2) twscrape desactualizado (X cambia su frontend): corre "
    f"'{config.VENV_PYTHON_HINT} -m pip install -U twscrape'.\n"
    "  3) Sin conexion a internet o X bloqueando la cuenta del scraper."
)


def _parse_created_at(value: str) -> datetime | None:
    """Parsea el formato de fecha de X: 'Wed Oct 10 20:19:24 +0000 2018'."""
    try:
        return datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y")
    except (ValueError, TypeError):
        return None


async def scrape_tab(
    api: API,
    user_id: int,
    tab: str,
    seen: set[str],
    records: list[dict],
    max_tweets: int | None,
    since: datetime | None,
    until: datetime | None,
    types: frozenset[str],
) -> tuple[int, bool]:
    """Scrapea una tab del perfil, agregando registros nuevos a `records`.

    Devuelve (paginas_que_respondieron, truncado_por_tope_de_x):
    - paginas == 0 => la API no devolvio nada, sintoma de sesion caida.
    - truncado True => se pidio bajar de `since`, el timeline se agoto y nunca
      llego a esa fecha: X solo deja recorrer los ~3200 tweets mas recientes
      del perfil, asi que el rango quedo incompleto. El llamador decide si
      completa con busqueda historica.
    """
    method = getattr(api, config.TAB_METHODS[tab])

    print(f"\n[scraper] === Tab '{tab}' ({config.TAB_METHODS[tab]}) ===")
    added_in_tab = 0
    pages = 0

    # limit=-1: dejamos que twscrape pagine hasta el final; el corte real lo
    # manejamos nosotros (max_tweets / since) para que cuente ya filtrado.
    reached_cutoff = False
    stopped_at_max = False
    async for rep in method(user_id, limit=-1):
        pages += 1
        # rep es un httpx.Response; rep.json() es el payload GraphQL crudo.
        parsed = parse_responses([rep.json()])

        new_count = 0
        for rec in parsed:
            if rec["id"] in seen:
                continue
            dt = _parse_created_at(rec["created_at"])
            # Techo: mas nuevo que --until -> lo salteamos (estan arriba del rango).
            if until is not None and dt is not None and dt > until:
                continue
            # Piso: mas viejo que --since -> marcamos corte y salteamos.
            if since is not None and dt is not None and dt < since:
                reached_cutoff = True
                continue

            seen.add(rec["id"])
            new_count += 1
            if rec["type"] in types:
                records.append(rec)
                added_in_tab += 1

        if added_in_tab:
            print(f"[scraper]   total tab: {added_in_tab} (del tipo pedido)")

        # Condiciones de corte.
        if max_tweets is not None and len(records) >= max_tweets:
            print(f"[scraper] Alcanzado max-tweets ({max_tweets}).")
            stopped_at_max = True
            break
        # Timeline en orden cronologico inverso: si esta pagina ya cayo bajo el
        # corte y no aporto nada nuevo en rango, lo que sigue es aun mas viejo.
        if reached_cutoff and new_count == 0:
            print("[scraper] Alcanzada la fecha de corte (--since).")
            break

    # Cortamos nosotros (por max-tweets), no se agoto el timeline de X: el rango
    # quedo incompleto por decision del usuario, no por el tope de ~3200.
    truncated = (since is not None and not reached_cutoff
                 and not stopped_at_max and pages > 0)
    return pages, truncated


async def scrape(
    api: API,
    target_user: str,
    tabs: list[str],
    max_tweets: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    types: frozenset[str] = ALL_TYPES,
    known_ids: set[str] | None = None,
) -> tuple[list[dict], bool]:
    """Scrapea las tabs indicadas y devuelve registros deduplicados/filtrados.

    `known_ids`: ids ya descargados en corridas previas; se pre-cargan para
    saltarlos (modo actualizar), de modo que `records` traiga solo lo nuevo.

    El segundo valor de la tupla es `truncado`: True cuando se pidio un rango
    hacia atras y el timeline se agoto antes de llegar a `since` (el tope de
    ~3200 de X). No es un error, pero avisa que al registro le falta la parte
    mas antigua del rango.
    """
    user = await api.user_by_login(target_user)
    if user is None:
        # Ojo: X responde igual (nada) si la sesion esta caida que si el usuario
        # no existe. Cortamos con error para no confundirlo con "sin novedades".
        raise ConexionXError(
            f"No se pudo consultar a @{target_user} en X: la API no devolvio "
            f"datos.\n{AYUDA_CONEXION}\n"
            f"  4) Menos probable: @{target_user} no existe, cambio de nombre "
            "o fue suspendido."
        )

    seen: set[str] = set(known_ids) if known_ids else set()
    records: list[dict] = []
    total_pages = 0

    truncated_total = False
    for tab in tabs:
        if tab not in config.TAB_METHODS:
            print(f"[scraper] Tab desconocida '{tab}', la salteo.")
            continue
        p, t = await scrape_tab(api, user.id, tab, seen, records,
                                max_tweets, since, until, types)
        total_pages += p
        truncated_total = truncated_total or t
        if max_tweets is not None and len(records) >= max_tweets:
            break

    # La API respondio para el usuario pero ni una pagina del timeline: no es
    # "sin novedades", es que la conexion/sesion fallo a mitad de camino.
    if total_pages == 0:
        raise ConexionXError(
            f"X no devolvio ninguna pagina del timeline de @{target_user} "
            f"(el perfil si resolvio).\n{AYUDA_CONEXION}"
        )

    if max_tweets is not None:
        records = records[:max_tweets]
    return records, truncated_total


async def scrape_search(
    api: API,
    target_user: str,
    since: datetime,
    until: datetime,
    max_tweets: int | None = None,
    types: frozenset[str] = ALL_TYPES,
    window_days: int = 7,
) -> list[dict]:
    """Historial por fechas via el endpoint de busqueda (esquiva el tope ~3200).

    Parte el rango [since, until] en ventanas y consulta
    `from:usuario since:A until:B` en cada una, deduplicando. Esto llega mucho
    mas atras que el timeline del perfil.

    Limitacion: la busqueda de X NO devuelve retweets, asi que el historial trae
    tweets propios, respuestas y citas, pero no los retweets.
    """
    seen: set[str] = set()
    records: list[dict] = []
    pages = 0

    # since: inclusiva | until de X: exclusiva -> +1 dia para incluir el dia tope.
    start_date = since.date()
    cur_until = until.date() + timedelta(days=1)
    step = timedelta(days=max(1, window_days))

    print(f"\n[search] Historial de @{target_user} "
          f"{start_date}..{until.date()} (ventanas de {window_days}d)")

    while cur_until > start_date:
        cur_since = max(start_date, cur_until - step)
        q = f"from:{target_user} since:{cur_since} until:{cur_until}"

        before = len(records)
        async for rep in api.search_raw(q, limit=-1, kv={"product": "Latest"}):
            pages += 1
            for rec in parse_search_responses([rep.json()]):
                if rec["id"] in seen:
                    continue
                seen.add(rec["id"])
                if rec["type"] in types:
                    records.append(rec)
            if max_tweets is not None and len(records) >= max_tweets:
                break

        print(f"[search]   {cur_since}..{cur_until}: "
              f"+{len(records) - before} (total {len(records)})")

        if max_tweets is not None and len(records) >= max_tweets:
            break
        cur_until = cur_since

    # Ni una respuesta en todo el rango => la busqueda no llego a X.
    if pages == 0:
        raise ConexionXError(
            f"X no respondio a ninguna busqueda de @{target_user} en el rango "
            f"{start_date}..{until.date()}.\n{AYUDA_CONEXION}"
        )

    if max_tweets is not None:
        records = records[:max_tweets]
    return records


def parse_since(value: str | None) -> datetime | None:
    """Convierte 'YYYY-MM-DD' a datetime UTC al inicio del dia (o None)."""
    if not value:
        return None
    dt = datetime.strptime(value, "%Y-%m-%d")
    return dt.replace(tzinfo=timezone.utc)


def parse_until(value: str | None) -> datetime | None:
    """Convierte 'YYYY-MM-DD' a datetime UTC al final del dia, inclusive (o None)."""
    if not value:
        return None
    dt = datetime.strptime(value, "%Y-%m-%d")
    return dt.replace(hour=23, minute=59, second=59, microsecond=999999,
                      tzinfo=timezone.utc)
