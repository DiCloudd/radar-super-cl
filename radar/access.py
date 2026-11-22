"""Control de acceso por dispositivo para la app (sitio estático en GitHub Pages).

En un sitio estático cualquiera puede descargar los archivos, así que el control real es CIFRADO:
- Cada dispositivo genera en su navegador un par de llaves ECDH P-256; la privada nunca sale del
  teléfono (WebCrypto, no exportable). Su identidad = huella SHA-256 de la llave pública.
- En cada publicación se crea una llave de datos aleatoria (AES-256-GCM) que cifra data.json y
  products.json (comprimidos). Esa llave se "envuelve" (ECIES: ECDH efímero + HKDF + AES-GCM)
  SOLO para los dispositivos aprobados -> keys.json.
- Bloquear = quitarlo de la lista: desde la siguiente publicación ya no recibe la llave nueva.
- La lista de dispositivos se guarda cifrada con DB_KEY (access/devices.enc): el repo público no
  revela apodos ni llaves.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .models import now_iso

DEVICES_FILE = Path(__file__).resolve().parent.parent / "access" / "devices.enc"
HKDF_SALT = b"radar-super-salt-v1"
HKDF_INFO = b"radar-super-wrap-v1"
SEALED = ("data.json", "products.json")
_B32 = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # sin 0/O/1/I para dictarlo sin confusiones


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def device_id(pub_raw: bytes) -> str:
    h = hashlib.sha256(pub_raw).digest()
    n = int.from_bytes(h[:5], "big")
    chars = "".join(_B32[(n >> (5 * i)) & 31] for i in range(8))[::-1]
    return f"{chars[:4]}-{chars[4:]}"


def parse_request(text: str) -> tuple[bytes, str]:
    """'RADAR1.<descripción>.<llave pública b64url>' -> (pub_raw, descripción)."""
    t = text.strip()
    if not t.startswith("RADAR1."):
        raise ValueError("La solicitud debe empezar con RADAR1.")
    _, desc, pub = t.split(".", 2)
    raw = unb64u(pub)
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)   # valida que sea un punto válido
    return raw, unb64u(desc).decode("utf-8", "replace")[:60] if desc else ""


# ---------------- lista de dispositivos (cifrada con DB_KEY) ----------------
def _fernet() -> Fernet:
    k = os.environ.get("DB_KEY", "").strip()
    if not k:
        raise SystemExit("Falta DB_KEY")
    return Fernet(k.encode())


def load_devices(path: Path = DEVICES_FILE) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(_fernet().decrypt(path.read_bytes()))["devices"]


def save_devices(devices: list[dict], path: Path = DEVICES_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_fernet().encrypt(json.dumps({"devices": devices}, ensure_ascii=False).encode()))


def apply_action(devices: list[dict], action: str, code: str, name: str = "") -> tuple[list[dict], str]:
    """aprobar (con solicitud RADAR1...), bloquear / desbloquear / eliminar (con solicitud o código)."""
    code = code.strip()
    if code.startswith("RADAR1."):
        raw, desc = parse_request(code)
        did = device_id(raw)
    else:
        raw, desc, did = None, "", code.upper()
    dev = next((d for d in devices if d["id"] == did), None)
    if action == "aprobar":
        if raw is None and dev is None:
            raise ValueError("Para aprobar un dispositivo nuevo pega la solicitud completa (RADAR1...).")
        if dev is None:
            dev = {"id": did, "pub": b64u(raw), "desc": desc, "added": now_iso()}
            devices.append(dev)
        dev["status"] = "ok"
        if name:
            dev["name"] = name[:40]
        return devices, f"✅ {did} aprobado ({dev.get('name') or dev.get('desc') or 'sin apodo'})"
    if dev is None:
        raise ValueError(f"No existe el dispositivo {did}")
    if action in ("bloquear", "desbloquear"):
        dev["status"] = "blocked" if action == "bloquear" else "ok"
        dev["changed"] = now_iso()
        return devices, f"{'⛔' if action == 'bloquear' else '✅'} {did} {action}do"
    if action == "eliminar":
        return [d for d in devices if d["id"] != did], f"🗑️ {did} eliminado"
    raise ValueError(f"Acción desconocida: {action}")


# ---------------- sellado de los datos publicados ----------------
def wrap_for(pub_raw: bytes, did: str, data_key: bytes) -> dict:
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub_raw)
    eph = ec.generate_private_key(ec.SECP256R1())
    shared = eph.exchange(ec.ECDH(), peer)
    wk = HKDF(algorithm=hashes.SHA256(), length=32, salt=HKDF_SALT, info=HKDF_INFO).derive(shared)
    iv = os.urandom(12)
    ct = AESGCM(wk).encrypt(iv, data_key, did.encode())
    epk = eph.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"epk": b64u(epk), "iv": b64u(iv), "ct": b64u(ct)}


def seal(site: Path, devices: list[dict]) -> dict:
    """Cifra data.json/products.json del sitio y escribe keys.json. Borra los JSON en claro."""
    data_key = AESGCM.generate_key(bit_length=256)
    aes = AESGCM(data_key)
    for name in SEALED:
        src = site / name
        if not src.exists():
            continue
        iv = os.urandom(12)
        blob = iv + aes.encrypt(iv, gzip.compress(src.read_bytes(), 6), name.encode())
        (site / name.replace(".json", ".bin")).write_bytes(blob)
        src.unlink()
    approved = [d for d in devices if d.get("status") == "ok"]
    keys = {"v": 1, "generated": now_iso(),
            "devices": {d["id"]: wrap_for(unb64u(d["pub"]), d["id"], data_key) for d in approved}}
    (site / "keys.json").write_text(json.dumps(keys), encoding="utf-8")
    return {"aprobados": len(approved), "bloqueados": sum(1 for d in devices if d.get("status") == "blocked")}


def public_list(devices: list[dict]) -> list[dict]:
    """Lista que se incluye DENTRO de data.json (cifrado): solo la ven dispositivos aprobados."""
    return [{"id": d["id"], "name": d.get("name") or "", "desc": d.get("desc") or "", "status": d.get("status"),
             "added": (d.get("added") or "")[:10]} for d in devices]
