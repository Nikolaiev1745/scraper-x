"""Configuracion central y constantes del scraper."""
from __future__ import annotations

import functools
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

# Carga .env (si existe) al importar este modulo.
load_dotenv()

# --- Plataforma ---
IS_WINDOWS = os.name == "nt"
# Como se invoca el Python del entorno virtual en este sistema. Se usa en los
# mensajes de ayuda al usuario (ver scraper.AYUDA_CONEXION) y en el README.
VENV_PYTHON_HINT = r".\.venv\Scripts\python.exe" if IS_WINDOWS else "./.venv/bin/python"

# --- Rutas ---
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "output"

# Ruta absoluta estilo Windows ("C:\Casos", "D:/Casos") o con backslashes.
_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")

# Rutas equivalentes entre Windows y WSL:
#   Windows:  C:\Users\nicol\Casos      (o C:/Users/nicol/Casos)
#   WSL:      /mnt/c/Users/nicol/Casos
# La herramienta se usa en las dos plataformas con el mismo .env, asi que se
# traducen solas en vez de obligar a editar la ruta cada vez que se cambia de
# sistema. Solo se traduce lo que es inequivoco (una letra de unidad).
_WSL_MNT_RE = re.compile(r"^/mnt/([A-Za-z])(?:/(.*))?$")
_WINDOWS_ABS_RE = re.compile(r"^([A-Za-z]):[\\/](.*)$")


@functools.lru_cache(maxsize=1)
def _is_wsl() -> bool:
    """True si este proceso corre dentro de WSL (Linux sobre Windows)."""
    if IS_WINDOWS:
        return False
    try:
        version = Path("/proc/version").read_text(
            encoding="utf-8", errors="replace"
        ).lower()
    except OSError:
        return False
    return "microsoft" in version or "wsl" in version


def adapt_path(value: str | Path) -> Path:
    """Traduce una ruta entre Windows y WSL cuando es inequivoca.

    El .env y `recientes.json` se comparten entre una consola WSL y Windows.
    Sin esto, al abrir la herramienta con doble clic en Windows una ruta como
    '/mnt/c/Users/nicol/Casos' se interpretaba como 'C:\\mnt\\c\\Users\\...' y
    las descargas terminaban en una carpeta equivocada (o fallaban).

    En Windows:  /mnt/c/Users/...  ->  C:\\Users\\...
    En WSL:      C:\\Users\\...     ->  /mnt/c/Users/...

    Cualquier otra ruta se devuelve tal cual. La conversion solo se hace si la
    unidad de destino existe, para no inventar rutas a partir de montajes
    internos de WSL (/mnt/wsl, /mnt/wslg).
    """
    crudo = str(value).strip()
    if not crudo:
        return Path(crudo)

    if IS_WINDOWS:
        m = _WSL_MNT_RE.match(crudo)
        if m:
            letra = m.group(1).upper()
            if Path(f"{letra}:\\").exists():
                resto = (m.group(2) or "").replace("/", "\\")
                return Path(f"{letra}:\\{resto}" if resto else f"{letra}:\\")
        return Path(crudo)

    if _is_wsl():
        m = _WINDOWS_ABS_RE.match(crudo)
        if m:
            letra = m.group(1).lower()
            if Path(f"/mnt/{letra}").exists():
                resto = m.group(2).replace("\\", "/")
                return Path(f"/mnt/{letra}/{resto}" if resto else f"/mnt/{letra}")
    return Path(crudo)


def _is_windows_path(value: str) -> bool:
    """True si `value` parece una ruta de Windows y no estamos en Windows.

    El .env se comparte entre Windows y WSL/Linux. En WSL la ruta se traduce
    sola (ver `adapt_path`). En un POSIX nativo, en cambio, 'C:\\Casos\\x' no
    falla: Path() lo toma como UN solo nombre de carpeta relativo y
    ensure_output_dir() crea un directorio con backslashes en el nombre dentro
    del cwd. Para una herramienta forense, escribir en el lugar equivocado en
    silencio es peor que no escribir, asi que lo detectamos y avisamos.
    """
    if IS_WINDOWS or not value:
        return False
    return bool(_WINDOWS_PATH_RE.match(value)) or "\\" in value


