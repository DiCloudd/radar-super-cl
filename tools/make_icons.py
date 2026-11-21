"""Genera los íconos PNG de la PWA (sin dependencias): radar verde con aguja blanca."""
import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "dashboard"
GREEN = (10, 125, 79)
WHITE = (255, 255, 255)


def pixel(x, y, n):
    c = n / 2
    dx, dy = x - c, y - c
    r = math.hypot(dx, dy) / (n / 2)
    for ring, w in ((0.62, 0.05), (0.34, 0.05)):
        if abs(r - ring) < w:
            return WHITE
    if r < 0.12:
        return WHITE
    # aguja hacia arriba-derecha
    ang = math.atan2(-dy, dx)
    if 0 < r < 0.62 and abs(ang - math.radians(40)) < 0.09 / max(r, 0.15):
        return WHITE
    return GREEN


def png(n, path):
    raw = b"".join(b"\x00" + bytes(v for x in range(n) for v in pixel(x, y, n)) for y in range(n))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    path.write_bytes(data)


if __name__ == "__main__":
    png(180, OUT / "icon-180.png")
    png(512, OUT / "icon-512.png")
    print("ok")
