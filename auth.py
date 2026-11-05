"""Provisiona la cuenta de X en twscrape y devuelve un API listo para usar.

twscrape pega directo a la API GraphQL de X (sin navegador) y persiste las
sesiones en una base SQLite (``accounts.db``). Esta capa reemplaza al login por
formulario con Playwright.

Estrategia de alta de la cuenta (la primera vez):
  1. Cookies explicitas en ``X_COOKIES`` del .env.
  2. Cookies extraidas del ``state.json`` que dejo la version Playwright.
  3. Fallback: credenciales del .env + ``login_all()`` (puede requerir acceso al
     email si X pide verificacion).
Una vez activa, la cuenta queda guardada y las corridas siguientes la reusan.
"""
from __future__ import annotations

import asyncio
import json

from twscrape import API

import config


def _cookies_from_state(path) -> str | None:
    """Arma 'auth_token=...; ct0=...' a partir del state.json de Playwright."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    wanted = {"auth_token": None, "ct0": None}
    for cookie in data.get("cookies", []):
        name = cookie.get("name")
        if name in wanted and wanted[name] is None:
            wanted[name] = cookie.get("value")

    if not wanted["auth_token"] or not wanted["ct0"]:
        return None
    return f"auth_token={wanted['auth_token']}; ct0={wanted['ct0']}"


def _resolve_cookies() -> str | None:
    """Cookies a usar para el alta: .env primero, luego state.json."""
    if config.LOGIN_COOKIES:
        return config.LOGIN_COOKIES
    if config.STATE_FILE.exists():
        return _cookies_from_state(config.STATE_FILE)
    return None


async def _has_active_account(api: API) -> bool:
    """True si ya hay al menos una cuenta activa en el pool."""
    accounts = await api.pool.get_all()
    return any(getattr(acc, "active", False) for acc in accounts)


async def _provision(api: API, cookies_override: str | None = None) -> None:
    """Da de alta la cuenta por cookies o, en su defecto, por credenciales."""
    username = config.LOGIN_USER or "scraper_account"
    cookies = cookies_override or _resolve_cookies()

    if cookies:
        print("[auth] Dando de alta la cuenta con cookies (auth_token/ct0).")
        # Las cookies con ct0 activan la cuenta de inmediato; no hace falta login.
        await api.pool.add_account(
            username,
            config.LOGIN_PASSWORD or "x",
            config.LOGIN_VERIFY or username,
            "x",
            cookies=cookies,
        )
        return

    if not config.LOGIN_USER or not config.LOGIN_PASSWORD:
        raise RuntimeError(
            "No hay cookies (X_COOKIES o state.json) ni credenciales en .env. "
            "Defini X_COOKIES='auth_token=...; ct0=...' o X_LOGIN_USER/PASSWORD."
        )

    print("[auth] Sin cookies; intentando login con credenciales del .env...")
    await api.pool.add_account(
        config.LOGIN_USER,
        config.LOGIN_PASSWORD,
        config.LOGIN_VERIFY or config.LOGIN_USER,
        config.LOGIN_PASSWORD,
    )
    await api.pool.login_all()


async def get_api(cookies: str | None = None) -> API:
    """Devuelve un API de twscrape con una cuenta activa.

    Si se pasan `cookies` y no hay cuenta activa, se usan para el alta (camino
    que toma el frontend cuando el usuario pega cookies nuevas). Si no, se reusa
    la cuenta ya guardada en accounts.db o se provisiona desde el .env/state.json.
    """
    api = API(str(config.ACCOUNTS_DB))
    if not await _has_active_account(api):
        await _provision(api, cookies_override=cookies)
        if not await _has_active_account(api):
            raise RuntimeError(
                "No se pudo activar ninguna cuenta. Revisa las cookies o las "
                "credenciales (X puede haber pedido 2FA/verificacion)."
            )
    else:
        print("[auth] Reusando cuenta activa de accounts.db.")
    return api


if __name__ == "__main__":
    # Prueba aislada del alta/activacion de la cuenta.
    async def _main() -> None:
        api = await get_api()
        info = await api.pool.accounts_info()
        print(f"[auth] Cuentas en el pool: {info}")

    asyncio.run(_main())
