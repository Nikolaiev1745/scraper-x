"""Normaliza el JSON GraphQL crudo de X a registros planos.

La forma del JSON de X cambia seguido y tiene varios envoltorios; este modulo
navega de forma defensiva (con .get encadenado) para no romperse si falta una
clave. Cada tweet se convierte en un dict plano listo para exportar.
"""
from __future__ import annotations

from typing import Any, Iterable


def _walk_instructions(payload: dict) -> Iterable[dict]:
    """Genera cada 'entry' de las instrucciones del timeline en una respuesta."""
    # Ruta tipica: data.user.result.timeline_v2.timeline.instructions
    user = (
        payload.get("data", {})
        .get("user", {})
        .get("result", {})
    )
    # El timeline puede estar como timeline_v2 o timeline.
    timeline = (
        user.get("timeline_v2", {}).get("timeline")
        or user.get("timeline", {}).get("timeline")
        or {}
    )
    for instruction in timeline.get("instructions", []):
        if instruction.get("type") == "TimelineAddEntries":
            yield from instruction.get("entries", [])
        # Algunos pinned/replace vienen como TimelinePinEntry.
        elif instruction.get("type") == "TimelinePinEntry":
            entry = instruction.get("entry")
            if entry:
                yield entry


def _walk_search_instructions(payload: dict) -> Iterable[dict]:
    """Genera cada 'entry' del timeline de busqueda (SearchTimeline)."""
    # Ruta: data.search_by_raw_query.search_timeline.timeline.instructions
    timeline = (
        payload.get("data", {})
        .get("search_by_raw_query", {})
        .get("search_timeline", {})
        .get("timeline", {})
        or {}
    )
    for instruction in timeline.get("instructions", []):
        if instruction.get("type") == "TimelineAddEntries":
            yield from instruction.get("entries", [])


def _iter_tweet_results(entry: dict) -> Iterable[dict]:
    """Extrae el/los tweet_results.result de una entry (item o module)."""
    content = entry.get("content", {})
    etype = content.get("entryType")

    if etype == "TimelineTimelineItem":
        res = content.get("itemContent", {}).get("tweet_results", {}).get("result")
        if res:
            yield res
    elif etype == "TimelineTimelineModule":
        # Conversaciones / hilos: varios items dentro.
        for item in content.get("items", []):
            res = (
                item.get("item", {})
                .get("itemContent", {})
                .get("tweet_results", {})
                .get("result")
            )
            if res:
                yield res


def _unwrap_tweet(result: dict) -> dict | None:
    """Desenvuelve TweetWithVisibilityResults y valida que sea un Tweet."""
    if result.get("__typename") == "TweetWithVisibilityResults":
        result = result.get("tweet", {})
    if result.get("__typename") == "TweetTombstone":
        return None
    if "legacy" not in result:
        return None
    return result


def _screen_name(result: dict) -> str:
    """Obtiene el @handle del autor, tolerando variantes de estructura."""
    user = result.get("core", {}).get("user_results", {}).get("result", {})
    # Estructura nueva: user.core.screen_name; vieja: user.legacy.screen_name.
    return (
        user.get("core", {}).get("screen_name")
        or user.get("legacy", {}).get("screen_name")
        or ""
    )


def _extract_media(legacy: dict) -> list[str]:
    """URLs de imagenes y videos (variante mp4 de mayor bitrate)."""
    urls: list[str] = []
    media = legacy.get("extended_entities", {}).get("media", []) or \
        legacy.get("entities", {}).get("media", [])
    for m in media:
        mtype = m.get("type")
        if mtype == "photo":
            if m.get("media_url_https"):
                urls.append(m["media_url_https"])
        elif mtype in ("video", "animated_gif"):
            variants = m.get("video_info", {}).get("variants", [])
            mp4s = [v for v in variants if v.get("content_type") == "video/mp4"]
            if mp4s:
                best = max(mp4s, key=lambda v: v.get("bitrate", 0))
                urls.append(best["url"])
            elif m.get("media_url_https"):
                urls.append(m["media_url_https"])
    return urls


