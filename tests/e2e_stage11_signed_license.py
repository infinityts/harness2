"""E2E Etapa 11: licencia firmada Ed25519, binding, degradacion y auditoria."""
import asyncio
import json
import os
import pathlib
import shutil
import urllib.error
import urllib.request

import sqlalchemy as sa
from sqlalchemy import create_engine

BASE = os.environ.get("HARNESS_BASE_URL", "http://127.0.0.1:8443")
DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_EMAIL = os.environ.get("HARNESS_E2E_ADMIN_EMAIL", "admin@local")
ADMIN_PASSWORD = os.environ.get("HARNESS_E2E_ADMIN_PASSWORD", "admin")
LICENSE_DIR = pathlib.Path("/var/lib/harness/license")
TEST_DIR = pathlib.Path("/var/lib/harness/license-test")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def http(method, path, body=None, bearer=None, timeout=120):
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


async def api(method, path, body=None, bearer=None, timeout=120):
    return await asyncio.to_thread(http, method, path, body, bearer, timeout)


def db_rows(sql, **params):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql), params).mappings().all()


def db_exec(sql, **params):
    with engine.begin() as conn:
        conn.execute(sa.text(sql), params)


def install_license(name: str) -> None:
    source = TEST_DIR / name
    if not source.exists():
        raise AssertionError(f"test license not found: {source}")
    shutil.copyfile(source, LICENSE_DIR / "license.json")


async def reload_license(admin: str):
    return await api("POST", "/api/v1/admin/license/reload", bearer=admin)


def assert_degraded(st, body, expected_status=None):
    assert st == 503, (st, body)
    assert isinstance(body, dict) and body.get("code") == "LICENSE_DEGRADED", body
    if expected_status:
        assert expected_status in body.get("detail", ""), body


