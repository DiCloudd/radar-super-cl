"""Control de acceso: solo dispositivos aprobados pueden descifrar los datos publicados."""
import base64
import gzip
import json

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from radar import access


class Device:
    """Simula lo que hace el navegador (WebCrypto) en el teléfono."""

    def __init__(self, desc="iPhone · Safari"):
        self.priv = ec.generate_private_key(ec.SECP256R1())
        self.raw = self.priv.public_key().public_bytes(serialization.Encoding.X962,
                                                       serialization.PublicFormat.UncompressedPoint)
        self.id = access.device_id(self.raw)
        self.request = f"RADAR1.{access.b64u(desc.encode())}.{access.b64u(self.raw)}"

    def open(self, site):
        keys = json.loads((site / "keys.json").read_text())
        w = keys["devices"].get(self.id)
        if not w:
            return None
        peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), access.unb64u(w["epk"]))
        shared = self.priv.exchange(ec.ECDH(), peer)
        wk = HKDF(algorithm=hashes.SHA256(), length=32, salt=access.HKDF_SALT, info=access.HKDF_INFO).derive(shared)
        dk = AESGCM(wk).decrypt(access.unb64u(w["iv"]), access.unb64u(w["ct"]), self.id.encode())
        blob = (site / "data.bin").read_bytes()
        return json.loads(gzip.decompress(AESGCM(dk).decrypt(blob[:12], blob[12:], b"data.json")))


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_KEY", Fernet.generate_key().decode())
    (tmp_path / "data.json").write_text(json.dumps({"hola": "mundo"}))
    (tmp_path / "products.json").write_text(json.dumps({"products": []}))
    return tmp_path


def test_device_id_format():
    d = Device()
    assert len(d.id) == 9 and d.id[4] == "-"
    assert not set(d.id.replace("-", "")) & set("01OI")


def test_approved_device_can_read_blocked_cannot(site, tmp_path):
    a, b = Device(), Device("Android · Chrome")
    devs, msg = access.apply_action([], "aprobar", a.request, "mi iphone")
    devs, _ = access.apply_action(devs, "aprobar", b.request)
    devs, _ = access.apply_action(devs, "bloquear", b.id)
    assert devs[0]["desc"] == "iPhone · Safari" and devs[0]["name"] == "mi iphone"
    res = access.seal(site, devs)
    assert res == {"aprobados": 1, "bloqueados": 1}
    assert not (site / "data.json").exists() and not (site / "products.json").exists()  # nada en claro
    assert a.open(site) == {"hola": "mundo"}
    assert b.open(site) is None


def test_devices_file_is_encrypted(site, tmp_path):
    f = tmp_path / "devices.enc"
    a = Device()
    devs, _ = access.apply_action([], "aprobar", a.request, "apodo secreto")
    access.save_devices(devs, f)
    assert b"apodo secreto" not in f.read_bytes()
    assert access.load_devices(f)[0]["name"] == "apodo secreto"


def test_invalid_requests_rejected():
    with pytest.raises(ValueError):
        access.apply_action([], "aprobar", "hola")
    with pytest.raises(ValueError):
        access.apply_action([], "aprobar", "ABCD-EFGH")   # nuevo sin llave pública
    bad = "RADAR1.." + base64.urlsafe_b64encode(b"\x04" + b"\x01" * 64).decode().rstrip("=")
    with pytest.raises(ValueError):
        access.apply_action([], "aprobar", bad)          # punto que no está en la curva


def test_unblock_and_delete():
    a = Device()
    devs, _ = access.apply_action([], "aprobar", a.request)
    devs, _ = access.apply_action(devs, "bloquear", a.id.lower())
    assert devs[0]["status"] == "blocked"
    devs, _ = access.apply_action(devs, "desbloquear", a.id)
    assert devs[0]["status"] == "ok"
    devs, _ = access.apply_action(devs, "eliminar", a.id)
    assert devs == []
