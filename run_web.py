#!/usr/bin/env python3
"""Lanzador multiplataforma de la interfaz web (macOS, Windows y Linux).

Hace cuatro cosas, en este orden:
  1. Si falta el entorno virtual o las dependencias, las instala.
  2. Ubica el Python del entorno virtual segun el sistema operativo.
  3. Si no lo estamos usando, se relanza con el (asi funciona igual si arrancas
     con el `python3` del sistema, o con doble clic desde el Finder).
  4. Abre el navegador y levanta el servidor Flask de `app.py`.

El paso 1 existe para que la herramienta se pueda entregar a alguien que tiene
Python pero no maneja la terminal: doble clic en el lanzador y listo, la primera
vez tarda unos minutos y las siguientes arranca directo. Es el mismo archivo
para instalar y para usar, a proposito, asi no hay que elegir cual abrir.

IMPORTANTE: este archivo se ejecuta ANTES de que exista el entorno virtual, asi
que solo puede usar la libreria estandar. No importar `config` ni ninguna
dependencia de requirements.txt en el nivel superior.

Uso:
    python3 run_web.py          (o doble clic en run_web.command / run_web.bat)
"""
from __future__ import annotations

import hashlib
import os
import shutil
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 5000
PUERTOS_ALTERNATIVOS = 10  # 5000..5009
MARCA = "Scraper de X"     # aparece en el <title>: identifica a NUESTRO servidor

# Minimo real de las dependencias: twscrape pide 3.10+. Sin este chequeo, una
# maquina con 3.9 falla recien dentro de pip, con un error largo e ilegible.
PYTHON_MINIMO = (3, 10)
REQUIREMENTS = BASE_DIR / "requirements.txt"

# Windows corta las rutas en 260 caracteres salvo que el sistema tenga
# habilitadas las rutas largas. numpy instala archivos a unos 140 caracteres de
# profundidad dentro del venv, asi que una carpeta muy anidada -el caso tipico es
# descomprimir dentro de un OneDrive corporativo- hace fallar a pip con un
# WinError 206 que no explica nada. Se chequea antes de instalar porque el
# mensaje de pip apunta para cualquier lado.
LIMITE_RUTA_WINDOWS = 260
PROFUNDIDAD_VENV = 140

IS_WINDOWS = os.name == "nt"


def venv_python() -> Path:
    """Ruta al interprete del entorno virtual del proyecto.

    Windows lo deja en .venv\\Scripts\\python.exe; el resto en .venv/bin/python.
    Esto duplica a proposito la logica de config.VENV_PYTHON_HINT: config.py
    importa python-dotenv, que puede no estar instalado todavia cuando corre
    este lanzador.
    """
    if IS_WINDOWS:
        return BASE_DIR / ".venv" / "Scripts" / "python.exe"
    return BASE_DIR / ".venv" / "bin" / "python"


def instrucciones_instalacion() -> str:
    """Pasos de instalacion del sistema actual, para cuando falta el .venv."""
    if IS_WINDOWS:
        return (
            "  py -3 -m venv .venv\n"
            "  .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt\n"
            "  copy .env.example .env      (y completa X_COOKIES)"
        )
    return (
        "  python3 -m venv .venv\n"
        "  ./.venv/bin/python -m pip install -r requirements.txt\n"
        "  cp .env.example .env        (y completa X_COOKIES)"
    )


def venv_dir() -> Path:
    """Carpeta del entorno virtual del proyecto."""
    return BASE_DIR / ".venv"


def _marca_instalacion() -> Path:
    """Archivo que certifica que las dependencias quedaron instaladas.

    No alcanza con mirar si existe el interprete del venv: si `pip install` se
    corta por la mitad (se cae la red, Ctrl+C), el interprete queda creado pero
    sin dependencias, y el arranque siguiente fallaria recien al importar flask,
    con un traceback que no le dice nada al usuario. La marca se escribe solo
    despues de un pip exitoso.

    Guarda ademas el hash de requirements.txt, asi una version nueva de la
    herramienta con dependencias distintas se reinstala sola en vez de arrancar
    con las viejas.
    """
    return venv_dir() / ".instalado"


