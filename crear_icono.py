#!/usr/bin/env python3
"""Genera el icono .icns de la app, sin dependencias externas.

Escribe un PNG a mano (zlib + struct) y lo convierte con `iconutil`, que viene
con macOS. Se usa Pillow en ningun lado a proposito: el proyecto no tiene
dependencias de imagen y no vale la pena agregar una por el icono.

Uso: python crear_icono.py <destino.icns>
"""
from __future__ import annotations

import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

FONDO = (14, 17, 22)        # el mismo #0e1116 de la interfaz
ACENTO = (79, 140, 255)     # el azul #4f8cff de los botones


def _png(ancho: int, alto: int, pixel) -> bytes:
    """PNG RGBA minimo. `pixel(x, y)` devuelve (r, g, b, a)."""
    filas = bytearray()
    for y in range(alto):
        filas.append(0)                       # filtro "None" por scanline
        for x in range(ancho):
            filas.extend(pixel(x, y))

    def chunk(tipo: bytes, datos: bytes) -> bytes:
        return (struct.pack(">I", len(datos)) + tipo + datos
                + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", ancho, alto, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(filas), 9))
            + chunk(b"IEND", b""))


def _dibujo(lado: int):
    """Cuadrado redondeado oscuro con una flecha de descarga azul."""
    radio = lado * 0.22
    margen = lado * 0.06
    cx = lado / 2
    # Proporciones de la flecha, en fracciones del lado.
    tallo_w, tallo_y0, tallo_y1 = lado * 0.10, lado * 0.24, lado * 0.58
    punta_y, punta_w = lado * 0.76, lado * 0.30
    base_y0, base_y1 = lado * 0.82, lado * 0.88
    base_w = lado * 0.42

    def dentro_redondeado(x, y):
        x0 = y0 = margen
        x1 = y1 = lado - margen
        cx_ = min(max(x, x0 + radio), x1 - radio)
        cy_ = min(max(y, y0 + radio), y1 - radio)
        return (x - cx_) ** 2 + (y - cy_) ** 2 <= radio ** 2 or (
            x0 <= x <= x1 and y0 + radio <= y <= y1 - radio) or (
            x0 + radio <= x <= x1 - radio and y0 <= y <= y1)

    def pixel(x, y):
        px, py = x + 0.5, y + 0.5
        if not dentro_redondeado(px, py):
            return (0, 0, 0, 0)
        # tallo
        if abs(px - cx) <= tallo_w / 2 and tallo_y0 <= py <= tallo_y1:
            return (*ACENTO, 255)
        # punta triangular
        if tallo_y1 <= py <= punta_y:
            avance = (py - tallo_y1) / max(punta_y - tallo_y1, 1e-6)
            if abs(px - cx) <= (punta_w / 2) * (1 - avance):
                return (*ACENTO, 255)
        # base
        if base_y0 <= py <= base_y1 and abs(px - cx) <= base_w / 2:
            return (*ACENTO, 255)
        return (*FONDO, 255)

    return pixel


def main() -> int:
    if len(sys.argv) < 2:
        print("uso: crear_icono.py <destino.icns>", file=sys.stderr)
        return 2
    destino = Path(sys.argv[1])

    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icono.iconset"
        iconset.mkdir()
        # Los tamanos que iconutil espera en un .iconset.
        for lado, nombres in [
            (16, ["icon_16x16.png"]), (32, ["icon_16x16@2x.png", "icon_32x32.png"]),
            (64, ["icon_32x32@2x.png"]), (128, ["icon_128x128.png"]),
            (256, ["icon_128x128@2x.png", "icon_256x256.png"]),
            (512, ["icon_256x256@2x.png", "icon_512x512.png"]),
            (1024, ["icon_512x512@2x.png"]),
        ]:
            datos = _png(lado, lado, _dibujo(lado))
            for nombre in nombres:
                (iconset / nombre).write_bytes(datos)

        r = subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(destino)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr.strip(), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
