import hashlib
import hmac
import os

PBKDF2_ITERATIONS = int(os.getenv("PBKDF2_ITERATIONS", "260000"))
PBKDF2_ALGORITHM = "sha256"


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
