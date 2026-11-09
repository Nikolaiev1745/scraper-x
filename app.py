"""Frontend web minimalista para ejecutar el scraper (Flask, local).

Levanta un mini servidor en http://127.0.0.1:5000 con un formulario que expone
todas las opciones del scraper: cookies (auth_token + ct0 separados), usuario
objetivo, rango de fechas, modo historial + tamano de ventana, tipos a incluir,
maximo y formatos. Al ejecutar corre `runner.run_job`, guarda los archivos en
la carpeta de datos configurada y la abre automaticamente.

Uso:
    python app.py
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from flask import Flask, render_template_string, request

import config
from runner import (
    add_by_ids,
    find_last_state,
    parse_ids,
    run_job,
    validar_usuario,
)
from scraper import ConexionXError

app = Flask(__name__)


def build_cookies(auth_token: str, ct0: str) -> str:
    """Arma 'auth_token=..; ct0=..' a partir de los dos campos separados.

    Tolera que el usuario pegue el valor solo o con el prefijo 'auth_token='/
    'ct0=' por delante. Si falta alguno, devuelve '' (se reusa la sesion guardada).
    """
    at = re.sub(r"^\s*auth_token\s*=\s*", "", (auth_token or "").strip()).rstrip(";").strip()
    ct = re.sub(r"^\s*ct0\s*=\s*", "", (ct0 or "").strip()).rstrip(";").strip()
    if at and ct:
        return f"auth_token={at}; ct0={ct}"
    return ""


# --- Persistencia de preferencias en .env --------------------------------

ENV_PATH = config.BASE_DIR / ".env"


def _env_line(clave: str, valor: str) -> str:
    """Arma la linea del .env citando el valor cuando hace falta.

    Las rutas de caso suelen traer espacios y, en Windows,
    backslashes. Se usan comillas SIMPLES porque dotenv las respeta literales;
    las dobles interpretan escapes y romperian '\\analisis' -> '\\x07nalisis'.
    """
    if not valor:
        return f"{clave}="
    if "'" in valor:                 # sin comillas simples que cerrarian antes
        return f"{clave}={valor}"
    return f"{clave}='{valor}'"


def _persist_env(clave: str, valor: str) -> None:
    """Guarda/actualiza una linea 'CLAVE=valor' en el .env.

    Lo usan las cookies (X_COOKIES) y la carpeta de salida
    (SCRAPER_OUTPUT_DIR), para que la proxima corrida las recuerde.
    """
    nueva = _env_line(clave, valor)
    lines: list[str] = []
    found = False
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{clave}="):
                lines.append(nueva)
                found = True
            else:
                lines.append(line)
    if not found:
        lines.insert(0, nueva)
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _persist_cookies(cookies: str) -> None:
    """Guarda/actualiza la linea X_COOKIES= en el .env (recordar cookies)."""
    _persist_env("X_COOKIES", cookies)


# --- Carpetas recientes ---------------------------------------------------
# Van en un archivo aparte y no en el .env para no ensuciar un archivo que el
# usuario edita a mano. Contienen nombres de causas: el .gitignore las excluye.

RECIENTES_PATH = config.BASE_DIR / "recientes.json"
MAX_RECIENTES = 5


def leer_recientes() -> list[str]:
    """Ultimas carpetas usadas, mas reciente primero.

    NO comprueba que existan, a proposito. Se llama en cada render, y un
    os.path.isdir() sobre un montaje de red caido (la unidad del juzgado, un
    disco externo desconectado) bloquea decenas de segundos: colgaria la pagina
    entera y tambien las corridas. Ademas, descartar una carpeta por estar
    momentaneamente inaccesible la borraria de la lista para siempre, cuando lo
    normal es que el disco vuelva.

    Si la carpeta no esta, /estado lo dice al elegirla ("La carpeta no existe").
    """
    try:
        datos = json.loads(RECIENTES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(datos, list):
        return []
    salida = []
    for d in datos:
        if isinstance(d, str) and d:
            try:
                salida.append(str(config.adapt_path(d)))
            except Exception:  # noqa: BLE001 - recordar carpetas nunca es fatal
                salida.append(d)
    return salida


def recientes_ui() -> list[dict]:
    """Recientes con una etiqueta corta para el desplegable.

    Las rutas de causa son largas ('.../Causas/<expediente>/analisis/datos')
    y en un select se cortan justo en la parte que distingue una de otra. Se
    muestran los ultimos dos tramos, con la ruta completa en el tooltip.
    """
    salida = []
    for ruta in leer_recientes():
        partes = Path(ruta).parts
        etiqueta = " › ".join(partes[-2:]) if len(partes) > 2 else ruta
        salida.append({"ruta": ruta, "etiqueta": etiqueta})
    return salida


def agregar_reciente(carpeta: str) -> None:
    """Pone `carpeta` al tope de la lista, sin duplicados y con tope MAX.

    Normaliza antes de comparar: en macOS '/tmp/x' y '/private/tmp/x' son la
    misma carpeta, y '~/Casos/x' tipeado a mano tambien; sin resolver, la misma
    carpeta entraria varias veces a la lista.
    """
    try:
        carpeta = str(config.normalize_output_dir(carpeta))
    except ValueError:
        return  # ruta de Windows en POSIX: no tiene sentido recordarla
    lista = [c for c in leer_recientes() if c != carpeta]
    lista.insert(0, carpeta)
    try:
        RECIENTES_PATH.write_text(
            json.dumps(lista[:MAX_RECIENTES], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass  # recordar carpetas es una comodidad, nunca un error fatal


def _reset_account_db() -> None:
    """Borra la base de twscrape para forzar el alta con cookies nuevas."""
    for suffix in ("", "-shm", "-wal"):
        p = config.ACCOUNTS_DB.with_name(config.ACCOUNTS_DB.name + suffix)
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def _open_folder(path) -> bool:
    """Abre la carpeta en el gestor de archivos del sistema. True si se pudo.

    Cada SO tiene su via: Finder (macOS), Explorador (Windows), xdg-open (el
    resto). Devuelve bool en vez de tragarse el fallo en silencio, para que la
    UI no afirme que abrio la carpeta cuando no paso nada.

    El argumento va como lista (nunca shell=True): la ruta la escribe el usuario
    en el formulario y no debe interpretarse como comando.
    """
    destino = str(path)
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", destino], check=True)
        elif os.name == "nt":
            os.startfile(destino)  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", destino], check=True)
        return True
    except Exception:  # noqa: BLE001 - abrir la carpeta es un extra, nunca un error fatal
        return False


# --- Selector de carpeta nativo ------------------------------------------
# El navegador NO puede darnos una ruta absoluta: showDirectoryPicker() devuelve
# un handle opaco (por diseno de seguridad) y webkitdirectory solo rutas
# relativas. Como los archivos los escribe Python, el dialogo tiene que salir
# del backend, que corre en la misma maquina que el navegador.
#
# tkinter no es opcion: no viene con el Python de Homebrew, y ademas con
# threaded=True el request corre fuera del hilo principal, lo que en macOS
# aborta el proceso entero con una excepcion de Objective-C que Python no puede
# capturar. Mataria el servidor, no el request.

# El dialogo bloquea el worker que lo abrio. Sin este lock, un doble clic en el
# boton apila dos ventanas nativas sobre la pantalla del usuario.
_PICKER_LOCK = threading.Lock()
_PICKER_TIMEOUT = 300  # s; un dialogo olvidado colgaria el hilo para siempre

# En macOS el dialogo se abre desde un .app auxiliar y no con "osascript -e".
# Medido: con osascript el foco se queda en el navegador y el selector aparece
# DETRAS de la ventana, asi que parece que el boton no hizo nada. El proceso
# osascript no esta registrado como aplicacion, y por eso su "activate" no
# tiene efecto. Un .app compilado si se registra y toma el foco.
#
# La alternativa era pedirselo a System Events, pero eso es un Apple Event
# cross-app: macOS pide permiso de Automatizacion y, si el usuario lo niega,
# el selector queda roto para siempre con un error -1743.
_PICK_MAC_APP = """
on run
	activate
	set carpetaInicial to ""
	try
		set carpetaInicial to (do shell script "cat " & quoted form of "__ENTRADA__")
	end try
	try
		if carpetaInicial is not "" then
			set elegida to choose folder with prompt "Elegi la carpeta del caso" ¬
				default location (POSIX file carpetaInicial)
		else
			set elegida to choose folder with prompt "Elegi la carpeta del caso"
		end if
	on error
		return  -- cancelo: no se escribe nada y Python lo lee como cancelado
	end try
	do shell script "printf %s " & quoted form of (POSIX path of elegida) & ¬
		" > " & quoted form of "__SALIDA__"
