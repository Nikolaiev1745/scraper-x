"""Orquestador compartido: auth -> scrape -> export.

Lo usan tanto la CLI ([main.py](main.py)) como el frontend web ([app.py](app.py)),
para no duplicar la logica. Traduce las etiquetas de tipo de la UI ("tweets",
"retweets", "replies") a los tipos internos del parser y deriva que tabs hace
falta recorrer.
"""
from __future__ import annotations

import glob
import json
import re
from datetime import datetime, timezone

import twscrape

import config
from auth import get_api
from export import export_consolidated, summarize
from parser import parse_tweet_detail
from scraper import (
    _parse_created_at,
    parse_since,
    parse_until,
    scrape,
    scrape_search,
)

# X devuelve errores transitorios (500 / DeadlineExceeded) que twscrape reintenta
# solo; los bajamos a ERROR para no llenar la consola de WARNINGs ruidosos.
try:
    twscrape.set_log_level("ERROR")
except Exception:  # noqa: BLE001 - si cambia la API de logging, no es critico.
    pass

# Etiqueta de la UI/CLI -> tipos internos que produce el parser.
TYPE_GROUPS: dict[str, set[str]] = {
    "tweets": {"tweet", "quote"},
    "retweets": {"retweet"},
    "replies": {"reply"},
}
DEFAULT_TYPE_LABELS = ("tweets", "retweets", "replies")


def expand_types(labels: list[str]) -> frozenset[str]:
    """Convierte etiquetas ('tweets', 'replies', ...) a tipos internos."""
    out: set[str] = set()
    for label in labels:
        out |= TYPE_GROUPS.get(label, set())
    return frozenset(out)


def parse_ids(raw: str) -> list[str]:
    """Extrae ids numericos de un texto (separados por coma, espacio o salto)."""
    return re.findall(r"\d{5,}", raw or "")


# X solo admite letras, digitos y guion bajo en el @usuario, hasta 15 caracteres.
# El patron es exacto, no una heuristica: deja pasar cualquier usuario real y
# descarta todo lo demas.
_RE_USUARIO = re.compile(r"^[A-Za-z0-9_]{1,15}$")


def validar_usuario(target: str) -> str:
    """Normaliza un @usuario de X (quita la arroba) y lo valida.

    No es solo cosmetico. El valor se interpola sin más en los nombres de
    archivo y en los globs sobre la carpeta de datos -- `{usuario}_all.json`,
    `{usuario}_*.json` -- asi que un objetivo con `..` o con un separador de
    ruta hace que la herramienta lea, escriba y borre FUERA de la carpeta que
    el usuario eligio. En una herramienta forense, escribir en el lugar
    equivocado en silencio es peor que no escribir.

    Todo lo que llega del formulario web o de la CLI pasa por aca, asi que el
    alcance del nombre queda acotado en un solo lugar.
    """
    limpio = (target or "").strip().lstrip("@").strip()
    if not limpio:
        raise ValueError("Indica un usuario objetivo.")
    if not _RE_USUARIO.match(limpio):
        raise ValueError(
            f"'{limpio}' no es un usuario de X valido. Solo admite letras, "
            "digitos y guion bajo, hasta 15 caracteres, y sin arroba."
        )
    return limpio


