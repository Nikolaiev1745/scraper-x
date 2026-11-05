"""CLI del scraper: orquesta auth -> scrape -> export (via runner.run_job).

Ejemplo:
    python main.py --target-user miusuario --max-tweets 200 --types tweets,replies
"""
from __future__ import annotations

import argparse
import asyncio
import sys

import config
from runner import DEFAULT_TYPE_LABELS, run_job
from scraper import ConexionXError


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Scraper de tweets/retweets/replies de un perfil de X."
    )
    p.add_argument(
        "--target-user",
        default=config.DEFAULT_TARGET,
        help="Usuario de X a scrapear (sin @). Default: TARGET_USERNAME del .env",
    )
    p.add_argument(
        "--types",
        default=",".join(DEFAULT_TYPE_LABELS),
        help="Tipos a incluir, separados por coma: tweets,retweets,replies",
    )
    p.add_argument(
        "--max-tweets", type=int, default=None,
        help="Cantidad maxima de tweets a recolectar (default: sin limite).",
    )
    p.add_argument(
        "--since", default=None,
        help="Fecha minima YYYY-MM-DD: no recolecta tweets mas viejos.",
    )
    p.add_argument(
        "--until", default=None,
        help="Fecha maxima YYYY-MM-DD: no recolecta tweets mas nuevos.",
    )
    p.add_argument(
        "--update", action="store_true",
        help="Trae solo lo nuevo desde la ultima descarga de la cuenta "
             "(busca en la carpeta de salida).",
    )
    p.add_argument(
        "--historical", action="store_true",
        help="Historial por fechas (busqueda) para pasar el tope ~3200. "
             "Requiere --since. No incluye retweets.",
    )
    p.add_argument(
        "--window-days", type=int, default=7,
        help="Tamano de cada ventana del modo historial, en dias (default: 7).",
    )
    p.add_argument(
        "--formats", default="json,csv",
        help="Formatos de salida separados por coma: json,csv,xlsx",
    )
    p.add_argument(
        "--output-dir", default=None,
        help="Carpeta donde guardar las descargas (default: SCRAPER_OUTPUT_DIR "
             "del .env, o output/ dentro de la herramienta). Usalo para escribir "
             "directo en la carpeta del caso.",
    )
    return p.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    type_labels = [t.strip() for t in args.types.split(",") if t.strip()]
    formats = [f.strip() for f in args.formats.split(",") if f.strip()]

    if args.output_dir:
        try:
            config.set_output_dir(args.output_dir)
        except (OSError, ValueError) as e:  # ValueError: ruta de Windows en POSIX
            print(f"ERROR: {e}", file=sys.stderr)
            return 2

    print(f"[main] Objetivo: @{args.target_user} | tipos={type_labels} | "
          f"max={args.max_tweets} | since={args.since} | until={args.until} | "
          f"historial={args.historical} | formatos={formats}")
    print(f"[main] Salida: {config.OUTPUT_DIR}")

    try:
        result = await run_job(
            target=args.target_user,
            type_labels=type_labels,
            since=args.since,
            until=args.until,
            max_tweets=args.max_tweets,
            formats=formats,
            historical=args.historical,
            window_days=args.window_days,
            update=args.update,
        )
    except ConexionXError as e:
        print("\n" + "=" * 62, file=sys.stderr)
        print("NO SE PUDO CONECTAR CON X - no se descargo nada.", file=sys.stderr)
        print("(esto NO significa que no haya tweets nuevos)", file=sys.stderr)
        print("=" * 62, file=sys.stderr)
        print(f"{e}", file=sys.stderr)
        return 3
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if not result["count"]:
        if result.get("aviso"):
            # El fallo real no fue el usuario ni la sesion, sino el tope de X:
            # decir "revisa el usuario" mandaria a revisar una causa que no es.
            print("[main] No se recolectaron tweets. " + result["aviso"])
        else:
            print("[main] No se recolectaron tweets. "
                  "Revisa el usuario objetivo o la sesion.")
        return 1

    print("\n[main] " + result["summary"])
    if result.get("aviso"):
        print("[main] AVISO: " + result["aviso"])
    print("[main] Archivos generados:")
    for path in result["written"]:
        print(f"         {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
