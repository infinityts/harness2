import base64
import hashlib
import hmac
import os

PBKDF2_ITERATIONS = int(os.getenv("PBKDF2_ITERATIONS", "260000"))
PBKDF2_ALGORITHM = "sha256"
VAULT_SECRET = os.getenv("HARNESS_VAULT_SECRET") or os.getenv("HARNESS_JWT_SECRET", "change-me-secret")


def hash_password(password: str, salt: bytes = None, iterations: int = None) -> str:
    """PBKDF2-HMAC-SHA256 con salt aleatoria. Formato: pbkdf2_sha256$iter$salt$hash."""
    if salt is None:
        salt = os.urandom(16)
    rounds = iterations or PBKDF2_ITERATIONS
    digest = hashlib.pbkdf2_hmac(PBKDF2_ALGORITHM, password.encode("utf-8"), salt, rounds)
    return f"pbkdf2_{PBKDF2_ALGORITHM}${rounds}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds, salt_hex, digest_hex = stored.split("$")
        if scheme != f"pbkdf2_{PBKDF2_ALGORITHM}":
            return False
        digest = hashlib.pbkdf2_hmac(
            PBKDF2_ALGORITHM,
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(rounds),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def _vault_key() -> bytes:
    return hashlib.sha256(VAULT_SECRET.encode("utf-8")).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = b""
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(key + nonce + counter.to_bytes(8, "big")).digest()
        counter += 1
    return out[:length]


def seal_secret(plaintext: str) -> str:
    """Cifra un secreto corto con SHA-256-CTR + HMAC-SHA256 (stdlib)."""
    data = plaintext.encode("utf-8")
    nonce = os.urandom(16)
    key = _vault_key()
    stream = _keystream(key, nonce, len(data))
    ciphertext = bytes(a ^ b for a, b in zip(data, stream))
    tag = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + ciphertext + tag).decode("ascii")


def open_secret(sealed: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(sealed.encode("ascii"))
        nonce, ciphertext, tag = raw[:16], raw[16:-32], raw[-32:]
        key = _vault_key()
        expected = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            return ""
        stream = _keystream(key, nonce, len(ciphertext))
        return bytes(a ^ b for a, b in zip(ciphertext, stream)).decode("utf-8")
    except Exception:
        return ""