def _hash_requirements() -> str:
    """Huella de las dependencias pedidas, o "" si no se puede leer.

    Hashea el contenido normalizado (sin comentarios, sin lineas vacias, sin
    finales de linea) y no los bytes crudos: asi un checkout con otro CRLF/LF, o
    un comentario agregado, no disparan una reinstalacion que no hace falta.
    Solo cambia cuando cambian las dependencias de verdad.
    """
    try:
        texto = REQUIREMENTS.read_text(encoding="utf-8")
    except OSError:
        return ""
    lineas = [ln.strip() for ln in texto.splitlines()]
    pedidas = [ln for ln in lineas if ln and not ln.startswith("#")]
    return hashlib.sha256("\n".join(pedidas).encode("utf-8")).hexdigest()


def _instalacion_al_dia() -> bool:
    """True si el venv existe y sus dependencias son las de requirements.txt."""
    if not venv_python().exists():
        return False
    try:
        marca = _marca_instalacion().read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return bool(marca) and marca == _hash_requirements()


def _rutas_largas_habilitadas() -> bool:
    """True si Windows tiene levantado el limite de 260 caracteres.

    Es una opcion del sistema (LongPathsEnabled). Si esta activa no hay problema
    de profundidad y no tiene sentido molestar al usuario.
    """
    try:
        import winreg  # noqa: PLC0415 - solo existe en Windows
    except ImportError:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\FileSystem",
        ) as clave:
            return winreg.QueryValueEx(clave, "LongPathsEnabled")[0] == 1
    except OSError:
        return False


def _ruta_demasiado_profunda() -> bool:
    """True si instalar aca va a chocar contra el limite de rutas de Windows."""
    if not IS_WINDOWS:
        return False
    if len(str(BASE_DIR)) + PROFUNDIDAD_VENV <= LIMITE_RUTA_WINDOWS:
        return False
    return not _rutas_largas_habilitadas()


def _instalar() -> bool:
    """Crea el entorno virtual e instala las dependencias. True si salio bien.

    Usa `sys.executable`, que es el Python con el que nos lanzaron: el del
    sistema en la primera corrida.
    """
    print(" Primera vez: preparando el entorno.")
    print(" Tarda unos minutos. No cierres esta ventana.\n")

    # El proyecto puede venir de otra plataforma: por ejemplo, una carpeta
    # creada en WSL/Linux y abierta despues con doble clic en Windows. En ese
    # caso existe .venv pero con el interprete de Linux (bin/python), no el de
    # Windows (Scripts/python.exe). Se descarta y se crea uno limpio; si no, la
    # instalacion quedaria mezclada y fallaria de formas dificiles de explicar.
    if venv_dir().exists() and not venv_python().exists():
        print(" [1/2] El entorno virtual es de otro sistema; se recrea.")
        shutil.rmtree(venv_dir(), ignore_errors=True)

    if not venv_python().exists():
        print(f" [1/2] Creando el entorno virtual en {venv_dir()}")
        if subprocess.run([sys.executable, "-m", "venv", str(venv_dir())]).returncode != 0:
            _avisar(
                "No se pudo preparar el entorno",
                f"Fallo la creacion del entorno virtual en:\n{venv_dir()}\n\n"
                f"Revisa que tengas permiso de escritura en esa carpeta. Si la "
                f"herramienta esta en una carpeta de red o sincronizada, proba "
                f"copiarla a tu disco local.",
            )
            return False
    else:
        print(" [1/2] El entorno virtual ya existe.")

    print(f" [2/2] Instalando las dependencias de {REQUIREMENTS.name}...\n")
    instalado = subprocess.run(
        [str(venv_python()), "-m", "pip", "install", "-r", str(REQUIREMENTS)]
    )
    if instalado.returncode != 0:
        _avisar(
            "No se pudieron instalar las dependencias",
            "Fallo la descarga o instalacion de las librerias. Mira el detalle "
            "rojo de arriba; las dos causas habituales son:\n\n"
            "  - La red bloquea el acceso a pypi.org (proxy o firewall).\n"
            "  - 'WinError 206': la carpeta esta demasiado anidada. Move la "
            "herramienta a una ruta corta, por ejemplo C:\\Scraper.\n\n"
            "Para hacerlo a mano, parado en la carpeta del proyecto:\n\n"
            f"{instrucciones_instalacion()}",
        )
        return False

    try:
        _marca_instalacion().write_text(_hash_requirements(), encoding="utf-8")
    except OSError:
        # Sin la marca solo se paga un pip de mas en el proximo arranque (que va
        # a decir "already satisfied"), no vale la pena abortar por esto.
        pass

    print("\n Entorno listo.\n")
    return True


