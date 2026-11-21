"""Completa cadenas TLS incompletas (servidor que no envía el certificado intermedio).

No desactiva la verificación: descarga el intermedio desde la URL "CA Issuers" (AIA)
que trae el propio certificado del sitio y verifica la cadena completa contra certifi.
"""
from __future__ import annotations

import socket
import ssl

import certifi
import httpx
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID


def _leaf_der(host: str, port: int = 443) -> bytes:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # solo para LEER el certificado hoja; no se envían datos
    with socket.create_connection((host, port), timeout=15) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            return tls.getpeercert(binary_form=True)


def _issuer_urls(cert: x509.Certificate) -> list[str]:
    try:
        aia = cert.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
    except x509.ExtensionNotFound:
        return []
    return [d.access_location.value for d in aia
            if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS]


def intermediates_pem(host: str, max_depth: int = 3) -> str:
    cert = x509.load_der_x509_certificate(_leaf_der(host))
    pems: list[str] = []
    for _ in range(max_depth):
        urls = _issuer_urls(cert)
        if not urls:
            break
        raw = httpx.get(urls[0], timeout=15, follow_redirects=True).content
        try:
            cert = x509.load_der_x509_certificate(raw)
        except ValueError:
            cert = x509.load_pem_x509_certificate(raw)
        if cert.issuer == cert.subject:  # raíz: ya está en certifi
            break
        pems.append(cert.public_bytes(Encoding.PEM).decode())
    return "".join(pems)


def ssl_context_for(host: str) -> ssl.SSLContext:
    """Contexto con verificación COMPLETA: certifi + intermedios obtenidos por AIA."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    extra = intermediates_pem(host)
    if extra:
        ctx.load_verify_locations(cadata=extra)
    return ctx
