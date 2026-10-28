"""Vendor-side activation service for Harness2.

En producción este servicio vive fuera de la infraestructura del cliente.
En el laboratorio on-premise se ejecuta como contenedor de referencia; el
cliente controla el host, así que el valor anti-tamper real viene de que la
clave privada no está en el contenedor de Harness2 y de la gracia offline.
"""
import base64
import json
import os
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

DB_PATH = Path(os.getenv("LICENSE_SERVER_DB", "/data/activation.db"))
PRIVATE_KEY_PATH = Path(os.getenv("LICENSE_SERVER_PRIVATE_KEY", "/license/private/private_key.pem"))
LEASE_TTL_SECONDS = int(os.getenv("LEASE_TTL_SECONDS", "604800"))
ACTIVATION_TOKEN = os.getenv("ACTIVATION_TOKEN", "").strip()
ADMIN_TOKEN = os.getenv("ACTIVATION_ADMIN_TOKEN", "").strip()
DEFAULT_LICENSE_ID = os.getenv("VENDOR_LICENSE_ID", "prod-enterprise-10-2026")
DEFAULT_INSTALLATION_ID = os.getenv("VENDOR_INSTALLATION_ID", "")
DEFAULT_PLAN = os.getenv("VENDOR_PLAN_CODE", "ENTERPRISE_10")
DEFAULT_LIMITS = json.loads(os.getenv("VENDOR_LIMITS_JSON", json.dumps({
    "max_active_projects": 10,
    "max_hermes_sessions": 10,
    "max_engineers_per_project": 10,
    "max_total_engineer_sessions": 30,
    "max_registered_users": 30,
    "max_parallel_rag_jobs": 3,
})))
PRIVATE_KEY = serialization.load_pem_private_key(PRIVATE_KEY_PATH.read_bytes(), password=None)
if not isinstance(PRIVATE_KEY, Ed25519PrivateKey):
    raise SystemExit("license server private key must be Ed25519")

MAINTENANCE = False

app = FastAPI(title="Harness2 Activation Service", version="1.0.0")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(db()) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activations (
                license_id TEXT NOT NULL,
                installation_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                plan_code TEXT NOT NULL,
                limits_json TEXT NOT NULL,
                lease_seq INTEGER NOT NULL DEFAULT 0,
                last_seen_at TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (license_id, installation_id)
            )
        """)
        if DEFAULT_LICENSE_ID and DEFAULT_INSTALLATION_ID:
            conn.execute("""
                INSERT INTO activations
                    (license_id, installation_id, status, plan_code, limits_json, created_at)
                VALUES (?, ?, 'ACTIVE', ?, ?, ?)
                ON CONFLICT (license_id, installation_id) DO NOTHING
            """, (
                DEFAULT_LICENSE_ID,
                DEFAULT_INSTALLATION_ID,
                DEFAULT_PLAN,
                json.dumps(DEFAULT_LIMITS),
                now_iso(),
            ))
        conn.commit()


init_db()


class ActivateRequest(BaseModel):
    license_id: str = Field(..., min_length=1, max_length=120)
    installation_id: str = Field(..., min_length=1, max_length=64)


class AdminRequest(BaseModel):
    license_id: str
    installation_id: str
    status: Optional[str] = None


class MaintenanceRequest(BaseModel):
    enabled: bool


def _require_token(token: Optional[str]):
    if ACTIVATION_TOKEN and (not token or token != ACTIVATION_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid activation token")


def _require_admin(token: Optional[str]):
    if ADMIN_TOKEN and (not token or token != ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid activation admin token")


@app.get("/health")
def health():
    return {"status": "ok", "service": "harness-activation", "lease_ttl_seconds": LEASE_TTL_SECONDS}


@app.post("/v1/activate")
def activate(payload: ActivateRequest, x_harness_activation_token: Optional[str] = Header(default=None)):
    _require_token(x_harness_activation_token)
    if MAINTENANCE:
        raise HTTPException(status_code=503, detail={
            "code": "ACTIVATION_MAINTENANCE",
            "message": "activation service is in maintenance",
        })
    with closing(db()) as conn:
        row = conn.execute("""
            SELECT license_id, installation_id, status, plan_code, limits_json, lease_seq
            FROM activations WHERE license_id = ? AND installation_id = ?
        """, (payload.license_id, payload.installation_id)).fetchone()
        if not row:
            raise HTTPException(status_code=403, detail={
                "code": "LICENSE_NOT_REGISTERED",
                "message": "license/installation is not registered for activation",
            })
        if row["status"] != "ACTIVE":
            raise HTTPException(status_code=403, detail={
                "code": "LICENSE_REVOKED",
                "message": "activation is revoked by vendor",
            })
        seq = int(row["lease_seq"]) + 1
        issued = datetime.now(timezone.utc)
        expires = issued + timedelta(seconds=LEASE_TTL_SECONDS)
        limits = json.loads(row["limits_json"])
        lease_payload = {
            "lease_id": str(uuid.uuid4()),
            "license_id": row["license_id"],
            "installation_id": row["installation_id"],
            "plan_code": row["plan_code"],
            "limits": {field: int(limits[field]) for field in limits},
            "issued_at": issued.isoformat(),
            "expires_at": expires.isoformat(),
            "nonce": uuid.uuid4().hex,
            "seq": seq,
        }
        signature = base64.b64encode(PRIVATE_KEY.sign(canonical_bytes(lease_payload))).decode("ascii")
        conn.execute("""
            UPDATE activations
            SET lease_seq = ?, last_seen_at = ?
            WHERE license_id = ? AND installation_id = ?
        """, (seq, issued.isoformat(), row["license_id"], row["installation_id"]))
        conn.commit()
        return {
            "lease": {"payload": lease_payload, "signature": signature},
            "server_time": now_iso(),
        }


@app.post("/v1/revoke")
def revoke(payload: AdminRequest, x_activation_admin: Optional[str] = Header(default=None)):
    _require_admin(x_activation_admin)
    with closing(db()) as conn:
        cur = conn.execute("""
            UPDATE activations SET status = 'REVOKED'
            WHERE license_id = ? AND installation_id = ?
        """, (payload.license_id, payload.installation_id))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="activation not found")
        return {"status": "REVOKED", "license_id": payload.license_id}


@app.post("/v1/restore")
def restore(payload: AdminRequest, x_activation_admin: Optional[str] = Header(default=None)):
    _require_admin(x_activation_admin)
    with closing(db()) as conn:
        cur = conn.execute("""
            UPDATE activations SET status = 'ACTIVE'
            WHERE license_id = ? AND installation_id = ?
        """, (payload.license_id, payload.installation_id))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="activation not found")
        return {"status": "ACTIVE", "license_id": payload.license_id}


@app.post("/v1/maintenance")
def maintenance(payload: MaintenanceRequest, x_activation_admin: Optional[str] = Header(default=None)):
    _require_admin(x_activation_admin)
    global MAINTENANCE
    MAINTENANCE = bool(payload.enabled)
    return {"maintenance": MAINTENANCE}