def _preparar_entorno() -> bool:
    """Deja el entorno listo para arrancar. False si hay que abortar.

    Concentra los tres motivos por los que la instalacion no puede seguir, cada
    uno con su mensaje: Python viejo, sin consola donde mostrar el avance, o
    fallo de la instalacion misma.
    """
    if _instalacion_al_dia():
        return True

    if sys.version_info < PYTHON_MINIMO:
        actual = ".".join(str(n) for n in sys.version_info[:3])
        minimo = ".".join(str(n) for n in PYTHON_MINIMO)
        _avisar(
            "La version de Python es muy vieja",
            f"Esta herramienta necesita Python {minimo} o superior, y aca hay "
            f"{actual}.\n\n"
            f"Instala una version mas nueva desde python.org (en Windows, marca "
            f"'Add to PATH' en el instalador) y volve a abrir este lanzador.",
        )
        return False

    if _ruta_demasiado_profunda():
        _avisar(
            "La carpeta esta demasiado anidada",
            f"Windows no admite rutas de mas de {LIMITE_RUTA_WINDOWS} caracteres, y "
            f"esta carpeta ya usa {len(str(BASE_DIR))}:\n\n{BASE_DIR}\n\n"
            f"La instalacion de las librerias fallaria por eso, con un error que "
            f"no lo explica.\n\n"
            f"Move la herramienta a una ruta corta, por ejemplo C:\\Scraper, y "
            f"volve a abrir el lanzador.",
        )
        return False

    # La instalacion imprime el avance de pip durante varios minutos. Sin
    # consola donde verlo (el .app de macOS), parece colgada: mejor mandar al
    # usuario al lanzador que si la tiene.
    if not _hay_terminal():
        lanzador = "run_web.bat" if IS_WINDOWS else "run_web.command"
        _avisar(
            "Falta preparar el entorno",
            f"La primera vez hay que instalar las dependencias, y eso muestra "
            f"su avance en una ventana de texto.\n\n"
            f"Abri '{lanzador}' en la carpeta del proyecto, espera a que termine, "
            f"y despues ya podes usar esta app normalmente.",
        )
        return False

    return _instalar()


def _responde_nuestra_app(puerto: int) -> bool:
    """True si en ese puerto ya esta corriendo ESTE scraper.

    Hace falta distinguirlo de otra cosa: en macOS el receptor de AirPlay
    (ControlCenter) escucha en el 5000 y contesta 403 con Server: AirTunes.

    Timeout corto a proposito: esto se consulta para cada puerto del rango y
    todo es loopback, donde un puerto cerrado responde al instante.
    """
    try:
        with urllib.request.urlopen(f"http://{HOST}:{puerto}/", timeout=0.4) as r:
            return MARCA in r.read(4096).decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _puerto_libre(desde: int = PORT) -> int | None:
    """Primer puerto que podamos usar realmente, o None si no hay ninguno.

    Se prueba bindeando: preguntar "esta ocupado?" y despues bindear tiene una
    carrera, y ademas en macOS el 5000 puede estar tomado por AirPlay.
    """
    for puerto in range(desde, desde + PUERTOS_ALTERNATIVOS):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, puerto))
                return puerto
            except OSError:
                continue
    return None


def _hay_terminal() -> bool:
    """True si nos lanzaron desde una consola donde el usuario puede leer."""
    try:
        return sys.stdout is not None and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _avisar(titulo: str, mensaje: str) -> None:
    """Muestra un error de arranque por el canal que el usuario pueda ver.

    Lanzado desde el .app no hay consola: un print se pierde y el programa
    parece no hacer nada. En ese caso va a un dialogo del sistema.
    """
    if _hay_terminal():
        print(f"{titulo}\n\n{mensaje}")
        return
    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["osascript", "-e",
                 'display dialog {} with title {} buttons {{"Entendido"}} '
                 'default button 1 with icon caution'.format(
                     _as_literal(mensaje), _as_literal(titulo))],
                capture_output=True, timeout=120,
            )
            return
        except Exception:  # noqa: BLE001 - si el dialogo falla, queda el print
            pass
    if IS_WINDOWS:
        try:
            import ctypes  # noqa: PLC0415 - solo existe en Windows
            MB_ICONWARNING = 0x30
            ctypes.windll.user32.MessageBoxW(
                None, f"{titulo}\n\n{mensaje}", "Scraper de X", MB_ICONWARNING
            )
            return
        except Exception:  # noqa: BLE001 - si el dialogo falla, queda el print
            pass
    print(f"{titulo}\n\n{mensaje}", file=sys.stderr)