async def add_by_ids(
    ids: list[str],
    formats: list[str] | None = None,
    cookies: str | None = None,
) -> dict:
    """Baja tweets por id (uno por uno) y los fusiona al consolidado de su autor.

    Sirve para recuperar tweets que X no lista en el timeline ni en la busqueda
    (p.ej. ciertas replies), pero si son accesibles por id directo.
    """
    ids = list(dict.fromkeys(ids))  # dedup preservando orden
    if not ids:
        raise ValueError("No se reconocio ningun id. Pega ids numericos de tweets.")

    formats = formats or ["json", "csv"]
    api = await get_api(cookies=cookies)

    by_author: dict[str, list[dict]] = {}
    not_found: list[str] = []
    for tid in ids:
        try:
            rep = await api.tweet_details_raw(int(tid))
            rec = parse_tweet_detail(rep.json(), tid)
        except Exception:  # noqa: BLE001 - cualquier fallo => no encontrado
            rec = None
        if rec is None:
            not_found.append(tid)
            continue
        by_author.setdefault(rec["screen_name"] or "desconocido", []).append(rec)

    written: list[str] = []
    added_total = 0
    per_author: list[str] = []
    for author, recs in by_author.items():
        w, added, total = export_consolidated(recs, author, formats)
        written.extend(str(p) for p in w)
        added_total += added
        per_author.append(f"@{author}: +{added} (total {total})")

    summary = (f"Agregados {added_total} de {len(ids)} ids"
               + (f" | {'; '.join(per_author)}" if per_author else "")
               + (f" | no encontrados: {len(not_found)}" if not_found else ""))
    return {
        "count": added_total,
        "written": written,
        "not_found": not_found,
        "summary": summary,
    }


def find_last_state(target: str, directory=None) -> tuple[set[str], datetime | None]:
    """Lee los .json previos de `target` y devuelve (ids, fecha_max).

    Sirve al modo actualizar: los ids ya bajados se saltan y la fecha del tweet
    mas reciente marca desde donde traer lo nuevo.

    `directory`: carpeta donde buscar. Por defecto, la carpeta de salida
    configurada. Se puede pasar explicita para consultar otra sin tocar el
    estado global (lo usa el endpoint /estado del frontend).
    """
    seen: set[str] = set()
    last: datetime | None = None
    carpeta = config.OUTPUT_DIR if directory is None else directory
    pattern = str(carpeta / f"{target}_*.json")
    for filepath in glob.glob(pattern):
        try:
            data = json.loads(open(filepath, encoding="utf-8").read())
        except (OSError, ValueError):
            continue
        for rec in data:
            rid = rec.get("id")
            if rid:
                seen.add(rid)
            dt = _parse_created_at(rec.get("created_at", ""))
            if dt is not None and (last is None or dt > last):
                last = dt
    return seen, last


def tabs_for(types: frozenset[str]) -> list[str]:
    """Decide que tabs recorrer segun los tipos pedidos.

    La tab 'replies' (user_tweets_and_replies) incluye posts + retweets +
    respuestas, asi que solo se usa cuando se piden respuestas; en otro caso
    alcanza con 'posts'.
    """
    return ["replies"] if "reply" in types else ["posts"]


