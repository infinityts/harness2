"""E2E Etapa 12: activación online, lease firmado, revocación y gracia offline."""
import asyncio
import json
import os
import urllib.error
import urllib.request

import sqlalchemy as sa
from sqlalchemy import create_engine

BASE = os.environ.get("HARNESS_BASE_URL", "http://127.0.0.1:8443")
ACTIVATION_URL = os.environ.get("HARNESS_ACTIVATION_URL", "http://harness-license-server:8450").rstrip("/")
ACTIVATION_TOKEN = os.environ.get("HARNESS_ACTIVATION_TOKEN", "")
ADMIN_TOKEN = os.environ.get("HARNESS_ACTIVATION_ADMIN_TOKEN", "")
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_EMAIL = os.environ.get("HARNESS_E2E_ADMIN_EMAIL", "admin@local")
ADMIN_PASSWORD = os.environ.get("HARNESS_E2E_ADMIN_PASSWORD", "admin")
LICENSE_ID = "prod-enterprise-10-2026"
INSTALLATION_ID = os.environ.get("HARNESS_INSTALLATION_ID", "")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def http(method, path, body=None, bearer=None, headers=None, timeout=60):
    hdrs = {"Content-Type": "application/json"}
    if bearer:
        hdrs["Authorization"] = f"Bearer {bearer}"
    if headers:
        hdrs.update(headers)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=hdrs)
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


async def api(method, path, body=None, bearer=None, headers=None, timeout=60):
    return await asyncio.to_thread(http, method, path, body, bearer, headers, timeout)


def server_http(method, path, body=None, headers=None, timeout=30):
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(ACTIVATION_URL + path, data=data, method=method, headers=hdrs)
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


def db_exec(sql, **params):
    with engine.begin() as conn:
        conn.execute(sa.text(sql), params)


def db_rows(sql, **params):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql), params).mappings().all()


def activation_state():
    return db_rows("""
        SELECT status, lease_expires_at, lease_grace_until, last_error, lease_payload
        FROM license_activation_state WHERE id = 1
    """)[0]


def server_activate(installation=INSTALLATION_ID):
    return server_http(
        "POST", "/v1/activate",
        {"license_id": LICENSE_ID, "installation_id": installation},
        {"X-Harness-Activation-Token": ACTIVATION_TOKEN},
    )


def server_admin(path, body):
    return server_http(
        "POST", path, body,
        {"X-Activation-Admin": ADMIN_TOKEN},
    )


