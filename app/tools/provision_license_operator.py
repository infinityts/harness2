"""Provisiona la cuenta sellada LICENSE_ADMIN de Harness2.

Uso (dentro del contenedor harness-app):
    python tools/provision_license_operator.py
    python tools/provision_license_operator.py --rotate

La salida contiene solo la password en claro una sola vez. El MFA se configura
obligatoriamente en el primer login desde el portal; el script no imprime ni
pre-genera el secreto TOTP.
"""
import argparse
import json
import os
import secrets
import string
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa
from sqlalchemy import create_engine

from src.security import hash_password

DEFAULT_EMAIL = os.getenv("HARNESS_LICENSE_OPERATOR_EMAIL", "license-operator@harness.local")
DEFAULT_NAME = "License Operator"
PASSWORD_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*()-_=+[]{}"
PASSWORD_LENGTH = 40


def generate_password(length: int = PASSWORD_LENGTH) -> str:
    while True:
        candidate = "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))
        if (
            any(ch.islower() for ch in candidate)
            and any(ch.isupper() for ch in candidate)
            and any(ch.isdigit() for ch in candidate)
            and any(ch in "!@#$%^&*()-_=+[]{}" for ch in candidate)
        ):
            return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or rotate the sealed LICENSE_ADMIN account")
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--full-name", default=DEFAULT_NAME)
    parser.add_argument("--rotate", action="store_true", help="Rotate password and reset MFA enrollment for an existing account")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2

    email = args.email.strip().lower()
    engine = create_engine(database_url, pool_pre_ping=True)

    with engine.begin() as conn:
        existing = conn.execute(
            sa.text("SELECT id::text, email, role FROM harness_users WHERE email = :email FOR UPDATE"),
            {"email": email},
        ).mappings().first()
        if existing and existing["role"] != "LICENSE_ADMIN":
            print(f"{email} exists with role {existing['role']}; refusing to overwrite", file=sys.stderr)
            return 3
        other = conn.execute(
            sa.text("""
                SELECT count(*) FROM harness_users
                WHERE role = 'LICENSE_ADMIN' AND email <> :email
            """),
            {"email": email},
        ).scalar_one()
        if int(other or 0) > 0:
            print("Another LICENSE_ADMIN already exists; refusing to create a second one", file=sys.stderr)
            return 4
        if existing and not args.rotate:
            print(f"{email} already exists; use --rotate to rotate credentials", file=sys.stderr)
            return 5

        password = generate_password()
        password_hash = hash_password(password)

        if existing:
            conn.execute(
                sa.text("""
                    UPDATE harness_users
                    SET password_hash = :password_hash,
                        full_name = :full_name,
                        role = 'LICENSE_ADMIN',
                        status = 'ACTIVE',
                        mfa_enabled = false,
                        mfa_totp_secret_vault = NULL,
                        password_change_allowed = false,
                        mfa_last_used_step = NULL,
                        failed_login_attempts = 0,
                        locked_until = NULL
                    WHERE id = CAST(:uid AS UUID)
                """),
                {
                    "password_hash": password_hash,
                    "full_name": args.full_name,
                    "uid": existing["id"],
                },
            )
            user_id = existing["id"]
            action = "rotated"
        else:
            user_id = str(uuid.uuid4())
            conn.execute(
                sa.text("""
                    INSERT INTO harness_users
                        (id, email, full_name, password_hash, role, status, default_skills,
                         mfa_enabled, mfa_totp_secret_vault, password_change_allowed)
                    VALUES
                        (CAST(:id AS UUID), :email, :full_name, :password_hash,
                         'LICENSE_ADMIN', 'ACTIVE', CAST('[]' AS JSONB),
                         false, NULL, false)
                """),
                {
                    "id": user_id,
                    "email": email,
                    "full_name": args.full_name,
                    "password_hash": password_hash,
                },
            )
            action = "created"

    print(json.dumps({
        "action": action,
        "user_id": user_id,
        "email": email,
        "full_name": args.full_name,
        "role": "LICENSE_ADMIN",
        "password": password,
        "password_change_allowed": False,
        "mfa_required": True,
        "mfa_enrollment_required": True,
        "next_step": "Inicia sesion en el portal con esta password y configura MFA cuando se solicite.",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