def _as_literal(texto: str) -> str:
    """Cita un texto como literal de AppleScript, escapando lo necesario."""
    return '"' + texto.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _venv_activo() -> bool:
    """True si el proceso que corre ya tiene el venv como entorno.

    La pregunta NO es "si el interprete es el mismo archivo": dentro de un venv,
    `bin/python` es un symlink al Python del sistema, asi que comparar rutas
    resueltas dice que siempre somos el mismo -- se compara con el Python del
    sistema como "iguales" y nunca nos relanzamos. Despues `import flask` falla
    con ModuleNotFoundError, que es exactamente el crash que este re-lanzamiento
    existe para evitar.

    Lo que de verdad distingue a los dos es `sys.prefix`: dentro del venv apunta
    a la carpeta del venv, y por fuera apunta a donde esta instalado el Python
    del sistema (/usr en Linux, /Library/Frameworks en macOS).
    """
    try:
        return Path(sys.prefix).resolve() == venv_dir().resolve()
    except OSError:
        return False


def main() -> int:
    if not _preparar_entorno():
        return 1

    py = venv_python()

    # Si nos lanzaron con otro Python (el del sistema, por ejemplo), nos
    # relanzamos con el del venv para tener las dependencias a mano.
    if not _venv_activo():
        os.execv(str(py), [str(py), str(Path(__file__).resolve()), *sys.argv[1:]])
        # os.execv reemplaza el proceso: no se vuelve de aca.

    # Ya hay una instancia nuestra levantada: abrimos esa en vez de arrancar
    # otra. Se busca en TODO el rango, no solo en PORT: si el 5000 lo tiene
    # AirPlay, la instancia previa quedo en el 5001 y mirar solo el 5000
    # levantaria una segunda copia en el 5002.
    for puerto in range(PORT, PORT + PUERTOS_ALTERNATIVOS):
        if _responde_nuestra_app(puerto):
            url = f"http://{HOST}:{puerto}"
            print(f" El scraper ya estaba abierto en {url}")
            print(" Se abre el navegador ahi.")
            print(" Para reiniciarlo, cerra primero la otra ventana con Ctrl+C.")
            webbrowser.open(url)
            return 0

    puerto = _puerto_libre()
    if puerto is None:
        _avisar(
            "No se pudo abrir el servidor",
            f"No hay ningun puerto libre entre {PORT} y "
            f"{PORT + PUERTOS_ALTERNATIVOS - 1}.\n\n"
            f"Cerra otros programas que esten usando esos puertos y volve a intentar.",
        )
        return 1

    url = f"http://{HOST}:{puerto}"
    print(" Scraper de X - interfaz web")
    print(f" Abriendo {url}")
    if puerto != PORT:
        print(f" (el puerto {PORT} estaba ocupado; se usa el {puerto})")
        if sys.platform == "darwin":
            # Causa habitual en macOS: el receptor de AirPlay escucha en *:5000
            # y encima contesta 403 a http://localhost:5000, lo que hace parecer
            # que el scraper esta roto. Por eso conviene no pelear por ese puerto.
            print("  En macOS suele ser el receptor de AirPlay. Se puede apagar en")
            print("  Ajustes del Sistema > General > AirDrop y Handoff, pero no hace falta.")
    print(" (si el navegador dice que no se puede conectar, refresca en 2-3 segundos)")
    print(" Para detener el servidor: Ctrl+C\n")

    # Damos un margen para que Flask este escuchando antes de abrir el navegador.
    # webbrowser es de la stdlib y ya resuelve el navegador de cada sistema.
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    import app  # noqa: PLC0415 - despues del re-exec, ya con el venv activo

    try:
        app.app.run(host=HOST, port=puerto, threaded=True)
    except KeyboardInterrupt:
        pass
    print("\nEl servidor se detuvo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
