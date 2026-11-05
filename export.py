"""Exporta los registros a JSON, CSV y/o Excel.

Modelo consolidado: por cada cuenta se mantiene un unico set de archivos
`<usuario>_all.json|csv|xlsx`. Cada corrida fusiona lo nuevo ahi (dedup por id,
ordenado por fecha desc), en vez de dejar archivos sueltos por corrida.
"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from openpyxl.utils import get_column_letter

import config

_MIN_DT = datetime.min.replace(tzinfo=timezone.utc)

# Columna de fecha y como se muestra en cada formato de salida.
DATE_COL = "created_at"
DATE_TEXT_FMT = "%d/%m/%Y %H:%M"          # dd/mm/aaaa hh:mm (CSV)
DATE_XLSX_FMT = "DD/MM/YYYY HH:MM"        # idem, como formato de celda Excel
DATE_COL_WIDTH = 18                       # sin esto Excel muestra ####


def _df(records: list[dict], dates: str = "raw") -> pd.DataFrame:
    """DataFrame con media_urls aplanada a string (separada por ';').

    `dates` define como queda created_at:
      "raw"   -> texto crudo de X ("Wed Sep 30 18:19:18 +0000 2026")
      "text"  -> dd/mm/aaaa hh:mm (CSV, que no admite formatos de celda)
      "excel" -> datetime UTC naive (XLSX: Excel no maneja zonas horarias)
    """
    df = pd.DataFrame(records)
    if "media_urls" in df.columns:
        df["media_urls"] = df["media_urls"].apply(
            lambda v: ";".join(v) if isinstance(v, list) else (v or "")
        )
    if dates != "raw" and DATE_COL in df.columns:
        dts = [_parse_dt(v) if isinstance(v, str) else None for v in df[DATE_COL]]
        if dates == "text":
            df[DATE_COL] = [
                dt.strftime(DATE_TEXT_FMT) if dt else "" for dt in dts
            ]
        else:  # excel
            df[DATE_COL] = [
                dt.astimezone(timezone.utc).replace(tzinfo=None) if dt else None
                for dt in dts
            ]
    return df


def _parse_dt(value: str) -> datetime | None:
    """Parsea el formato de fecha de X (o None)."""
    try:
        return datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y")
    except (ValueError, TypeError):
        return None


def _sort_key(rec: dict) -> datetime:
    return _parse_dt(rec.get("created_at", "")) or _MIN_DT


def _write_xlsx(df: pd.DataFrame, path: Path) -> None:
    """Escribe el XLSX con created_at como celda de fecha real dd/mm/aaaa hh:mm.

    Al ser un datetime y no texto, Excel lo ordena y filtra por fecha. El ancho
    se fija a mano porque openpyxl no autoajusta columnas: sin eso, el ancho
    default no entra y Excel muestra #### en vez de la fecha.
    """
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
        ws = writer.book.active
        if DATE_COL in df.columns:
            letter = get_column_letter(df.columns.get_loc(DATE_COL) + 1)
            ws.column_dimensions[letter].width = DATE_COL_WIDTH
            for row in range(2, ws.max_row + 1):
                ws[f"{letter}{row}"].number_format = DATE_XLSX_FMT


def _write_formats(records: list[dict], base: Path, formats: list[str]) -> list[Path]:
    """Escribe los formatos pedidos con nombre `base.<ext>`; devuelve rutas."""
    written: list[Path] = []
    if "json" in formats:
        path = base.with_suffix(".json")
        path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        written.append(path)

    if "csv" in formats:
        path = base.with_suffix(".csv")
        # BOM para Excel
        _df(records, dates="text").to_csv(path, index=False, encoding="utf-8-sig")
        written.append(path)
    if "xlsx" in formats:
        path = base.with_suffix(".xlsx")
        _write_xlsx(_df(records, dates="excel"), path)
        written.append(path)

    return written


def _load_master_base(target_user: str) -> list[dict]:
    """Carga el consolidado existente; si no hay, junta los .json sueltos previos."""
    master = config.OUTPUT_DIR / f"{target_user}_all.json"
    if master.exists():
        try:
            return json.loads(master.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
    # Migracion: primera vez, absorbemos las descargas sueltas de la cuenta.
    base: list[dict] = []
    for filepath in glob.glob(str(config.OUTPUT_DIR / f"{target_user}_*.json")):
        try:
            base.extend(json.loads(open(filepath, encoding="utf-8").read()))
        except (OSError, ValueError):
            continue
    return base


def _cleanup_loose(target_user: str) -> int:
    """Borra los archivos sueltos por-corrida de la cuenta, dejando solo `_all`.

    Se llama despues de escribir el consolidado, asi que su contenido ya esta a
    salvo: lo que se borra aca son copias de corridas viejas.

    OJO con la diferencia entre sistemas: en Windows un archivo abierto (p.ej.
    en Excel) esta bloqueado y os.remove falla, asi que el `except OSError` lo
    saltea. En macOS/Linux no hay bloqueo obligatorio: el borrado igual procede
    y Excel/Numbers se queda con una copia en memoria que ya no existe en disco.
    """
    keep_prefix = f"{target_user}_all."
    removed = 0
    for ext in ("json", "csv", "xlsx"):
        for filepath in glob.glob(str(config.OUTPUT_DIR / f"{target_user}_*.{ext}")):
            if os.path.basename(filepath).startswith(keep_prefix):
                continue  # nunca tocamos el consolidado
            try:
                os.remove(filepath)
                removed += 1
            except OSError:
                pass  # abierto/bloqueado; se saltea
    return removed


def export_consolidated(
    new_records: list[dict], target_user: str, formats: list[str]
) -> tuple[list[Path], int, int]:
    """Fusiona `new_records` en el consolidado de la cuenta y lo reescribe.

    Devuelve (rutas_escritas, agregados_nuevos, total_en_consolidado). El JSON
    del consolidado siempre se escribe (es la fuente para futuras fusiones),
    ademas de los formatos pedidos.
    """
    config.ensure_output_dir()

    by_id: dict[str, dict] = {}
    for rec in _load_master_base(target_user):
        rid = rec.get("id")
        if rid:
            by_id[rid] = rec

    added = 0
    for rec in new_records:
        rid = rec.get("id")
        if not rid:
            continue
        if rid not in by_id:
            added += 1
        by_id[rid] = rec  # lo nuevo pisa lo viejo (actualiza metricas)

    merged = sorted(by_id.values(), key=_sort_key, reverse=True)
    if not merged:
        return [], 0, 0  # nada que consolidar (ej. usuario inexistente)

    fmts = list(dict.fromkeys(["json", *formats]))  # el master siempre lleva json
    base = config.OUTPUT_DIR / f"{target_user}_all"
    written = _write_formats(merged, base, fmts)
    _cleanup_loose(target_user)  # el consolidado ya esta escrito: limpiamos sueltos
    return written, added, len(merged)


def export(records: list[dict], target_user: str, stamp: str,
           formats: list[str]) -> list[Path]:
    """Escribe un snapshot con timestamp (uso puntual; el flujo usa el consolidado)."""
    config.ensure_output_dir()
    base = config.OUTPUT_DIR / f"{target_user}_{stamp}"
    return _write_formats(records, base, formats)


def summarize(records: list[dict]) -> str:
    """Resumen por tipo para imprimir al final."""
    counts: dict[str, int] = {}
    for r in records:
        counts[r["type"]] = counts.get(r["type"], 0) + 1
    parts = [f"{k}: {v}" for k, v in sorted(counts.items())]
    return f"Total: {len(records)} ({', '.join(parts)})"