def _output_dir_from_env() -> Path:
    """Carpeta de salida segun el .env, adaptada al sistema actual.

    Si el .env trae una ruta WSL y estamos en Windows (o al reves), se traduce
    automaticamente: el mismo archivo sirve para abrir la herramienta desde
    Windows con doble clic y desde una consola WSL.
    """
    raw = os.getenv("SCRAPER_OUTPUT_DIR", "").strip()
    if not raw:
        return DEFAULT_OUTPUT_DIR
    ruta = adapt_path(raw)
    if _is_windows_path(str(ruta)):
        print(
            f"[config] AVISO: SCRAPER_OUTPUT_DIR tiene una ruta de Windows "
            f"('{raw}') y este sistema no es Windows. Se ignora y se usa "
            f"{DEFAULT_OUTPUT_DIR}. Corregila en el .env o desde la web.",
            file=sys.stderr,
        )
        return DEFAULT_OUTPUT_DIR
    return ruta.expanduser()


# Carpeta donde se guardan las descargas. Por defecto queda dentro de la
# herramienta, pero conviene apuntarla a la carpeta del caso que se investiga
# (SCRAPER_OUTPUT_DIR en el .env, o --output-dir en la CLI) para que el codigo
# quede libre de datos de cualquier cuenta.
OUTPUT_DIR = _output_dir_from_env()
# Sesion guardada por Playwright en versiones previas; se reusa para extraer
# las cookies (auth_token / ct0) y darle de alta la cuenta en twscrape.
STATE_FILE = BASE_DIR / "state.json"
# Base SQLite donde twscrape persiste las cuentas/sesiones.
ACCOUNTS_DB = BASE_DIR / "accounts.db"

# --- Credenciales (desde .env) ---
LOGIN_USER = os.getenv("X_LOGIN_USER", "").strip()
LOGIN_PASSWORD = os.getenv("X_LOGIN_PASSWORD", "").strip()
LOGIN_VERIFY = os.getenv("X_LOGIN_VERIFY", "").strip()
DEFAULT_TARGET = os.getenv("TARGET_USERNAME", "").strip()
# Cookies directas (opcional): "auth_token=...; ct0=...". Si se define, es el
# camino preferido para dar de alta la cuenta (evita login/2FA/captcha).
LOGIN_COOKIES = os.getenv("X_COOKIES", "").strip()

# --- X / URLs ---
BASE_URL = "https://x.com"

# Mapeo de tabs CLI -> metodo de timeline de twscrape (variante _raw).
# "posts"   -> tweets del perfil; "replies" -> tweets + respuestas.
TAB_METHODS = {
    "posts": "user_tweets_raw",
    "replies": "user_tweets_and_replies_raw",
}


def ensure_output_dir() -> Path:
    """Crea la carpeta de salida si no existe y devuelve su ruta.

    Lee OUTPUT_DIR en cada llamada, asi respeta el override que hacen la CLI
    (--output-dir) o el frontend web antes de arrancar la corrida.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def normalize_output_dir(path: str | Path) -> Path:
    """Normaliza una ruta de carpeta de salida sin tocar el estado global.

    Expande `~`, resuelve relativas y symlinks, y rechaza rutas de Windows
    fuera de Windows (ver `_is_windows_path`).

    Existe para que el frontend y la corrida coincidan siempre sobre QUE carpeta
    estan hablando: antes /estado usaba `Path(raw)` crudo mientras la corrida
    normalizaba, asi que escribir '~/Casos/x' hacia que la interfaz dijera "la
    carpeta no existe" y bloqueara el modo actualizar, aunque la descarga si
    hubiera funcionado.

    Lanza ValueError si la ruta es de Windows y este sistema no lo es.
    """
    crudo = str(path).strip()
    ruta = adapt_path(crudo)
    if _is_windows_path(str(ruta)):
        ejemplo = Path.home() / "Casos" / "expediente"
        raise ValueError(
            f"'{crudo}' es una ruta de Windows y este sistema no es Windows. "
            f"Usa una ruta local (por ejemplo {ejemplo})."
        )
    return ruta.expanduser().resolve()


def set_output_dir(path: str | Path) -> Path:
    """Cambia la carpeta de salida en runtime (CLI / web) y la crea.

    Es el camino por donde entra lo que el usuario tipea en --output-dir y en el
    formulario web; la validacion vive en `normalize_output_dir`.
    """
    global OUTPUT_DIR
    OUTPUT_DIR = normalize_output_dir(path)
    return ensure_output_dir()