def _classify(legacy: dict) -> str:
    """Determina el tipo: retweet / reply / quote / tweet."""
    if legacy.get("retweeted_status_result"):
        return "retweet"
    if legacy.get("in_reply_to_status_id_str"):
        return "reply"
    if legacy.get("is_quote_status"):
        return "quote"
    return "tweet"


def _views(result: dict) -> int | None:
    count = result.get("views", {}).get("count")
    try:
        return int(count) if count is not None else None
    except (TypeError, ValueError):
        return None


def parse_tweet(result: dict) -> dict | None:
    """Convierte un tweet_results.result en un registro plano. None si invalido."""
    result = _unwrap_tweet(result)
    if result is None:
        return None

    legacy = result.get("legacy", {})
    tweet_id = legacy.get("id_str") or result.get("rest_id")
    if not tweet_id:
        return None

    screen_name = _screen_name(result)
    # full_text largo vive en note_tweet (tweets > 280).
    note = (
        result.get("note_tweet", {})
        .get("note_tweet_results", {})
        .get("result", {})
        .get("text")
    )
    full_text = note or legacy.get("full_text", "")

    return {
        "id": tweet_id,
        "type": _classify(legacy),
        "created_at": legacy.get("created_at", ""),
        "screen_name": screen_name,
        "full_text": full_text,
        "lang": legacy.get("lang", ""),
        "in_reply_to_user": legacy.get("in_reply_to_screen_name", ""),
        "in_reply_to_status_id": legacy.get("in_reply_to_status_id_str", ""),
        "quoted_status_id": legacy.get("quoted_status_id_str", ""),
        "retweet_count": legacy.get("retweet_count", 0),
        "favorite_count": legacy.get("favorite_count", 0),
        "reply_count": legacy.get("reply_count", 0),
        "quote_count": legacy.get("quote_count", 0),
        "bookmark_count": legacy.get("bookmark_count", 0),
        "views": _views(result),
        "media_urls": _extract_media(legacy),
        "url": f"https://x.com/{screen_name}/status/{tweet_id}" if screen_name
        else f"https://x.com/i/status/{tweet_id}",
    }


def _records_from_entries(entries: Iterable[dict]) -> list[dict]:
    """Convierte una secuencia de entries del timeline en registros planos."""
    records: list[dict] = []
    for entry in entries:
        # Ignoramos entries de cursor / promoted.
        entry_id = entry.get("entryId", "")
        if entry_id.startswith(("cursor-", "promoted-", "who-to-follow")):
            continue
        for result in _iter_tweet_results(entry):
            rec = parse_tweet(result)
            if rec:
                records.append(rec)
    return records


def parse_responses(payloads: list[dict]) -> list[dict]:
    """Aplana respuestas del timeline de perfil (UserTweets) a registros."""
    records: list[dict] = []
    for payload in payloads:
        records.extend(_records_from_entries(_walk_instructions(payload)))
    return records


def parse_search_responses(payloads: list[dict]) -> list[dict]:
    """Aplana respuestas del timeline de busqueda (SearchTimeline) a registros."""
    records: list[dict] = []
    for payload in payloads:
        records.extend(_records_from_entries(_walk_search_instructions(payload)))
    return records


def _walk_conversation_instructions(payload: dict) -> Iterable[dict]:
    """Genera cada 'entry' de un tweet-detalle (conversacion con inyecciones)."""
    convo = (
        payload.get("data", {})
        .get("threaded_conversation_with_injections_v2", {})
        or {}
    )
    for instruction in convo.get("instructions", []):
        if instruction.get("type") == "TimelineAddEntries":
            yield from instruction.get("entries", [])


def parse_tweet_detail(payload: dict, wanted_id: str) -> dict | None:
    """Del payload de tweet_details, devuelve el registro del id pedido (o None)."""
    for rec in _records_from_entries(_walk_conversation_instructions(payload)):
        if rec["id"] == str(wanted_id):
            return rec
    return None
