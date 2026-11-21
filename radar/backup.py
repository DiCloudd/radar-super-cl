"""Cifrado de la BD con Fernet (clave en el secret DB_KEY) para guardarla en la rama pública gh-pages."""
from __future__ import annotations

import gzip
import os
from pathlib import Path

from cryptography.fernet import Fernet


def _key() -> bytes:
    k = os.environ.get("DB_KEY", "").strip()
    if not k:
        raise SystemExit("Falta DB_KEY (GitHub Secret). No se puede cifrar/descifrar la BD.")
    return k.encode()


def encrypt(src: Path, dst: Path) -> int:
    data = Fernet(_key()).encrypt(gzip.compress(src.read_bytes(), compresslevel=6))
    dst.write_bytes(data)
    return len(data)


def decrypt(src: Path, dst: Path) -> int:
    data = gzip.decompress(Fernet(_key()).decrypt(src.read_bytes()))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    return len(data)
