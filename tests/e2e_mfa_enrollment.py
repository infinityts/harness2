"""E2E MFA enrollment del primer login de LICENSE_ADMIN."""
import asyncio
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request
import uuid

import sqlalchemy as sa
from sqlalchemy import create_engine

for _candidate in ("/app", str(pathlib.Path(__file__).resolve().parents[1] / "app")):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)

from src import totp
from src.security import hash_password

BASE = os.environ.get("HARNESS_BASE_URL", "http://127.0.0.1:8443")
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_EMAIL = os.environ.get("HARNESS_E2E_ADMIN_EMAIL", "admin@local")
ADMIN_PASSWORD = os.environ.get("HARNESS_E2E_ADMIN_PASSWORD", "admin")
EMAIL = "mfa-enroll-e2e@example.com"
PASSWORD = "MfaEnrollE2E2026!"
USER_ID = str(uuid.uuid4())

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def http(method, path, body=None, bearer=None, timeout=60):
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, raw


async def api(method, path, body=None, bearer=None, timeout=60):
    return await asyncio.to_thread(http, method, path, body, bearer, timeout)


def http_text(path, timeout=60):
    req = urllib.request.Request(BASE + path)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode()


def db_rows(sql, **params):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql), params).mappings().all()


def db_exec(sql, **params):
    with engine.begin() as conn:
        conn.execute(sa.text(sql), params)


def cleanup():
    db_exec("DELETE FROM harness_users WHERE email = :email", email=EMAIL)


def wrong_code(secret):
    value = totp.code(secret)
    return ("0" if value[0] != "0" else "1") + value[1:]


async def main():
    print("== MFA ENROLLMENT - PRIMER LOGIN LICENSE_ADMIN ==")
    try:
        cleanup()
        st, admin = await api("POST", "/api/v1/auth/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert st == 200 and not admin.get("mfa_enrollment_required"), admin
        print("admin normal sin MFA:", st)

        db_exec("""
            INSERT INTO harness_users
                (id, email, full_name, password_hash, role, status, default_skills,
                 mfa_enabled, mfa_totp_secret_vault, password_change_allowed)
            VALUES
                (CAST(:id AS UUID), :email, 'MFA Enroll E2E', :ph,
                 'LICENSE_ADMIN', 'ACTIVE', CAST('[]' AS JSONB), false, NULL, false)
        """, id=USER_ID, email=EMAIL, ph=hash_password(PASSWORD))

        print("\n--- 1. Primer login: enrollment ---")
        st, first = await api("POST", "/api/v1/auth/login", {"email": EMAIL, "password": PASSWORD})
        print("primer login:", st, first.get("mfa_enrollment_required") if isinstance(first, dict) else first)
        assert st == 200 and first["mfa_enrollment_required"] is True
        assert first.get("enrollment_token")
        assert first.get("secret") and first.get("otpauth_uri")
        secret = first["secret"]
        row = db_rows("""
            SELECT mfa_enabled, mfa_totp_secret_vault
            FROM harness_users WHERE email = :email
        """, email=EMAIL)[0]
        assert row["mfa_enabled"] is False and row["mfa_totp_secret_vault"]

        print("\n--- 2. Confirmar con codigo incorrecto ---")
        st, bad = await api("POST", "/api/v1/auth/mfa/enroll/confirm", {
            "enrollment_token": first["enrollment_token"],
            "totp_code": wrong_code(secret),
        })
        print("codigo incorrecto:", st, bad)
        assert st == 401 and bad["detail"]["code"] == "MFA_INVALID"

        print("\n--- 3. Confirmar enrollment ---")
        st, enrolled = await api("POST", "/api/v1/auth/mfa/enroll/confirm", {
            "enrollment_token": first["enrollment_token"],
            "totp_code": totp.code(secret),
        })
        print("confirm:", st, enrolled.get("user", {}).get("role") if isinstance(enrolled, dict) else enrolled)
        assert st == 200 and enrolled["user"]["role"] == "LICENSE_ADMIN"
        assert enrolled["user"]["mfa_enabled"] is True
        row = db_rows("""
            SELECT mfa_enabled, mfa_last_used_step
            FROM harness_users WHERE email = :email
        """, email=EMAIL)[0]
        assert row["mfa_enabled"] is True and row["mfa_last_used_step"] is not None

        print("\n--- 4. Login posterior exige MFA ---")
        st, required = await api("POST", "/api/v1/auth/login", {"email": EMAIL, "password": PASSWORD})
        print("sin codigo:", st, required)
        assert st == 401 and required["detail"]["code"] == "MFA_REQUIRED"
        db_exec("UPDATE harness_users SET mfa_last_used_step = NULL WHERE email = :email", email=EMAIL)
        st, second = await api("POST", "/api/v1/auth/login", {
            "email": EMAIL, "password": PASSWORD, "totp_code": totp.code(secret),
        })
        print("con codigo:", st, second.get("user", {}).get("role") if isinstance(second, dict) else second)
        assert st == 200 and second["user"]["role"] == "LICENSE_ADMIN"

        print("\n--- 5. UI enrollment ---")
        st, ui = await asyncio.to_thread(http_text, "/portal")
        assert st == 200
        for marker in ("mfaEnrollPane", "Configura MFA", "enrollment_token"):
            assert marker.lower() in ui.lower(), marker

        print("\nE2E MFA ENROLLMENT: OK")
    finally:
        try:
            cleanup()
        except Exception as exc:
            print("cleanup:", exc)


if __name__ == "__main__":
    asyncio.run(main())
