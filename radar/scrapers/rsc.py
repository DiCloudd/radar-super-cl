"""Decodificador mínimo del payload RSC de Next.js App Router (self.__next_f.push).

El HTML trae filas "id:valor" donde los valores pueden referenciar otras filas con "$id".
Solo necesitamos leer objetos de datos, no reconstruir la UI.
"""
from __future__ import annotations

import json
import re

_PUSH = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', re.S)
_ROW = re.compile(r"(?:^|\n)([0-9a-f]{1,6}):", re.I)
_REF = re.compile(r"^\$([0-9a-f]{1,6})$", re.I)
_DEC = json.JSONDecoder()


def payload_text(html: str) -> str:
    return "".join(json.loads(m.group(1)) for m in _PUSH.finditer(html))


def parse_rows(text: str) -> dict[str, object]:
    rows: dict[str, object] = {}
    for m in _ROW.finditer(text):
        start = m.end()
        if start >= len(text):
            break
        ch = text[start]
        if ch not in '[{"' and not ch.isdigit() and ch not in "-tfn":
            continue  # filas "I[...]", "T...", "HL[...]" etc.: no son datos
        try:
            val, _ = _DEC.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        rows[m.group(1)] = val
    return rows


def resolve(value, rows: dict[str, object], depth: int = 0):
    """Sustituye referencias "$id" por su valor (profundidad limitada)."""
    if depth > 12:
        return value
    if isinstance(value, str):
        m = _REF.match(value)
        if m and m.group(1) in rows:
            return resolve(rows[m.group(1)], rows, depth + 1)
        if value == "$undefined":
            return None
        return value
    if isinstance(value, list):
        return [resolve(v, rows, depth + 1) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v, rows, depth + 1) for k, v in value.items()}
    return value


def walk_dicts(value):
    stack = [value]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            yield v
            stack.extend(v.values())
        elif isinstance(v, list):
            stack.extend(v)
