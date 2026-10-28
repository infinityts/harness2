"""TOTP RFC 6238 con stdlib (HMAC-SHA1, 30s, 6 dígitos)."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def generate_secret(length: int = 32) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def normalize_secret(secret: str) -> str:
    return str(secret or "").replace(" ", "").replace("=", "").strip().upper()


def _decode_secret(secret: str) -> bytes:
    value = normalize_secret(secret)
    padding = "=" * ((8 - len(value) % 8) % 8)
    return base64.b32decode(value + padding, casefold=True)


def code(secret: str, timestamp: int = None, step: int = 30, digits: int = 6) -> str:
    counter = int((timestamp if timestamp is not None else time.time()) // step)
    digest = hmac.new(_decode_secret(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10 ** digits)).zfill(digits)


def matching_step(secret: str, value: str, window: int = 1, step: int = 30, digits: int = 6):
    """Devuelve el contador TOTP que coincide, o None.

    El contador permite aplicar anti-replay: un mismo codigo no puede usarse
    dos veces aunque siga dentro de la ventana de 30 segundos.
    """
    candidate = str(value or "").strip()
    if not candidate.isdigit() or len(candidate) != digits:
        return None
    now = int(time.time())
    for offset in range(-window, window + 1):
        counter = int(now // step) + offset
        if hmac.compare_digest(code(secret, counter * step, step, digits), candidate):
            return counter
    return None


def verify(secret: str, value: str, window: int = 1, step: int = 30, digits: int = 6) -> bool:
    return matching_step(secret, value, window=window, step=step, digits=digits) is not None


def provisioning_uri(secret: str, email: str, issuer: str = "Harness2") -> str:
    label = urllib.parse.quote(f"{issuer}:{email}")
    params = urllib.parse.urlencode({
        "secret": normalize_secret(secret),
        "issuer": issuer,
        "algorithm": "SHA1",
        "digits": "6",
        "period": "30",
    })
    return f"otpauth://totp/{label}?{params}"