end run
"""


def _dir_selector() -> Path:
    """Carpeta de trabajo del selector auxiliar (se crea al vuelo)."""
    d = Path(tempfile.gettempdir()) / "scrapeador_tw_selector"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pick_mac(inicial: str | None) -> subprocess.CompletedProcess:
    """Abre el selector de macOS y devuelve algo con la forma de CompletedProcess.

    Se compila un .app chico en un temporal y se lanza con `open -W`, que
    espera a que el usuario cierre el dialogo.
    """
    trabajo = _dir_selector()
    entrada, salida = trabajo / "entrada.txt", trabajo / "salida.txt"
    app_aux = trabajo / "Elegir carpeta.app"

    entrada.write_text(inicial or "", encoding="utf-8")
    salida.unlink(missing_ok=True)

    if not app_aux.exists():
        fuente = trabajo / "selector.applescript"
        fuente.write_text(
            _PICK_MAC_APP.replace("__ENTRADA__", str(entrada))
                         .replace("__SALIDA__", str(salida)),
            encoding="utf-8",
        )
        r = subprocess.run(["osacompile", "-o", str(app_aux), str(fuente)],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return subprocess.CompletedProcess([], 1, "", r.stderr)

    # -W espera a que la app termine; -n fuerza una instancia nueva, si no una
    # copia ya abierta haria que open vuelva enseguida sin mostrar nada.
    subprocess.run(["open", "-W", "-n", str(app_aux)],
                   capture_output=True, text=True, timeout=_PICKER_TIMEOUT)

    ruta = salida.read_text(encoding="utf-8").strip() if salida.exists() else ""
    salida.unlink(missing_ok=True)
    # returncode 1 = el usuario cancelo, igual que el resto de las plataformas.
    return subprocess.CompletedProcess([], 0 if ruta else 1, ruta, "")


# ShowDialog($owner) con un Form TopMost es lo que evita que el dialogo aparezca
# DETRAS del navegador. El OutputEncoding es para que las rutas con acentos
# ("Analisis") no salgan mojibake por la codepage OEM de la consola.
# Sin .format() ni f-string: la ruta inicial se inserta con .replace() para no
# tener que duplicar cada llave de PowerShell, que tiene muchas.
_PICK_WIN = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "Add-Type -AssemblyName System.Windows.Forms;"
    "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
    "$d.Description = 'Elegi la carpeta del caso';"
    "$d.ShowNewFolderButton = $true;"
    "__SEL__"
    "$o = New-Object System.Windows.Forms.Form -Property @{TopMost=$true};"
    "if ($d.ShowDialog($o) -eq [System.Windows.Forms.DialogResult]::OK)"
    " { [Console]::Out.Write($d.SelectedPath) } else { exit 1 }"
)


def _pick_windows(inicial: str | None) -> subprocess.CompletedProcess:
    # En un literal de PowerShell con comillas simples, la comilla se escapa
    # duplicandola.
    sel = ""
    if inicial:
        sel = "$d.SelectedPath = '" + inicial.replace("'", "''") + "';"
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-Command",
         _PICK_WIN.replace("__SEL__", sel)],
        capture_output=True, timeout=_PICKER_TIMEOUT,
        creationflags=0x08000000,  # CREATE_NO_WINDOW: sin flash de consola negra
    )


def _pick_linux(inicial: str | None) -> subprocess.CompletedProcess | None:
    """zenity (GNOME) o kdialog (KDE); None si no hay ninguno instalado."""
    if shutil.which("zenity"):
        cmd = ["zenity", "--file-selection", "--directory",
               "--title=Elegi la carpeta del caso"]
        if inicial:
            cmd.append(f"--filename={inicial}/")
    elif shutil.which("kdialog"):
        cmd = ["kdialog", "--title", "Elegi la carpeta del caso",
               "--getexistingdirectory", inicial or "."]
    else:
        return None
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=_PICKER_TIMEOUT)


def _pick_folder(inicial: str | None = None) -> str | None:
    """Abre el selector de carpetas del sistema. Devuelve la ruta, o None.

    None cubre tres casos que para el frontend son equivalentes (no hay carpeta
    nueva que usar): el usuario cancelo, no hay dialogo disponible en este
    sistema, o el dialogo fallo. En todos, el campo de texto sigue estando para
    escribir la ruta a mano.
    """
    if inicial and not os.path.isdir(inicial):
        inicial = None  # no abrir en una carpeta que no existe
    try:
        if sys.platform == "darwin":
            r = _pick_mac(inicial)
        elif os.name == "nt":
            r = _pick_windows(inicial)
        else:
            r = _pick_linux(inicial)
        if r is None or r.returncode != 0:
            return None  # cancelado (exit 1 / -128 en macOS) o sin dialogo
        salida = r.stdout
        if isinstance(salida, bytes):  # PowerShell: capture_output sin text
            salida = salida.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - elegir carpeta es un extra, nunca fatal
        return None

    # Salida vacia con returncode 0 (Windows puede dar DialogResult.OK con
    # SelectedPath vacio). Hay que cortar ANTES del normpath: normpath("")
    # devuelve ".", que pasa el isdir() de abajo y mandaria las descargas al
    # directorio de trabajo del servidor en vez de a la carpeta del caso.
    salida = salida.strip()
    if not salida:
        return None

    # POSIX path de macOS trae barra final; el dialogo COM de Windows puede
    # devolver un CLSID si se elige un item virtual ("Este equipo").
    ruta = os.path.normpath(salida)
    return ruta if os.path.isdir(ruta) else None


# Claves que `parser.py` pone en cada registro. Con tres de las cuatro alcanza
# para distinguir una descarga de un JSON cualquiera del expediente.
_CLAVES_TWEET = ('"created_at"', '"screen_name"', '"full_text"', '"type"')
_MAX_HUERFANOS_MIRADOS = 40


def _parece_descarga(path: Path) -> bool:
    """True si el JSON parece una descarga hecha con esta herramienta.

    Mira SOLO el principio del archivo, nunca lo parsea entero: /estado se
    consulta en cada tecleo del formulario y un consolidado de varios MB tarda
    ~0,3 s en parsear, lo que haria inusable el campo.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            cabeza = fh.read(8192)
    except OSError:
        return False
    if not cabeza.lstrip().startswith("["):
        return False
    return sum(c in cabeza for c in _CLAVES_TWEET) >= 3


