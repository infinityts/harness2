"""E2E Etapa 13: LICENSE_ADMIN sellado, MFA TOTP y separacion de funciones."""
import asyncio
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

import sqlalchemy as sa
from sqlalchemy import create_engine

BASE = os.environ.get("HARNESS_BASE_URL", "http://127.0.0.1:8443")
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_EMAIL = os.environ.get("HARNESS_E2E_ADMIN_EMAIL", "admin@local")
ADMIN_PASSWORD = os.environ.get("HARNESS_E2E_ADMIN_PASSWORD", "admin")
LICENSE_EMAIL = os.environ.get("HARNESS_E2E_LICENSE_OPERATOR_EMAIL", "license-operator@harness.local")
LICENSE_PASSWORD = os.environ.get("HARNESS_E2E_LICENSE_OPERATOR_PASSWORD", "")
LICENSE_TOTP_SECRET = os.environ.get("HARNESS_E2E_LICENSE_OPERATOR_TOTP_SECRET", "")

for _candidate in ("/app", str(pathlib.Path(__file__).resolve().parents[1] / "app")):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)
from src import totp as totp_lib

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
            if not raw:
                return resp.status, None
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, raw


async def api(method, path, body=None, bearer=None, timeout=60):
    return await asyncio.to_thread(http, method, path, body, bearer, timeout)


def db_rows(sql, **params):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql), params).mappings().all()


def db_exec(sql, **params):
    with engine.begin() as conn:
        conn.execute(sa.text(sql), params)


def operator_row():
    rows = db_rows("""
        SELECT id::text, email, role, status, password_change_allowed, mfa_enabled,
               mfa_totp_secret_vault, mfa_last_used_step, failed_login_attempts, locked_until
        FROM harness_users WHERE email = :email
    """, email=LICENSE_EMAIL)
    assert rows, f"license operator not found: {LICENSE_EMAIL}"
    return rows[0]


def reset_operator():
    db_exec("""
        UPDATE harness_users
        SET mfa_last_used_step = NULL, failed_login_attempts = 0, locked_until = NULL
        WHERE email = :email
    """, email=LICENSE_EMAIL)


def current_totp():
    return totp_lib.code(LICENSE_TOTP_SECRET)


def wrong_totp():
    value = current_totp()
    return ("0" if value[0] != "0" else "1") + value[1:]


async def login_license(code=None, password=None):
    body = {"email": LICENSE_EMAIL, "password": password or LICENSE_PASSWORD}
    if code is not None:
        body["totp_code"] = code
    return await api("POST", "/api/v1/auth/login", body)


def require_credentials():
    assert LICENSE_PASSWORD, "HARNESS_E2E_LICENSE_OPERATOR_PASSWORD is required"
    assert LICENSE_TOTP_SECRET, "HARNESS_E2E_LICENSE_OPERATOR_TOTP_SECRET is required"


