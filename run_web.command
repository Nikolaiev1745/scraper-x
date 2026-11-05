#!/bin/bash
# Lanza la interfaz web del scraper. Doble clic para abrir (macOS), o
# ./run_web.command desde la terminal (macOS y Linux).
#
# Se llama .command y no .sh porque esa es la extension que el Finder abre con
# doble clic. Toda la logica vive en run_web.py, que es multiplataforma.
cd "$(dirname "$0")" || exit 1
exec python3 run_web.py "$@"