def _descargas_sin_convencion(carpeta: Path) -> list[str]:
    """JSON que parecen descargas pero no se llaman `<cuenta>_all.json`.

    Sin esto, una carpeta con un `data.json` de 5.700 tweets se reporta como
    "no tiene descargas todavia": es cierto que la herramienta no puede
    adoptarlo (el nombre no dice de que cuenta es), pero callarlo hace parecer
    que la carpeta esta vacia.
    """
    sueltos = []
    for f in sorted(carpeta.glob("*.json"))[:_MAX_HUERFANOS_MIRADOS]:
        if f.name.endswith("_all.json"):
            continue
        if _parece_descarga(f):
            sueltos.append(f.name)
    return sueltos


def _mismo_origen(req) -> bool:
    """True si el request salio de la propia app y no de otra pagina web.

    Sin esto, cualquier sitio que el usuario visite podria abrir un dialogo
    nativo sobre su pantalla con un fetch a 127.0.0.1:5000.
    """
    sitio = req.headers.get("Sec-Fetch-Site")
    if sitio:
        return sitio in ("same-origin", "same-site", "none")
    origen = req.headers.get("Origin")
    return not origen or origen == req.host_url.rstrip("/")


def _solo_mismo_origen(fn):
    """Decorador: rechaza requests que no vengan de la propia pagina.

    Hace falta porque el servidor escucha en 127.0.0.1 y no tiene contrasena,
    y porque un POST de formulario NO dispara preflight de CORS: el navegador
    lo manda a otra pagina sin preguntar. Sin este chequeo, cualquier sitio
    que el usuario tenga abierto puede disparar una corrida, escribir en el
    disco o borrar archivos. Un POST de formulario a 127.0.0.1 llega con
    `Sec-Fetch-Site: cross-site`, asi que el corte cae en el primer header.

    Va en los endpoints que devuelven HTML. Los que devuelven JSON
    (/abrir, /elegir-carpeta, /apagar) conservan el chequeo en linea, porque
    su JavaScript hace `r.json()` y un 403 en HTML lo dejaria sin poder
    mostrar el motivo. Son dos estilos a proposito, no un descuido.

    Si un dia esto te bloquea en uso normal, no lo saques: recargar con F5 y
    volver a intentar. El mensaje dice que ante un falso positivo hay que
    recargar, no que la peticion se va a dejar pasar.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not _mismo_origen(request):
            print(
                f"[seguridad] Rechazado {request.method} {request.path} "
                f"(Sec-Fetch-Site: {request.headers.get('Sec-Fetch-Site')!r}, "
                f"Origin: {request.headers.get('Origin')!r})",
                file=sys.stderr,
            )
            return render_template_string(
                PAGE, recientes=recientes_ui(), form=default_form(), result=None,
                error="Se rechazo la peticion: no vino de esta pagina.\n\n"
                      "Si la estabas usando con normalidad, recargala con F5 y "
                      "volve a intentar. Si no era de esta herramienta, cerra "
                      "la pestana.",
            ), 403
        return fn(*args, **kwargs)
    return wrapper


# --- HTML (minimalista, embebido) ----------------------------------------

PAGE = """
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scraper de X</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: flex; flex-direction: column;
    align-items: center; padding: 12px 16px;
    background: #0e1116; color: #e6e6e6;
    font: 15px/1.5 system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
  }
  /* Se usa siempre en PC: el formulario se reparte en dos columnas para que
     entre en una pantalla, en vez de la columna angosta que obligaba a
     scrollear dos veces para llegar al boton Ejecutar. */
  .card {
    width: 100%; max-width: 1040px; background: #171b22;
    border: 1px solid #262c36; border-radius: 14px; padding: 16px 20px 18px;
  }
  .grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 0 20px;
    align-items: start;
  }
  .col { min-width: 0; }            /* sin esto, un texto largo estira la columna */
  .full { grid-column: 1 / -1; }    /* mensajes y boton cruzan las dos columnas */
  /* Ventana a media pantalla: vuelve a una sola columna en vez de romperse. */
  @media (max-width: 900px) {
    .grid { grid-template-columns: 1fr; }
  }
  .head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 4px; }
  /* Mini carteles de ayuda: pasos de uso y aclaraciones cortas. */
  .pasos {
    display: flex; gap: 7px; flex-wrap: wrap; margin: 10px 0 2px;
    font-size: 12px; color: #8b95a5;
  }
  .pasos span {
    display: inline-flex; align-items: center; gap: 6px;
    background: #0e1116; border: 1px solid #2b323d; border-radius: 999px;
    padding: 4px 10px;
  }
  .pasos b {
    display: inline-flex; width: 16px; height: 16px; border-radius: 50%;
    background: #4f8cff; color: #fff; align-items: center; justify-content: center;
    font-size: 10px;
  }
  .sum-hint { text-transform: none; letter-spacing: 0; color: #6c7585; }
  .hint code { color: #b7c0cd; }
  /* Discreto y en la esquina opuesta a Ejecutar: son acciones muy distintas y
     no se tienen que poder confundir. */
  #salir {
    width: auto; margin: 0 0 0 auto; padding: 4px 12px; font-size: 12px;
    font-weight: 400; background: transparent; color: #6c7585;
    border: 1px solid #2b323d;
  }
  #salir:hover { border-color: #5a2626; color: #f0a3a3; }
  .buscador {
    position: fixed; left: 50%; transform: translateX(-50%); bottom: 24px;
    max-width: 620px; padding: 12px 16px; border-radius: 10px; z-index: 50;
    background: #1a1710; border: 1px solid #4a4326; color: #d8c98a;
    font-size: 13px; line-height: 1.5; text-align: center;
    box-shadow: 0 6px 24px rgba(0,0,0,.45);
  }
  .buscador b { color: #e6e6e6; }
  .apagado {
    position: fixed; inset: 0; display: flex; align-items: center;
    justify-content: center; background: #0e1116; color: #8b95a5;
    font-size: 15px; text-align: center; z-index: 99; padding: 40px;
  }
  h1 { font-size: 19px; margin: 0; }
  .sub { color: #8b95a5; font-size: 13px; margin: 0; }
  fieldset {
    border: 1px solid #262c36; border-radius: 11px; padding: 10px 14px 14px;
    margin: 14px 0 0;
  }
  legend { font-size: 12px; color: #8b95a5; padding: 0 6px; text-transform: uppercase;
    letter-spacing: .04em; }
  label { display: block; font-size: 13px; color: #b7c0cd; margin: 9px 0 5px; }
  label.first { margin-top: 0; }
  input[type=text], input[type=date], input[type=number] {
    width: 100%; padding: 10px 12px; background: #0e1116;
    border: 1px solid #2b323d; border-radius: 9px; color: #e6e6e6;
    font: inherit; outline: none;
  }
  input:focus { border-color: #4f8cff; }
  .hint { font-size: 11px; color: #6c7585; margin-top: 5px; }
  .row { display: flex; gap: 12px; }
  .row > div { flex: 1; }
  .checks { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
  .chk {
    display: flex; align-items: center; gap: 7px; padding: 8px 12px;
    background: #0e1116; border: 1px solid #2b323d; border-radius: 9px;
    cursor: pointer; font-size: 13px; user-select: none;
  }
  .chk input { accent-color: #4f8cff; }
  button {
    width: 100%; margin-top: 12px; padding: 11px; border: 0; border-radius: 9px;
    background: #4f8cff; color: #fff; font: inherit; font-weight: 600;
    cursor: pointer;
  }
  button:disabled { opacity: .6; cursor: progress; }
  .msg { margin-top: 18px; padding: 14px; border-radius: 9px; font-size: 13px; }
  .ok { background: #102a1c; border: 1px solid #1f5138; color: #79e3a8; }
  .msg.warn { background: #1c1710; border: 1px solid #4a3a1a; color: #d8a657;
    white-space: pre-line; }
  .err { background: #2a1414; border: 1px solid #5a2626; color: #f0a3a3;
    white-space: pre-line; }
  .estado {
    margin-top: 8px; padding: 9px 11px; border-radius: 9px; font-size: 12.5px;
    line-height: 1.5; background: #0e1116; border: 1px solid #2b323d; color: #8b95a5;
  }
  .estado.ok  { border-color: #1f5138; background: #101c17; color: #79e3a8; }
  .estado.no  { border-color: #4a3a1a; background: #1c1710; color: #d8a657; }
  .estado.err { border-color: #5a2626; background: #1c1010; color: #f0a3a3; }
  .aviso {
    margin-top: 8px; padding: 8px 10px; border-radius: 8px;
    border: 1px solid #4a4326; background: #1a1710; color: #d8c98a;
    font-size: 12.5px; line-height: 1.45;
  }
  .aviso code { color: #e6e6e6; font-size: 12px; }
  .estado b { color: #e6e6e6; }
  .estado .ruta { color: #6c7585; word-break: break-all; }
  select {
    width: 100%; padding: 9px 10px; background: #0e1116;
    border: 1px solid #2b323d; border-radius: 9px; color: #e6e6e6;
    font: 13px system-ui; margin-bottom: 4px;
  }
  .dirrow { display: flex; gap: 8px; }
  .dirrow input { flex: 1; }
  .dirrow button {
    width: auto; margin: 0; padding: 0 14px; background: #0e1116; color: #b7c0cd;
    border: 1px solid #2b323d; font-weight: 400; font-size: 13px;
  }
  .dirrow button:hover { border-color: #4f8cff; color: #e6e6e6; }
  .chips { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; align-items: center; }
  .chips .lbl { font-size: 11.5px; color: #6c7585; }
  .chips button {
    width: auto; margin: 0; padding: 4px 10px; font-size: 12px; font-weight: 500;
    background: #16202c; color: #9fc6ff; border: 1px solid #2b3a4d; border-radius: 999px;
  }
  .chips button:hover { border-color: #4f8cff; color: #fff; }
  .chk.modo { display: flex; margin-top: 10px; }
  .chk.modo:first-of-type { margin-top: 0; }
  .modo-note { font-size: 11.5px; color: #6c7585; margin: 5px 0 0 12px; line-height: 1.45; }
  .chk.off { opacity: .45; }
  #fechasbox.off { opacity: .45; }
  #winbox.off { display: none; }
  /* Bloques plegables (Sesion, Agregar por id): son HTML nativo, sin JS. */
  details.plegable { margin-top: 14px; border: 1px solid #262c36; border-radius: 11px;
    background: #141821; }
  details.plegable > summary {
    cursor: pointer; padding: 9px 14px; font-size: 12px; color: #8b95a5;
    text-transform: uppercase; letter-spacing: .04em; user-select: none;
    list-style: none;
  }
  details.plegable > summary::-webkit-details-marker { display: none; }
  /* Los triangulos van como caracteres directos y NO como escapes CSS del tipo
     barra-2-5-B-8: PAGE es un string normal de Python, que interpreta esas
     secuencias como escapes octales antes de que el CSS las vea. */
  details.plegable > summary::before { content: "▸  "; color: #6c7585; }
  details.plegable[open] > summary::before { content: "▾  "; }
  details.plegable > summary:hover { color: #e6e6e6; }
  details.plegable .cuerpo { padding: 0 14px 14px; }
  /* Cookies vencidas: el bloque se abre solo y se marca, que es cuando se usa. */
  details.plegable.urgente { border-color: #5a2626; background: #1c1010; }
  details.plegable.urgente > summary { color: #f0a3a3; }
  details.plegable fieldset { border: 0; padding: 0; margin: 0; }
  .files { margin: 8px 0 0; padding-left: 18px; color: #cfd6e0; word-break: break-all; }
  .files li { margin: 2px 0; }
  code { color: #b7c0cd; }
</style>
</head>
<body>
  <form class="card" method="post" action="/run" onsubmit="lock()">
    <div class="head">
      <h1>Scraper de X</h1>
      <p class="sub">Descarga tweets de un perfil a JSON / CSV / Excel.</p>
      <button type="button" id="salir" title="Detener el servidor y cerrar la herramienta">Salir</button>
    </div>
    <div class="pasos">
      <span><b>1</b> Elegí la carpeta de guardado</span>
      <span><b>2</b> Escribí el usuario (sin @)</span>
      <span><b>3</b> Revisá el filtro y Ejecutá</span>
    </div>

    <div class="grid">

    {% if error %}<div class="msg err full">{{ error }}</div>{% endif %}
    {% if result %}
      {% if result.aviso %}<div class="msg warn full">{{ result.aviso }}</div>{% endif %}
      <div class="msg ok full">
        <strong>{{ result.summary }}</strong><br>
        {% if result.abierta %}Guardado en la carpeta de datos (se abrio en el gestor de archivos).
        {% else %}Guardado en <code>{{ result.carpeta }}</code> (no se pudo abrir la carpeta sola).
        {% endif %}
        <ul class="files">
          {% for f in result.written %}<li>{{ f }}</li>{% endfor %}
        </ul>
      </div>
    {% endif %}

    <div class="col">
    <fieldset>
      <legend>Carpeta del caso</legend>
      {% if recientes %}
      <label class="first">Usadas recientemente</label>
      <select id="recientes">
        <option value="">&mdash; elegir de la lista &mdash;</option>
        {% for r in recientes %}<option value="{{ r.ruta }}" title="{{ r.ruta }}">{{ r.etiqueta }}</option>{% endfor %}
      </select>
      {% endif %}
      <label{{ ' class="first"'|safe if not recientes }}>Ruta</label>
      <div class="dirrow">
        <input type="text" id="output_dir" name="output_dir" value="{{ form.output_dir }}"
               placeholder="ruta de la carpeta del caso" autocomplete="off">
        <button type="button" id="elegirDir" title="Elegir la carpeta con el selector del sistema">Elegir...</button>
        <button type="button" id="abrirDir" title="Abrir la carpeta en el gestor de archivos">Abrir</button>
      </div>
      <div class="hint">Acá se guardan los archivos. Usá <b>Elegir...</b> o pegá una ruta
        como <code>C:\\Casos\\expediente</code> (en WSL, <code>/mnt/c/Casos/expediente</code>).
        La última carpeta queda recordada.</div>
      <div class="row">
        <div>
          <label>Usuario (sin @)</label>
          <input type="text" id="target" name="target" value="{{ form.target }}" placeholder="nasa" autofocus>
        </div>
        <div>
          <label>Maximo (opcional, vacío = todo)</label>
          <input type="number" name="max_tweets" min="1" value="{{ form.max_tweets }}" placeholder="sin limite"
                 title="Corta la descarga al llegar a esa cantidad de tweets. Vacío = sin límite.">
        </div>
      </div>
      <div id="estado" class="estado">Consultando la carpeta...</div>
      <div id="chips" class="chips"></div>
    </fieldset>
    </div>

    <div class="col">
    <fieldset>
      <legend>Que traer</legend>
      <div class="hint" style="margin-top:0; margin-bottom:8px">
        <b>Filtro:</b> elegí qué parte del perfil bajar. Si es la primera vez,
        dejá <b>Descarga completa</b>.
      </div>
      <label class="chk modo"><input type="radio" name="modo" value="completa" onchange="modoManual()"
        {{ 'checked' if form.modo == 'completa' }}>Descarga completa</label>
      <label class="chk modo" id="upd-lbl"><input type="radio" id="upd" name="modo" value="update" onchange="modoManual()"
        {{ 'checked' if form.modo == 'update' }}>Solo lo nuevo desde la ultima descarga</label>
      <label class="chk modo"><input type="radio" id="hist" name="modo" value="historial" onchange="modoManual()"
        {{ 'checked' if form.modo == 'historial' }}>Historial por fechas (avanzado)</label>
      <div class="modo-note" id="nota-completa">Recorre el timeline del perfil. Podes acotar con las fechas de abajo.</div>
      <div class="modo-note" id="nota-update">Mira lo ya bajado de esta cuenta <b>en la carpeta de arriba</b>
        y trae solo lo posterior al ultimo tweet. Ignora las fechas. Incluye retweets.</div>
      <div class="modo-note" id="nota-historial">Para ir mas atras del tope ~3200 de X. Requiere "Desde".
        Usa la busqueda: <b>no incluye retweets</b> y tarda mas.</div>

      <div class="row" id="fechasbox">
        <div>
          <label>Desde</label>
          <input type="date" id="since" name="since" value="{{ form.since }}">
        </div>
        <div>
          <label>Hasta</label>
          <input type="date" id="until" name="until" value="{{ form.until }}">
        </div>
      </div>
      <div class="hint" id="fechas-hint">Opcional: limita los tweets a un rango
        de fechas. Vacío = todo lo disponible.</div>
      <div id="winbox">
        <label>Dias por ventana</label>
        <input type="number" id="win" name="window_days" min="1" value="{{ form.window_days }}">
        <div class="hint">Solo aplica al historial. Mas chico = mas preciso pero mas lento.</div>
      </div>
    </fieldset>

    <fieldset>
      <legend>Que incluir</legend>
      <div class="hint" style="margin-top:0; margin-bottom:8px">
        <b>Tipos:</b> qué publicaciones traer. <b>Formatos:</b> en qué archivos
        guardar (JSON es la fuente de verdad).
      </div>
      <label class="first">Tipos</label>
      <div class="checks">
        <label class="chk"><input type="checkbox" name="types" value="tweets" {{ 'checked' if 'tweets' in form.types }}>Tweets</label>
        <label class="chk" id="rt-lbl"><input type="checkbox" id="rt" name="types" value="retweets" {{ 'checked' if 'retweets' in form.types }}>Retweets</label>
        <label class="chk"><input type="checkbox" name="types" value="replies" {{ 'checked' if 'replies' in form.types }}>Replies</label>
      </div>
      <label>Formatos</label>
      <div class="checks">
        <label class="chk"><input type="checkbox" name="formats" value="json" {{ 'checked' if 'json' in form.formats }}>JSON</label>
        <label class="chk"><input type="checkbox" name="formats" value="csv" {{ 'checked' if 'csv' in form.formats }}>CSV</label>
        <label class="chk"><input type="checkbox" name="formats" value="xlsx" {{ 'checked' if 'xlsx' in form.formats }}>Excel</label>
      </div>
      <div class="hint" id="rt-note" style="display:none; color:#d8a657">
        El modo historial usa la busqueda de X, que no devuelve retweets: por eso
        queda deshabilitado.
      </div>
    </fieldset>
    </div>

    <div class="full">
      <button id="go" type="submit">Ejecutar</button>

      <!-- Solo se toca cuando vencen las cookies: plegado por defecto. Se abre
           solo (y se marca) cuando la corrida fallo, que es justo cuando hace
           falta pegar unas nuevas. -->
      <details class="plegable{{ ' urgente' if error }}" {{ 'open' if error }}>
        <summary>Sesion de X &mdash; cookies
          <span class="sum-hint">(¿vencidas? acá te decimos cómo sacarlas)</span>
        </summary>
        <div class="cuerpo">
          <fieldset>
            <div class="hint" style="margin-top:0; margin-bottom:10px">
              <b>Cómo extraerlas:</b> 1) Abrí <b>x.com</b> con tu sesión iniciada.
              2) Apretá <b>F12</b> → <b>Application</b> (o <b>Storage</b>) →
              <b>Cookies</b> → <code>https://x.com</code>.
              3) Copiá los valores de <code>auth_token</code> y <code>ct0</code>.
              4) Pegalos acá: se guardan solos para la próxima.
            </div>
            <label class="first">auth_token</label>
            <input type="text" name="auth_token" placeholder="pega aca el auth_token" autocomplete="off">
            <label>ct0</label>
            <input type="text" name="ct0" placeholder="pega aca el ct0" autocomplete="off">
            <div class="hint">Dejalos vacios para usar la sesion ya guardada.</div>
          </fieldset>
        </div>
      </details>
    </div>

    </div><!-- /grid -->
  </form>

  <form class="card" method="post" action="/add_ids" onsubmit="lock2()" style="margin-top:12px; padding:0">
    <details class="plegable" style="margin:0; border:0; background:transparent">
      <summary>Agregar tweets sueltos por id</summary>
      <div class="cuerpo">
        <p class="sub" style="margin-bottom:10px">Recupera tweets que X no lista (algunas replies), pegando su id o URL.</p>
        <label class="first">Ids o URLs de tweets</label>
        <textarea name="tweet_ids" style="width:100%; min-height:60px; padding:10px 12px;
          background:#0e1116; border:1px solid #2b323d; border-radius:9px; color:#e6e6e6;
          font:12px/1.4 system-ui" placeholder="2067038824748696024&#10;https://x.com/user/status/123..."></textarea>
        <div class="hint">Uno por linea o separados por coma. Cada tweet se suma al
          consolidado de su autor. Usa la sesion ya guardada.</div>
        <button id="go2" type="submit">Agregar</button>
      </div>
    </details>
  </form>

  <script>
    // Los tres modos son excluyentes por ser radios: aca solo ajustamos que
    // campos tienen sentido en cada uno.
    function modoActual() {
      var r = document.querySelector('input[name="modo"]:checked');
      return r ? r.value : 'completa';
    }
    // Si el usuario ya eligio un modo a mano, la deteccion de descargas previas
    // no se lo pisa. Lo prende sync() cuando lo dispara un onchange de radio.
    var modoTocado = false;
    function modoManual() { modoTocado = true; sync(); }
    function sync() {
      var modo = modoActual();
      var isHist = modo === 'historial', isUpd = modo === 'update';

      // Solo la explicacion del modo elegido: las tres juntas ocupaban casi
      // 100 px describiendo modos que no se eligieron.
      ['completa', 'update', 'historial'].forEach(function (m) {
        document.getElementById('nota-' + m).style.display = (m === modo) ? 'block' : 'none';
      });

      // Ventana de dias: solo en historial.
      document.getElementById('winbox').classList.toggle('off', !isHist);
      // Fechas: no aplican al actualizar (el corte lo marca lo ya bajado). Se
      // atenua el bloque entero: antes quedaban deshabilitadas sin ninguna
      // senal visual mas que el gris nativo del navegador.
      document.getElementById('since').disabled = isUpd;
      document.getElementById('until').disabled = isUpd;
      document.getElementById('fechasbox').classList.toggle('off', isUpd);
      document.getElementById('fechas-hint').style.display = isUpd ? 'none' : 'block';
      // Retweets: la busqueda del historial no los devuelve.
      var rt = document.getElementById('rt');
      rt.disabled = isHist;
      if (isHist) { rt.checked = false; }
      document.getElementById('rt-lbl').classList.toggle('off', isHist);
      document.getElementById('rt-note').style.display = isHist ? 'block' : 'none';
    }
    function lock() {
      var b = document.getElementById('go');
      b.disabled = true; b.textContent = 'Procesando... (puede tardar)';
    }
    function lock2() {
      var b = document.getElementById('go2');
      b.disabled = true; b.textContent = 'Agregando...';
    }

    // --- Estado de la carpeta de datos -----------------------------------
    // Consulta que hay ya descargado, para saber ANTES de ejecutar si el modo
    // "solo lo nuevo" va a encontrar de donde continuar.
    var estTimer = null;
    function esc(s){ return String(s == null ? '' : s)
      .replace(/[&<>"]/g, function(c){
        return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }

    // Deshabilita "solo lo nuevo" cuando no hay de donde continuar, y si estaba
    // elegido cae a "descarga completa" para no dejar el form en un estado invalido.
    function permitirUpdate(ok) {
      var upd = document.getElementById('upd');
      upd.disabled = !ok;
      document.getElementById('upd-lbl').classList.toggle('off', !ok);
      if (!ok && upd.checked) {
        document.querySelector('input[name="modo"][value="completa"]').checked = true;
      }
      sync();
    }

    function pintarChips(cuentas) {
      var box = document.getElementById('chips');
      box.innerHTML = '';
      if (!cuentas || !cuentas.length) return;
      var lbl = document.createElement('span');
      lbl.className = 'lbl';
      lbl.textContent = 'Ya descargadas aca:';
      box.appendChild(lbl);
      cuentas.forEach(function (c) {
        var b = document.createElement('button');
        b.type = 'button';
        b.textContent = '@' + c;
        b.onclick = function () {
          document.getElementById('target').value = c;
          consultarEstado();
        };
        box.appendChild(b);
      });
    }

    // Archivos que parecen descargas pero no siguen `<cuenta>_all.json`. La
    // herramienta no puede adoptarlos sola: el nombre no dice de que cuenta
    // son. Pero callarlos hace que una carpeta con datos parezca vacia.
    function avisoSinConvencion(d) {
      if (!d.sin_convencion || !d.sin_convencion.length) { return ''; }
      var lista = d.sin_convencion.map(function (n) {
        return '<code>' + esc(n) + '</code>';
      }).join(', ');
      var plural = d.sin_convencion.length > 1;
      return '<div class="aviso">' +
        (plural ? 'Hay ' + d.sin_convencion.length + ' archivos que parecen descargas'
                : 'Hay un archivo que parece una descarga') +
        ' pero no sigue' + (plural ? 'n' : '') + ' la convencion de nombre: ' + lista +
        '.<br>Para que se use' + (plural ? 'n' : '') + ' aca, copialo' + (plural ? 's' : '') +
        ' como <code>&lt;cuenta&gt;_all.json</code> ' +
        '(por ejemplo <code>nasa_all.json</code>).</div>';
    }

    function pintarEstado(d) {
      var box = document.getElementById('estado');
      var ruta = '<div class="ruta">' + esc(d.carpeta) + '</div>';
      pintarChips(d.cuentas);

      if (d.error) {                       // p.ej. ruta de Windows en esta Mac
        box.className = 'estado err';
        box.innerHTML = esc(d.error);
        permitirUpdate(false);
        return;
      }
      if (!d.existe) {
        box.className = 'estado err';
        box.innerHTML = 'La carpeta no existe (se va a crear al descargar).' + ruta;
        permitirUpdate(false);
        return;
      }
      if (d.encontrado) {
        box.className = 'estado ok';
        // Hay de donde continuar: pasamos solo a "solo lo nuevo", salvo que el
        // usuario ya haya elegido un modo a mano. Se avisa, para que el cambio
        // no pase desapercibido.
        var cambiado = false;
        permitirUpdate(true);
        if (!modoTocado && modoActual() !== 'update') {
          document.getElementById('upd').checked = true;
          sync();
          cambiado = true;
        }
        box.innerHTML = 'Ya descargado de <b>@' + esc(d.target) + '</b>: ' +
          '<b>' + d.registros.toLocaleString('es') + '</b> registros · ' +
          'ultimo tweet <b>' + esc(d.ultimo) + '</b>' +
          (d.archivo ? ' · ' + esc(d.archivo) : '') +
          (cambiado ? '<br><b>Se cambio a "solo lo nuevo".</b> ' +
                      'Podes elegir otro modo abajo.' : '') + ruta +
          avisoSinConvencion(d);
      } else {
        box.className = 'estado no';
        box.innerHTML = (d.target
            ? 'No hay descargas previas de <b>@' + esc(d.target) + '</b>. '
            : 'Indica un usuario para ver si ya hay algo descargado. ') +
          (d.cuentas.length ? '' : 'La carpeta no tiene descargas con el nombre que usa esta herramienta. ') +
          'Para actualizar hace falta una descarga previa.' + ruta +
          avisoSinConvencion(d);
        permitirUpdate(false);
      }
    }

    // Numero de consulta: el selector y la lista de recientes consultan al
    // instante, y pueden adelantar a un fetch del debounce que todavia esta en
    // vuelo. Sin esto, la respuesta vieja pinta sobre la nueva y el auto-cambio
    // de modo se decide con los datos de otra carpeta.
    var estSeq = 0;
    function consultarEstado() {
      var t = document.getElementById('target').value.trim();
      var d = document.getElementById('output_dir').value.trim();
      var box = document.getElementById('estado');
      var mia = ++estSeq;
      box.className = 'estado'; box.textContent = 'Consultando la carpeta...';
      fetch('/estado?target=' + encodeURIComponent(t) + '&dir=' + encodeURIComponent(d))
        .then(function(r){ return r.json(); })
        .then(function(d2){
          if (mia !== estSeq) { return; }   // llego tarde: ya hay otra consulta
          pintarEstado(d2);
        })
        .catch(function(){
          if (mia !== estSeq) { return; }
          box.className = 'estado err';
          box.textContent = 'No se pudo consultar la carpeta.';
        });
    }
    function estadoDebounce() {
      clearTimeout(estTimer);
      estTimer = setTimeout(consultarEstado, 400);
    }
    document.getElementById('target').addEventListener('input', estadoDebounce);
    document.getElementById('output_dir').addEventListener('input', estadoDebounce);

    // Selector de carpetas del sistema. El backend abre el dialogo nativo (el
    // navegador no puede darnos una ruta absoluta) y devuelve la ruta elegida.
    document.getElementById('elegirDir').onclick = function () {
      var btn = this;
      var d = document.getElementById('output_dir').value.trim();
      btn.disabled = true; btn.textContent = 'Elegiendo...';
      // El selector es una ventana del sistema y macOS la abre en la pantalla
      // PRINCIPAL, no necesariamente donde esta el navegador. Con varios
      // monitores puede caer fuera de la vista y parecer que el boton no hizo
      // nada, asi que se avisa donde buscarla mientras esta abierta.
      mostrarBuscador(true);
      fetch('/elegir-carpeta?dir=' + encodeURIComponent(d), {method: 'POST'})
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (j.ok && j.carpeta) {
            document.getElementById('output_dir').value = j.carpeta;
            consultarEstado();            // directo: el debounce es para tipeo
          } else if (!j.cancelado) {
            alert(j.error || 'No se pudo abrir el selector. Escribi la ruta a mano.');
          }
        })
        .catch(function () {
          alert('No se pudo abrir el selector. Escribi la ruta a mano.');
        })
        .then(function () {
          btn.disabled = false; btn.textContent = 'Elegir...';
          mostrarBuscador(false);
        });
    };

    document.getElementById('salir').onclick = function () {
      // El salto va como \\n (doble barra): PAGE es un string normal de Python y
      // un \\n simple se convierte en un salto REAL, que parte el literal de JS y
      // rompe la sintaxis de TODO el script de la pagina.
      if (!confirm('Detener el servidor?\\n\\nLa herramienta se cierra y hay que ' +
                   'volver a abrirla para usarla.')) { return; }
      fetch('/apagar', {method: 'POST'}).catch(function () {});
      // El servidor se apaga enseguida, asi que la respuesta puede no llegar:
      // se tapa la pagina igual, que es lo que el usuario pidio.
      setTimeout(function () {
        var d = document.createElement('div');
        d.className = 'apagado';
        d.textContent = 'La herramienta se detuvo. Ya podes cerrar esta pestaña.';
        document.body.appendChild(d);
      }, 300);
    };

    function mostrarBuscador(visible) {
      var id = 'buscando-selector';
      var v = document.getElementById(id);
      if (!visible) { if (v) { v.remove(); } return; }
      if (v) { return; }
      v = document.createElement('div');
      v.id = id;
      v.className = 'buscador';
      v.innerHTML = 'Se abrio el <b>selector de carpetas del sistema</b>.' +
        '<br>Si no lo ves, revisá si quedó <b>detrás de esta ventana</b> ' +
        '(o en otro monitor) y traelo al frente.';
      document.body.appendChild(v);
    }

    var recientesSel = document.getElementById('recientes');
    if (recientesSel) {
      recientesSel.onchange = function () {
        if (!this.value) return;
        document.getElementById('output_dir').value = this.value;
        consultarEstado();
      };
    }

    document.getElementById('abrirDir').onclick = function () {
      var d = document.getElementById('output_dir').value.trim();
      fetch('/abrir?dir=' + encodeURIComponent(d), {method: 'POST'})
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (!j.ok) {
            // El backend explica el motivo (p.ej. ruta de Windows en esta Mac);
            // si no lo trae, al menos mostramos que carpeta intento abrir.
            alert(j.error || ('No se pudo abrir la carpeta: ' + j.carpeta));
          }
        })
        .catch(function () { alert('No se pudo abrir la carpeta.'); });
    };

    sync();
    consultarEstado();
  </script>
</body>
</html>
"""

def default_form() -> dict:
    """Valores iniciales del formulario, leidos frescos en cada render.

    Es una funcion y no una constante porque `config.OUTPUT_DIR` cambia cuando
    el usuario elige otra carpeta: como constante de modulo quedaba congelada al
    importar, y despues de una corrida un refresh de la pagina volvia a mostrar
    la carpeta vieja.
    """
    return {
        "target": config.DEFAULT_TARGET,
        "output_dir": str(config.OUTPUT_DIR),
        "since": "",
        "until": "",
        "types": ["tweets", "retweets", "replies"],
        "formats": ["json", "csv"],
        "max_tweets": "",
        "modo": "completa",
        "historical": False,
        "window_days": "7",
        "update": False,
    }


@app.get("/")
def index():
    return render_template_string(PAGE, recientes=recientes_ui(), form=default_form(), result=None, error=None)


@app.get("/estado")
@_solo_mismo_origen
def estado():
    """Que hay ya descargado en una carpeta, para la cuenta indicada.

    Lo consulta el formulario al vuelo, para que se vea ANTES de ejecutar si el
    modo "solo lo nuevo" va a funcionar (y no enterarse recien al fallar).
    """
    # Aqui el objetivo es una consulta, no una accion: se valida pero un valor
    # invalido se trata como "no hay objetivo" en vez de mostrar un error. La
    # validacion estricta queda para /run y /add_ids, que si escriben en disco.
    # Asi el mensaje de error no aparece y desaparece mientras se tipea.
    try:
        target = validar_usuario(request.args.get("target") or "")
    except ValueError:
        target = ""
    raw_dir = (request.args.get("dir") or "").strip()
    # Se normaliza con el mismo helper que usa la corrida, para que la interfaz
    # y la descarga nunca hablen de carpetas distintas ('~/Casos/x' incluido).
    try:
        carpeta = config.normalize_output_dir(raw_dir) if raw_dir else config.OUTPUT_DIR
    except ValueError as e:
        return {
            "carpeta": raw_dir, "existe": False, "cuentas": [], "target": target,
            "encontrado": False, "registros": 0, "ultimo": None, "archivo": None,
            "sin_convencion": [], "error": str(e),
        }

    info = {
        "carpeta": str(carpeta),
        "existe": carpeta.is_dir(),
        "cuentas": [],        # cuentas con descarga previa en esa carpeta
        "target": target,
        "encontrado": False,
        "registros": 0,
        "ultimo": None,
        "archivo": None,
        "sin_convencion": [],
        "error": None,
    }
    if not info["existe"]:
        return info

    # Cuentas ya bajadas: solo nombres de archivo, es instantaneo.
    #
    # Se miran SOLO los consolidados `<cuenta>_all.json`, que es lo unico que
    # produce esta herramienta. No ampliar a `*_*.json`: la carpeta del caso
    # tiene archivos del perito, y un `informe_2024.json` apareceria
    # como la cuenta "otros_datos". Los chips setean el usuario objetivo con
    # un clic, y al ejecutar, export._load_master_base() absorbe ese archivo
    # como si fueran tweets y _cleanup_loose() borra el original.
    #
    # El precio es que una carpeta con solo archivos sueltos de versiones
    # viejas muestra cero cuentas aunque find_last_state si los encuentre. Es
    # una inconsistencia cosmetica, y del lado seguro.
    info["cuentas"] = sorted(
        f.name[: -len("_all.json")] for f in carpeta.glob("*_all.json")
    )
    info["sin_convencion"] = _descargas_sin_convencion(carpeta)

    # Conteo real solo para la cuenta pedida (parsea el JSON: ~0,3 s en 4 MB).
    if target:
        try:
            seen, last = find_last_state(target, carpeta)
        except OSError:
            seen, last = set(), None
        if last is not None:
            principal = carpeta / f"{target}_all.json"
            info.update(
                encontrado=True,
                registros=len(seen),
                ultimo=last.astimezone().strftime("%d-%m-%Y %H:%M"),
                archivo=principal.name if principal.exists() else None,
            )
    return info


@app.post("/abrir")
def abrir():
    """Abre la carpeta de datos en el gestor de archivos (boton 'Abrir')."""
    # Mismo chequeo de origen que /elegir-carpeta: sin el, cualquier pagina
    # abierta en el navegador podria abrir ventanas del sistema a nombre del
    # usuario. Devuelve JSON porque su JavaScript hace r.json().
    if not _mismo_origen(request):
        return {"ok": False, "carpeta": None, "error": "origen no permitido"}, 403

    raw = (request.args.get("dir") or "").strip()
    try:
        carpeta = config.normalize_output_dir(raw) if raw else config.OUTPUT_DIR
    except ValueError as e:
        return {"ok": False, "carpeta": raw, "error": str(e)}
    ok = carpeta.is_dir() and _open_folder(carpeta)
    return {"ok": ok, "carpeta": str(carpeta)}


@app.post("/elegir-carpeta")
def elegir_carpeta():
    """Abre el selector de carpetas del sistema (boton 'Elegir...').

    Devuelve `cancelado` aparte de `ok` para que el frontend no muestre un error
    cuando el usuario simplemente cerro el dialogo.
    """
    if not _mismo_origen(request):
        return {"ok": False, "carpeta": None, "cancelado": False,
                "error": "origen no permitido"}, 403

    if not _PICKER_LOCK.acquire(blocking=False):
        # cancelado=False a proposito: si esto se reportara como "cancelo", un
        # selector que quedo abierto en otra ventana haria que cada clic no
        # mostrara NADA, y el boton pareceria roto.
        return {"ok": False, "carpeta": None, "cancelado": False,
                "error": "Ya hay un selector de carpetas abierto. Buscalo entre "
                         "las ventanas (puede estar detras del navegador) y "
                         "cerralo antes de volver a intentar."}, 409
    try:
        inicial = (request.args.get("dir") or "").strip()
        if inicial:
            try:
                inicial = str(config.normalize_output_dir(inicial))
            except ValueError:
                inicial = ""   # ruta de Windows heredada: arrancamos del default
        elegida = _pick_folder(inicial or str(config.OUTPUT_DIR))
    finally:
        _PICKER_LOCK.release()

    if elegida is None:
        return {"ok": False, "carpeta": None, "cancelado": True}
    return {"ok": True, "carpeta": elegida, "cancelado": False}


@app.post("/apagar")
def apagar():
    """Detiene el servidor (boton 'Salir' de la interfaz).

    Existe porque lanzada desde el .app no hay consola donde hacer Ctrl+C.

    El mismo chequeo de origen que /elegir-carpeta: sin el, cualquier pagina
    que el usuario visite podria apagarle la herramienta con un fetch a
    127.0.0.1.
    """
    if not _mismo_origen(request):
        return {"ok": False, "error": "origen no permitido"}, 403

    # Se responde primero y se sale un instante despues, para que el navegador
    # alcance a recibir el OK y pueda mostrar el aviso de "ya podes cerrar".
    threading.Timer(0.4, lambda: os._exit(0)).start()
    return {"ok": True}


@app.post("/run")
@_solo_mismo_origen
def run():
    # Los tres modos vienen en un solo radio; se derivan los flags del runner.
    modo = request.form.get("modo", "completa")
    form = {
        "target": request.form.get("target", "").strip(),
        "output_dir": request.form.get("output_dir", "").strip(),
        "since": request.form.get("since", "").strip(),
        "until": request.form.get("until", "").strip(),
        "types": request.form.getlist("types"),
        "formats": request.form.getlist("formats") or ["json", "csv"],
        "max_tweets": request.form.get("max_tweets", "").strip(),
        "modo": modo,
        "historical": modo == "historial",
        "window_days": request.form.get("window_days", "7").strip() or "7",
        "update": modo == "update",
    }
    cookies = build_cookies(
        request.form.get("auth_token", ""), request.form.get("ct0", "")
    )
    max_tweets = int(form["max_tweets"]) if form["max_tweets"].isdigit() else None
    window_days = int(form["window_days"]) if form["window_days"].isdigit() else 7

    # Cookies nuevas: las recordamos en .env y reseteamos la cuenta para que
    # twscrape se de de alta con ellas.
    if cookies:
        _persist_cookies(cookies)
        _reset_account_db()

    # Carpeta de salida: se aplica a esta corrida y se recuerda para la proxima.
    if form["output_dir"]:
        try:
            config.set_output_dir(form["output_dir"])
            _persist_env("SCRAPER_OUTPUT_DIR", str(config.OUTPUT_DIR))
            agregar_reciente(str(config.OUTPUT_DIR))
        except (OSError, ValueError) as e:  # ValueError: ruta de Windows en POSIX
            return render_template_string(
                PAGE, recientes=recientes_ui(), form=form, result=None,
                error=f"No se pudo usar esa carpeta de salida: {e}",
            )

    try:
        result = asyncio.run(run_job(
            target=form["target"],
            type_labels=form["types"],
            since=form["since"] or None,
            until=form["until"] or None,
            max_tweets=max_tweets,
            formats=form["formats"],
            cookies=cookies or None,
            historical=form["historical"],
            window_days=window_days,
            update=form["update"],
        ))
    except ConexionXError as e:
        # Fallo de conexion/sesion: NO es "sin tweets nuevos". Avisamos fuerte.
        return render_template_string(
            PAGE, recientes=recientes_ui(), form=form, result=None,
            error="NO SE PUDO CONECTAR CON X - no se descargo nada "
                  f"(los archivos ya descargados quedaron intactos).\n\n{e}",
        )
    except ValueError as e:
        return render_template_string(PAGE, recientes=recientes_ui(), form=form, result=None, error=str(e))
    except Exception as e:  # noqa: BLE001 - mostramos cualquier fallo en la UI
        return render_template_string(
            PAGE, recientes=recientes_ui(), form=form, result=None,
            error=f"Fallo la corrida: {e}",
        )

    if result["written"]:
        # Hay consolidado (con o sin nuevos): abrimos carpeta y mostramos resumen.
        result["carpeta"] = str(config.OUTPUT_DIR)
        result["abierta"] = _open_folder(config.OUTPUT_DIR)
        return render_template_string(PAGE, recientes=recientes_ui(), form=form, result=result, error=None)

    # Sin archivos: no se recolecto nada y no hay consolidado previo.
    msg = ("No hay descargas previas ni tweets nuevos para esta cuenta."
           if form["update"]
           else "No se recolectaron tweets. Revisa el usuario o las cookies.")
    return render_template_string(PAGE, recientes=recientes_ui(), form=form, result=None, error=msg)


@app.post("/add_ids")
@_solo_mismo_origen
def add_ids():
    ids = parse_ids(request.form.get("tweet_ids", ""))
    try:
        result = asyncio.run(add_by_ids(ids, formats=["json", "csv", "xlsx"]))
    except ValueError as e:
        return render_template_string(PAGE, recientes=recientes_ui(), form=default_form(), result=None, error=str(e))
    except Exception as e:  # noqa: BLE001
        return render_template_string(
            PAGE, recientes=recientes_ui(), form=default_form(),
            result=None, error=f"Fallo: {e}",
        )

    if result["written"]:
        result["carpeta"] = str(config.OUTPUT_DIR)
        result["abierta"] = _open_folder(config.OUTPUT_DIR)
        return render_template_string(PAGE, recientes=recientes_ui(), form=default_form(), result=result, error=None)
    return render_template_string(
        PAGE, recientes=recientes_ui(), form=default_form(), result=None,
        error=f"No se pudo agregar ningun tweet. No encontrados: "
              f"{', '.join(result['not_found']) or '-'}",
    )


if __name__ == "__main__":
    print("Abri http://127.0.0.1:5000 en el navegador.")
    app.run(host="127.0.0.1", port=5000, threaded=True)