async def main():
    print("== ETAPA 13 - LICENSE_ADMIN CON MFA ==")
    require_credentials()
    admin = None
    license_token = None
    try:
        st, login = await api("POST", "/api/v1/auth/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert st == 200, login
        admin = login["access_token"]

        print("\n--- 1. Migracion y cuenta sellada ---")
        version = db_rows("SELECT version_num FROM alembic_version")[0]["version_num"]
        assert version == "0015", version
        operator = operator_row()
        print("operator:", operator["email"], operator["role"], "mfa:", operator["mfa_enabled"])
        assert operator["role"] == "LICENSE_ADMIN"
        assert operator["status"] == "ACTIVE"
        assert operator["mfa_enabled"] is True
        assert operator["password_change_allowed"] is False
        assert operator["mfa_totp_secret_vault"]

        print("\n--- 2. Login sin TOTP y TOTP invalido ---")
        reset_operator()
        st, body = await login_license()
        print("sin TOTP:", st, body)
        assert st == 401 and body["detail"]["code"] == "MFA_REQUIRED"
        st, body = await login_license(wrong_totp())
        print("TOTP invalido:", st, body)
        assert st == 401 and body["detail"]["code"] == "MFA_INVALID"
        assert operator_row()["failed_login_attempts"] >= 1

        print("\n--- 3. Login MFA valido y anti-replay ---")
        reset_operator()
        code = current_totp()
        st, body = await login_license(code)
        print("login MFA:", st, body.get("user", {}).get("role"))
        assert st == 200 and body["user"]["role"] == "LICENSE_ADMIN"
        license_token = body["access_token"]
        assert operator_row()["mfa_last_used_step"] is not None
        st, body = await login_license(code)
        print("replay:", st, body)
        assert st == 401 and body["detail"]["code"] == "MFA_REPLAY"

        reset_operator()
        st, body = await login_license(current_totp())
        assert st == 200, body
        license_token = body["access_token"]

        print("\n--- 4. ADMIN normal: solo lectura ---")
        st, body = await api("PUT", "/api/v1/admin/license", {"plan_code": "ENTERPRISE_10"}, bearer=admin)
        print("admin PUT license:", st, body)
        assert st == 403 and body["detail"]["code"] == "LICENSE_ADMIN_REQUIRED"
        st, body = await api("POST", "/api/v1/admin/license/activate", bearer=admin)
        print("admin activate:", st, body)
        assert st == 403 and body["detail"]["code"] == "LICENSE_ADMIN_REQUIRED"
        st, body = await api("POST", "/api/v1/admin/license/reload", bearer=admin)
        print("admin reload:", st, body)
        assert st == 403 and body["detail"]["code"] == "LICENSE_ADMIN_REQUIRED"
        st, body = await api("GET", "/api/v1/admin/license", bearer=admin)
        assert st == 200 and body["signed_license"]["status"] == "VALID"
        st, body = await api("GET", "/api/v1/admin/license/audit?limit=50", bearer=admin)
        assert st == 200 and body["chain"]["valid"] is True

        print("\n--- 5. LICENSE_ADMIN: solo licencia ---")
        st, body = await api("GET", "/api/v1/admin/license", bearer=license_token)
        assert st == 200
        st, body = await api("GET", "/api/v1/admin/license/audit?limit=50", bearer=license_token)
        assert st == 200
        st, body = await api("POST", "/api/v1/projects", {
            "project_slug": "stage13-forbidden", "display_name": "Forbidden",
            "description": "license operator must not create projects", "default_branch": "main",
        }, bearer=license_token)
        print("license admin create project:", st, body)
        assert st == 403 and "Admin role required" in json.dumps(body)
        st, body = await api("GET", "/api/v1/admin/users", bearer=license_token)
        print("license admin list users:", st, body)
        assert st == 403 and "Admin role required" in json.dumps(body)

        print("\n--- 6. Password sellada ---")
        st, body = await api("PUT", "/api/v1/auth/password", {
            "current_password": LICENSE_PASSWORD,
            "new_password": "Stage13NewPass2026!",
        }, bearer=license_token)
        print("cambio propio:", st, body)
        assert st == 403 and body["detail"]["code"] == "PASSWORD_SEALED"
        st, body = await api("PUT", f"/api/v1/admin/users/{operator['id']}/reset-password", {
            "new_password": "Stage13Reset2026!",
        }, bearer=admin)
        print("reset admin:", st, body)
        assert st == 403 and body["detail"]["code"] == "PASSWORD_SEALED"
        st, users = await api("GET", "/api/v1/admin/users", bearer=admin)
        assert st == 200
        assert LICENSE_EMAIL not in {u["email"] for u in users["users"]}

        print("\n--- 7. Bloqueo por intentos ---")
        reset_operator()
        for index in range(5):
            st, body = await login_license(wrong_totp())
            print(f"intento {index + 1}:", st, (body or {}).get("detail", {}).get("code"))
            assert st == 401 and body["detail"]["code"] == "MFA_INVALID"
        st, body = await login_license(current_totp())
        print("cuenta bloqueada:", st, body)
        assert st == 423 and body["detail"]["code"] == "ACCOUNT_LOCKED"
        reset_operator()
        st, body = await login_license(current_totp())
        assert st == 200, body
        license_token = body["access_token"]

        print("\n--- 8. Auditoria actor/IP ---")
        rows = db_rows("""
            SELECT payload::text AS payload FROM license_audit_events
            WHERE event_type = 'LICENSE_ADMIN_LOGIN'
            ORDER BY id DESC LIMIT 1
        """)
        assert rows
        payload = json.loads(rows[0]["payload"])
        print("login audit:", payload.get("actor"), payload.get("data", {}).get("context"))
        assert payload.get("actor") == LICENSE_EMAIL
        assert payload.get("data", {}).get("context", {}).get("actor_ip")

        rows = db_rows("""
            SELECT count(*) AS total FROM license_audit_events
            WHERE event_type IN ('LICENSE_ADMIN_LOGIN_FAILED', 'LICENSE_ADMIN_LOGIN_LOCKED',
                                 'LICENSE_ADMIN_PASSWORD_RESET_BLOCKED')
        """)
        assert int(rows[0]["total"]) >= 2
        st, audit = await api("GET", "/api/v1/admin/license/audit?limit=200", bearer=admin)
        assert st == 200 and audit["chain"]["valid"] is True

        print("\n--- 9. UI de gobernanza ---")
        st, ui = await api("GET", "/portal")
        assert st == 200 and isinstance(ui, str)
        for marker in ("loginTotp", "licenseGovernanceNote", "LICENSE_ADMIN", "Código MFA"):
            assert marker.lower() in ui.lower(), f"UI sin marcador: {marker}"

        print("\nE2E STAGE 13: OK")
    finally:
        try:
            reset_operator()
        except Exception as exc:
            print("no se pudo resetear el operador:", exc)


if __name__ == "__main__":
    asyncio.run(main())