async def main():
    print("== ETAPA 11 - LICENCIA FIRMADA ==")
    admin = None
    original = None
    try:
        st, login = await api("POST", "/api/v1/auth/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert st == 200, login
        admin = login["access_token"]
        st, original = await api("GET", "/api/v1/admin/license", bearer=admin)
        assert st == 200

        print("\n--- 1. Licencia de produccion valida ---")
        signed = original["signed_license"]
        print("license:", signed["status"], signed["license_id"], "installation:", signed["installation_id"])
        assert signed["status"] == "VALID"
        assert signed["license_id"] == "prod-enterprise-10-2026"
        assert signed["allowed_plans"] == ["ENTERPRISE_10"]
        assert original["license_operational"] is True
        assert original["audit_chain"]["valid"] is True
        installation = db_rows("SELECT installation_id::text AS installation_id FROM system_installation WHERE id = 1")[0]["installation_id"]
        assert str(signed["installation_id"]) == str(installation)

        print("\n--- 2. Un Admin no puede elevar el plan firmado ---")
        st, denied = await api("PUT", "/api/v1/admin/license", {"plan_code": "PERSONAL_1"}, bearer=admin)
        print("plan no incluido:", st, denied)
        assert st == 403 and denied["code"] == "LICENSE_PLAN_NOT_ENTITLED"
        st, denied = await api("PUT", "/api/v1/admin/license", {
            "plan_code": "ENTERPRISE_10", "max_active_projects": 999,
        }, bearer=admin)
        print("cuota por encima del techo:", st, denied)
        assert st == 403 and denied["code"] == "LICENSE_ENTITLEMENT_EXCEEDED"

        print("\n--- 3. Licencia de pruebas multi-plan ---")
        install_license("test-all-plans.json")
        st, data = await reload_license(admin)
        print("reload test-all-plans:", st, data.get("signed_license", {}).get("status"))
        assert st == 200 and data["signed_license"]["status"] == "VALID"
        assert set(data["signed_license"]["allowed_plans"]) == {
            "PERSONAL_1", "TEAM_3", "BUSINESS_5", "ENTERPRISE_10", "MAX_20"
        }
        for plan in ("PERSONAL_1", "TEAM_3", "MAX_20"):
            st, data = await api("PUT", "/api/v1/admin/license", {"plan_code": plan}, bearer=admin)
            print("plan", plan, st, data.get("limits"))
            assert st == 200 and data["plan_code"] == plan
        st, denied = await api("PUT", "/api/v1/admin/license", {
            "plan_code": "MAX_20", "max_engineers_per_project": 21,
        }, bearer=admin)
        print("cuota custom fuera de techo:", st, denied)
        assert st == 403 and denied["code"] == "LICENSE_ENTITLEMENT_EXCEEDED"

        print("\n--- 4. Firma manipulada -> INVALID + degradado ---")
        install_license("test-tampered.json")
        st, data = await reload_license(admin)
        print("tampered:", st, data.get("signed_license", {}).get("status"), data.get("signed_license", {}).get("error_message"))
        assert st == 200 and data["signed_license"]["status"] == "INVALID"
        assert data["license_operational"] is False
        st, body = await api("POST", "/api/v1/projects", {
            "project_slug": "stage11-tamper", "display_name": "Tamper",
            "description": "should fail", "default_branch": "main",
        }, bearer=admin)
        assert_degraded(st, body, "INVALID")
        st, body = await api("PUT", "/api/v1/admin/license", {"plan_code": "TEAM_3"}, bearer=admin)
        assert_degraded(st, body, "INVALID")

        print("\n--- 5. Installation binding ---")
        install_license("test-installation-mismatch.json")
        st, data = await reload_license(admin)
        print("installation mismatch:", st, data.get("signed_license", {}).get("status"))
        assert st == 200 and data["signed_license"]["status"] == "INVALID"
        assert "installation_id" in data["signed_license"]["error_message"]

        print("\n--- 6. Expirada y gracia ---")
        install_license("test-expired.json")
        st, data = await reload_license(admin)
        print("expired:", st, data.get("signed_license", {}).get("status"))
        assert st == 200 and data["signed_license"]["status"] == "EXPIRED"
        assert data["license_operational"] is False
        st, body = await api("POST", "/api/v1/projects", {
            "project_slug": "stage11-expired", "display_name": "Expired",
            "description": "should fail", "default_branch": "main",
        }, bearer=admin)
        assert_degraded(st, body, "EXPIRED")

        install_license("test-grace.json")
        st, data = await reload_license(admin)
        print("grace:", st, data.get("signed_license", {}).get("status"))
        assert st == 200 and data["signed_license"]["status"] == "GRACE"
        assert data["license_operational"] is True
        st, data = await api("PUT", "/api/v1/admin/license", {"plan_code": "ENTERPRISE_10"}, bearer=admin)
        assert st == 200, data

        print("\n--- 7. Restauracion de produccion y auditoria ---")
        install_license("license.production.json")
        st, data = await reload_license(admin)
        print("production:", st, data.get("signed_license", {}).get("status"), data.get("plan_code"))
        assert st == 200 and data["signed_license"]["status"] == "VALID"
        assert data["plan_code"] == "ENTERPRISE_10"

        st, audit = await api("GET", "/api/v1/admin/license/audit?limit=100", bearer=admin)
        assert st == 200 and audit["chain"]["valid"] is True
        assert audit["events"]
        event_types = {e["event_type"] for e in audit["events"]}
        print("audit events:", sorted(event_types))
        assert "LICENSE_INVALID" in event_types
        assert "LICENSE_EXPIRED" in event_types
        assert "LICENSE_GRACE" in event_types

        print("\n--- 8. Deteccion de tamper en audit chain ---")
        row = db_rows("SELECT id::text, payload::text AS payload FROM license_audit_events ORDER BY id DESC LIMIT 1")[0]
        original_payload = row["payload"]
        db_exec(
            "UPDATE license_audit_events SET payload = jsonb_set(payload, '{data,tampered}', 'true'::jsonb) WHERE id = CAST(:id AS BIGINT)",
            id=row["id"],
        )
        st, broken = await api("GET", "/api/v1/admin/license/audit?limit=100", bearer=admin)
        print("audit tamper:", st, broken["chain"])
        assert st == 200 and broken["chain"]["valid"] is False
        db_exec(
            "UPDATE license_audit_events SET payload = CAST(:payload AS JSONB) WHERE id = CAST(:id AS BIGINT)",
            id=row["id"], payload=original_payload,
        )
        st, fixed = await api("GET", "/api/v1/admin/license/audit?limit=100", bearer=admin)
        assert st == 200 and fixed["chain"]["valid"] is True

        print("\n--- 9. UI de licencia firmada ---")
        st, ui = await api("GET", "/portal")
        assert st == 200 and isinstance(ui, str)
        for marker in ("licenseSignedStatus", "licenseAuditState", "licenseAuditRows", "Auditoría de licencia"):
            assert marker.lower() in ui.lower(), f"UI sin marcador: {marker}"
        print("portal:", len(ui), "bytes")

        print("\nE2E STAGE 11: OK")
    finally:
        if admin:
            try:
                install_license("license.production.json")
                await reload_license(admin)
            except Exception as exc:
                print("no se pudo restaurar la licencia de produccion:", exc)
            if original:
                try:
                    st, _ = await api("PUT", "/api/v1/admin/license", {
                        "plan_code": original["plan_code"],
                        **original["limits"],
                    }, bearer=admin)
                    print("policy restaurada:", st)
                except Exception as exc:
                    print("no se pudo restaurar policy:", exc)


try:
    asyncio.run(main())
finally:
    # Restauracion defensiva aun si el login fallo.
    try:
        source = TEST_DIR / "license.production.json"
        if source.exists():
            shutil.copyfile(source, LICENSE_DIR / "license.json")
    except Exception:
        pass