async def main():
    print("== ETAPA 12 - ACTIVACION ONLINE ==")
    admin = None
    try:
        st, login = await api("POST", "/api/v1/auth/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert st == 200, login
        admin = login["access_token"]

        print("\n--- 1. Activacion inicial ---")
        st, lic = await api("GET", "/api/v1/admin/license", bearer=admin)
        assert st == 200, lic
        print("license:", lic["signed_license"]["status"], "activation:", lic["activation"]["status"])
        assert lic["signed_license"]["status"] == "VALID"
        assert lic["activation"]["status"] == "ACTIVE"
        assert lic["activation_operational"] is True
        assert lic["activation"]["lease_id"]
        assert lic["activation"]["lease_expires_at"]
        assert lic["audit_chain"]["valid"] is True
        st, health = await asyncio.to_thread(server_http, "GET", "/health")
        print("server health:", st, health)
        assert st == 200 and health["status"] == "ok"

        print("\n--- 2. Binding de instalacion en el servidor ---")
        st, body = await asyncio.to_thread(server_activate, "00000000-0000-0000-0000-000000000000")
        print("wrong installation:", st, body)
        assert st == 403 and body["detail"]["code"] == "LICENSE_NOT_REGISTERED"

        print("\n--- 3. Revocacion vendor-side ---")
        st, body = await asyncio.to_thread(server_admin, "/v1/revoke", {
            "license_id": LICENSE_ID, "installation_id": INSTALLATION_ID,
        })
        print("revoke server:", st, body)
        assert st == 200 and body["status"] == "REVOKED"
        st, body = await api("POST", "/api/v1/admin/license/activate", bearer=admin)
        print("client revoked:", st, body.get("activation", {}).get("status"))
        assert st == 200 and body["activation"]["status"] == "REVOKED"
        assert body["license"]["activation_operational"] is False
        st, body = await api("POST", "/api/v1/projects", {
            "project_slug": "stage12-revoked", "display_name": "Revoked",
            "description": "must fail", "default_branch": "main",
        }, bearer=admin)
        print("create project revoked:", st, body)
        assert st == 503 and body["code"] == "LICENSE_ACTIVATION_REQUIRED"

        st, body = await asyncio.to_thread(server_admin, "/v1/restore", {
            "license_id": LICENSE_ID, "installation_id": INSTALLATION_ID,
        })
        assert st == 200
        st, body = await api("POST", "/api/v1/admin/license/activate", bearer=admin)
        print("client restored:", st, body.get("activation", {}).get("status"))
        assert st == 200 and body["activation"]["status"] == "ACTIVE"
        assert body["license"]["activation_operational"] is True

        print("\n--- 4. Gracia offline ---")
        st, body = await asyncio.to_thread(server_admin, "/v1/maintenance", {"enabled": True})
        assert st == 200 and body["maintenance"] is True
        db_exec("""
            UPDATE license_activation_state
            SET status = 'ACTIVE',
                lease_expires_at = NOW() - interval '1 day',
                lease_grace_until = NOW() + interval '6 days',
                last_error = NULL
            WHERE id = 1
        """)
        st, body = await api("POST", "/api/v1/admin/license/activate", bearer=admin, timeout=60)
        print("offline grace:", st, body.get("activation", {}).get("status"))
        assert st == 200 and body["activation"]["status"] == "OFFLINE_GRACE"
        assert body["license"]["activation_operational"] is True

        print("\n--- 5. Gracia agotada -> EXPIRED ---")
        db_exec("""
            UPDATE license_activation_state
            SET status = 'OFFLINE_GRACE',
                lease_expires_at = NOW() - interval '10 days',
                lease_grace_until = NOW() - interval '3 days'
            WHERE id = 1
        """)
        st, body = await api("POST", "/api/v1/admin/license/activate", bearer=admin)
        print("expired:", st, body.get("activation", {}).get("status"))
        assert st == 200 and body["activation"]["status"] == "EXPIRED"
        assert body["license"]["activation_operational"] is False
        st, body = await api("POST", "/api/v1/projects", {
            "project_slug": "stage12-expired", "display_name": "Expired",
            "description": "must fail", "default_branch": "main",
        }, bearer=admin)
        assert st == 503 and body["code"] == "LICENSE_ACTIVATION_REQUIRED"

        print("\n--- 6. Lease almacenado manipulado ---")
        db_exec("""
            UPDATE license_activation_state
            SET status = 'ACTIVE',
                lease_expires_at = NOW() + interval '5 days',
                lease_grace_until = NOW() + interval '12 days',
                lease_signature = 'tampered-signature'
            WHERE id = 1
        """)
        st, body = await api("POST", "/api/v1/admin/license/activate", bearer=admin)
        print("tampered stored lease:", st, body.get("activation", {}).get("status"), body.get("activation", {}).get("last_error"))
        assert st == 200 and body["activation"]["status"] == "ERROR"
        assert body["license"]["activation_operational"] is False

        print("\n--- 7. Restauracion final ---")
        st, body = await asyncio.to_thread(server_admin, "/v1/maintenance", {"enabled": False})
        assert st == 200 and body["maintenance"] is False
        st, body = await asyncio.to_thread(server_admin, "/v1/restore", {
            "license_id": LICENSE_ID, "installation_id": INSTALLATION_ID,
        })
        assert st == 200
        st, body = await api("POST", "/api/v1/admin/license/activate", bearer=admin)
        print("final activation:", st, body.get("activation", {}).get("status"))
        assert st == 200 and body["activation"]["status"] == "ACTIVE"
        assert body["license"]["activation_operational"] is True

        st, audit = await api("GET", "/api/v1/admin/license/audit?limit=200", bearer=admin)
        assert st == 200 and audit["chain"]["valid"] is True
        event_types = {e["event_type"] for e in audit["events"]}
        print("audit events:", sorted(event_types))
        assert {"LICENSE_ACTIVATED", "LICENSE_LEASE_RENEWED", "LICENSE_REVOKED",
                "LICENSE_OFFLINE_GRACE", "LICENSE_ACTIVATION_EXPIRED",
                "LICENSE_LEASE_INVALID"}.issubset(event_types)

        print("\n--- 8. UI de activacion ---")
        st, ui = await api("GET", "/portal")
        assert st == 200 and isinstance(ui, str)
        for marker in ("licenseActivationStatus", "licenseActivate", "Activación"):
            assert marker.lower() in ui.lower(), f"UI sin marcador: {marker}"
        print("portal:", len(ui), "bytes")

        print("\nE2E STAGE 12: OK")
    finally:
        try:
            server_admin("/v1/maintenance", {"enabled": False})
            server_admin("/v1/restore", {"license_id": LICENSE_ID, "installation_id": INSTALLATION_ID})
        except Exception:
            pass
        if admin:
            try:
                await api("POST", "/api/v1/admin/license/activate", bearer=admin)
            except Exception:
                pass


try:
    asyncio.run(main())
finally:
    pass
