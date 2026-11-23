"""Cifrado de la BD con Fernet (clave en el secret DB_KEY) para guardarla en la rama pública gh-pages.

Se guarda en BINARIO (el token Fernet decodificado de base64). Motivo: como texto base64, trozos
aleatorios del cifrado pueden parecer "tokens" y el escáner de secretos de GitHub bloquea el push
(pasó con un falso "Lichess token"). Los binarios no se escanean. Se siguen leyendo los archivos
antiguos en texto.
"""
from __future__ import annotations

import base64
import gzip
import os
from pathlib import Path

from cryptography.fernet import Fernet

_TEXT_PREFIX = b"gAAAAA"   # todo token Fernet en texto empieza así (versión 0x80 + timestamp)


def _key() -> bytes:
    k = os.environ.get("DB_KEY", "").strip()
    if not k:
        raise SystemExit("Falta DB_KEY (GitHub Secret). No se puede cifrar/descifrar la BD.")
    return k.encode()


def seal_bytes(data: bytes) -> bytes:
    """Fernet -> binario (no escaneable como texto)."""
    return base64.urlsafe_b64decode(Fernet(_key()).encrypt(data))


def open_bytes(blob: bytes) -> bytes:
    token = blob.strip() if blob.startswith(_TEXT_PREFIX) else base64.urlsafe_b64encode(blob)
    return Fernet(_key()).decrypt(token)


def encrypt(src: Path, dst: Path) -> int:
    data = seal_bytes(gzip.compress(src.read_bytes(), compresslevel=6))
    dst.write_bytes(data)
    return len(data)


def decrypt(src: Path, dst: Path) -> int:
    data = gzip.decompress(open_bytes(src.read_bytes()))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    return len(data)