async def run_job(
    *,
    target: str,
    type_labels: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    max_tweets: int | None = None,
    formats: list[str] | None = None,
    cookies: str | None = None,
    historical: bool = False,
    window_days: int = 7,
    update: bool = False,
) -> dict:
    """Ejecuta una corrida completa y devuelve un resumen con las rutas escritas.

    - `update=True`: mira las descargas previas de la cuenta en la carpeta de
      salida configurada y trae solo lo nuevo (mas reciente que el ultimo tweet
      ya bajado). Usa el timeline, asi que incluye retweets nuevos.
    - `historical=True`: usa el endpoint de busqueda por ventanas de fecha para
      esquivar el tope de ~3200 del timeline (requiere `since`).
    - Si no, timeline del perfil normal.

    Lanza ValueError si los parametros son invalidos (target vacio, usuario mal
    formado, sin tipos, fechas mal formadas).
    """
    target = validar_usuario(target)

    labels = type_labels or list(DEFAULT_TYPE_LABELS)
    types = expand_types(labels)
    if not types:
        raise ValueError("Selecciona al menos un tipo (tweets, retweets o replies).")

    since_dt = parse_since(since)
    until_dt = parse_until(until)
    if since_dt and until_dt and since_dt > until_dt:
        raise ValueError("La fecha 'desde' no puede ser posterior a 'hasta'.")

    formats = formats or ["json", "csv"]

    api = await get_api(cookies=cookies)

    # Aviso de rango incompleto por el tope de ~3200 del timeline. Solo aplica al
    # modo descarga completa (update no pide rango hacia atras; historical ya
    # esquiva el tope por busqueda).
    nota_truncado = ""

    if update:
        # Directorio explicito: no depender de que el llamador haya seteado
        # antes el global config.OUTPUT_DIR como efecto colateral.
        seen_ids, last_date = find_last_state(target, config.OUTPUT_DIR)
        if last_date is None:
            raise ValueError(
                f"No hay descargas previas de @{target} para actualizar.\n"
                f"Carpeta consultada: {config.OUTPUT_DIR}\n"
                "Revisa que la carpeta sea la correcta, o usa una descarga completa."
            )
        print(f"[update] Ultimo registro de @{target}: {last_date:%Y-%m-%d %H:%M} "
              f"| ya conocidos: {len(seen_ids)}. Trayendo lo nuevo...")
        records, _ = await scrape(
            api, target, tabs_for(types),
            max_tweets=max_tweets, since=last_date, until=None, types=types,
            known_ids=seen_ids,
        )
    elif historical:
        if since_dt is None:
            raise ValueError(
                "El modo historial necesita una fecha 'Desde' (el rango a recorrer)."
            )
        until_eff = until_dt or datetime.now(timezone.utc)
        records = await scrape_search(
            api, target, since_dt, until_eff,
            max_tweets=max_tweets, types=types, window_days=window_days,
        )
    else:
        records, truncated = await scrape(
            api, target, tabs_for(types),
            max_tweets=max_tweets, since=since_dt, until=until_dt, types=types,
        )
        # X corta el timeline del perfil alrededor de los ~3200 tweets mas
        # recientes: si pedimos rango hacia atras y no llegamos a `since`, la
        # corrida quedo incompleta aunque no haya error. Para no reportar "listo"
        # con un hueco gigante, completamos por busqueda historica, que esquiva
        # ese tope. Solo ella no trae retweets, asi que el tramo de retweets se
        # queda en lo que el timeline alcanzo.
        if truncated and since_dt is not None:
            historicos = types - {"retweet"}
            if historicos:
                msg = (
                    f"El timeline de @{target} se cortó antes de "
                    f"{since_dt:%Y-%m-%d} (tope ~3200 de X); se completó con "
                    "búsqueda histórica"
                )
                if "retweet" in types:
                    msg += ", los retweets llegan solo hasta donde alcanzó el timeline"
                msg += "."
                print(f"[fallback] {msg}")
                nota_truncado = msg
                until_eff = until_dt or datetime.now(timezone.utc)
                extra = await scrape_search(
                    api, target, since_dt, until_eff,
                    max_tweets=max_tweets, types=frozenset(historicos),
                    window_days=window_days,
                )
                ya = {r["id"] for r in records}
                records.extend(r for r in extra if r["id"] not in ya)
                if max_tweets is not None:
                    records = records[:max_tweets]
            else:
                nota_truncado = (
                    f"Rango incompleto: el timeline de @{target} se cortó antes de "
                    f"{since_dt:%Y-%m-%d} (tope ~3200 de X) y solo pediste retweets, "
                    "que la búsqueda de X no devuelve. Pedí también tweets o replies "
                    "para llegar más atrás."
                )
                print(f"[aviso] {nota_truncado}")

    # Siempre consolidamos: aunque esta corrida no traiga nada nuevo, el master
    # se materializa/actualiza (absorbe descargas sueltas previas).
    written, added, total = export_consolidated(records, target, formats)

    if records:
        summary = (f"{summarize(records)} — {added} nuevos al consolidado "
                   f"(total acumulado: {total})")
    elif total:
        summary = f"Sin tweets nuevos. Consolidado: {total} registros."
    else:
        summary = "Sin resultados."

    return {
        "target": target,
        "count": len(records),
        "added": added,
        "total": total,
        "summary": summary,
        # Solo si el rango quedo incompleto por el tope de X. La web lo muestra
        # aparte: si no, el aviso se pierde dentro del bloque verde de "listo".
        "aviso": nota_truncado or "",
        "written": [str(p) for p in written],
        "records": records,
    }
