@echo off
chcp 65001 >nul
setlocal
title Scraper de X

rem pushd (en vez de cd /d) tambien funciona si el .bat se abre desde una
rem carpeta de red o desde \\wsl$\...: Windows le asigna una letra temporal.
pushd "%~dp0" >nul
if errorlevel 1 (
    echo.
    echo   No se pudo abrir la carpeta de la herramienta:
    echo   "%~dp0"
    echo.
    echo   Copiala a una ruta local, por ejemplo C:\Scraper.
    echo.
    pause
    exit /b 1
)

rem UTF-8 para que los acentos y las rutas con enie no rompan la salida.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

rem Buscar un Python 3.10 o superior. Preferimos 'py -3' (lanzador oficial de
rem Windows) y despues 'python'. Si no hay ninguno, avisamos y NO cerramos la
rem ventana de golpe: el usuario tiene que poder leer el mensaje.
set "PYTHON_CMD="
where py >nul 2>nul && py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
    where python >nul 2>nul && python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
    echo.
    echo   No se encontro Python 3.10 o superior.
    echo.
    echo   1. Descargalo de https://www.python.org/downloads/
    echo   2. En el instalador, marca "Add Python to PATH".
    echo   3. Volve a abrir este archivo con doble clic.
    echo.
    pause
    popd
    exit /b 1
)

rem Toda la logica vive en run_web.py: crea el entorno virtual si falta, elige
rem un puerto libre, abre el navegador y levanta el servidor.
%PYTHON_CMD% run_web.py %*

rem Si hubo un error, deja la ventana abierta para que el mensaje se pueda
rem leer. Si el usuario cerro la herramienta desde el boton "Salir", se cierra
rem sola (no hace falta apretar una tecla de mas).
if errorlevel 1 pause
popd
