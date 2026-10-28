"""Licencia firmada Ed25519 + auditoria hash-chain.

La fuente de verdad es el archivo firmado por el vendor. La base de datos solo
cachea el estado y la politica activa; un Admin no puede elevar cuotas por SQL.
"""
import base64
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional, Tuple

import sqlalchemy as sa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from src.licensing import LICENSE_TIERS, QUOTA_FIELDS, LicenseError

ZERO_HASH = "0" * 64
OPERATIONAL = ("VALID", "GRACE", "DEVELOPMENT")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _load_public_key() -> Optional[Ed25519PublicKey]:
    raw_b64 = os.getenv("HARNESS_LICENSE_PUBLIC_KEY", "").strip()
    if raw_b64:
        try:
            raw = base64.b64decode(raw_b64)
            if len(raw) == 32:
                return Ed25519PublicKey.from_public_bytes(raw)
        except Exception:
            return None
    path = os.getenv("HARNESS_LICENSE_PUBLIC_KEY_FILE", "").strip()
    if path and Path(path).exists():
        try:
            key = serialization.load_pem_public_key(Path(path).read_bytes())
            if isinstance(key, Ed25519PublicKey):
                return key
        except Exception:
            return None
    return None


class LicenseManager:
    def __init__(self, engine: sa.Engine):
        self.engine = engine
        self.license_file = Path(os.getenv("HARNESS_LICENSE_FILE", "/var/lib/harness/license/license.json"))
        self.required = os.getenv("HARNESS_LICENSE_REQUIRED", "true").strip().lower() not in ("0", "false", "no")
        self.public_key = _load_public_key()
        self.lock = RLock()
        self.state: Dict[str, Any] = {
            "status": "MISSING",
            "license_id": None,
            "license_hash": None,
            "plan_code": None,
            "customer": None,
            "installation_id": None,
            "allowed_plans": [],
            "signed_limits": {},
            "features": [],
            "issued_at": None,
            "expires_at": None,
            "grace_until": None,
            "last_verified_at": None,
            "error_message": "license not loaded",
            "raw_license": None,
        }
        self._last_audit_status: Optional[str] = None
        self._last_audit_hash: Optional[str] = None

    # ------------------------------------------------------------------
    # Estado y validacion
    # ------------------------------------------------------------------
    def _installation_id(self, conn) -> str:
        row = conn.execute(sa.text("SELECT installation_id::text FROM system_installation WHERE id = 1")).fetchone()
        if row:
            return row[0]
        import uuid
        new_id = str(uuid.uuid4())
        conn.execute(
            sa.text("INSERT INTO system_installation (id, installation_id) VALUES (1, CAST(:iid AS UUID))"),
            {"iid": new_id},
        )
        return new_id

    def get_state(self, conn=None) -> Dict[str, Any]:
        if conn is None:
            with self.engine.connect() as own:
                return self.get_state(own)
        row = conn.execute(
            sa.text("""
                SELECT status, license_id, license_hash, plan_code, customer,
                       installation_id::text, allowed_plans, signed_limits, features,
                       issued_at, expires_at, grace_until, last_verified_at,
                       error_message, raw_license, updated_at
                FROM system_license_state WHERE id = 1
            """)
        ).mappings().fetchone()
        if not row:
            return dict(self.state)
        return dict(row)

    def _verify_document(self, doc: Any, installation_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if not isinstance(doc, dict):
            return None, "license file is not a JSON object"
        payload = doc.get("payload")
        signature_b64 = doc.get("signature")
        if not isinstance(payload, dict) or not signature_b64:
            return None, "license must contain payload and signature"
        if not self.public_key:
            return None, "license public key is not configured"
        try:
            signature = base64.b64decode(signature_b64)
            self.public_key.verify(signature, canonical_bytes(payload))
        except Exception:
            return None, "invalid license signature"
        license_installation = str(payload.get("installation_id") or "")
        if license_installation != installation_id:
            return None, "license installation_id does not match this installation"
        if not payload.get("plan_code") or payload.get("plan_code") not in LICENSE_TIERS:
            return None, "license plan_code is invalid"
        allowed = payload.get("allowed_plans") or [payload["plan_code"]]
        if not isinstance(allowed, list) or not allowed:
            return None, "license allowed_plans is invalid"
        for plan in allowed:
            if plan not in LICENSE_TIERS:
                return None, f"license allowed_plans contains invalid plan: {plan}"
        limits = payload.get("limits") or {}
        if not isinstance(limits, dict):
            return None, "license limits is invalid"
        for field in QUOTA_FIELDS:
            if field not in limits:
                return None, f"license limits missing {field}"
            try:
                if int(limits[field]) < 0:
                    return None, f"license limits.{field} is negative"
            except Exception:
                return None, f"license limits.{field} is not an integer"
        return payload, None

    def _status_for(self, payload: Dict[str, Any]) -> Tuple[str, Optional[datetime], Optional[datetime], Optional[str]]:
        expires = _parse_dt(payload.get("expires_at"))
        if not expires:
            return "INVALID", None, None, "license expires_at is missing or invalid"
        grace_days = int(payload.get("grace_days", 0) or 0)
        grace_until = expires + timedelta(days=grace_days)
        now = datetime.now(timezone.utc)
        if now > grace_until:
            return "EXPIRED", expires, grace_until, "license expired and grace period ended"
        if now > expires:
            return "GRACE", expires, grace_until, "license expired; grace period active"
        return "VALID", expires, grace_until, None

    def _reconcile_policy(self, conn, payload: Dict[str, Any]) -> bool:
        allowed = payload.get("allowed_plans") or [payload["plan_code"]]
        limits = payload.get("limits") or {}
        row = conn.execute(
            sa.text("""
                SELECT plan_code, max_active_projects, max_hermes_sessions,
                       max_engineers_per_project, max_total_engineer_sessions,
                       max_registered_users, max_parallel_rag_jobs
                FROM system_license_policy WHERE id = 1
            """)
        ).mappings().fetchone()
        valid = bool(row) and row["plan_code"] in allowed
        if valid:
            for field in QUOTA_FIELDS:
                if int(row[field]) > int(limits[field]):
                    valid = False
                    break
        if valid:
            return False
        default_plan = payload["plan_code"]
        defaults = {field: int(limits[field]) for field in QUOTA_FIELDS}
        conn.execute(
            sa.text("""
                UPDATE system_license_policy
                SET plan_code = :plan_code,
                    max_active_projects = :max_active_projects,
                    max_hermes_sessions = :max_hermes_sessions,
                    max_engineers_per_project = :max_engineers_per_project,
                    max_total_engineer_sessions = :max_total_engineer_sessions,
                    max_registered_users = :max_registered_users,
                    max_parallel_rag_jobs = :max_parallel_rag_jobs,
                    updated_at = NOW()
                WHERE id = 1
            """),
            {"plan_code": default_plan, **defaults},
        )
        return True

    def _audit(
        self,
        conn,
        event_type: str,
        actor: Optional[str],
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        conn.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext('harness_license_audit'))"))
        last = conn.execute(
            sa.text("SELECT event_hash FROM license_audit_events ORDER BY id DESC LIMIT 1")
        ).fetchone()
        prev_hash = last[0] if last else ZERO_HASH
        event_time = datetime.now(timezone.utc)
        body = {
            "event_type": event_type,
            "actor": actor,
            "data": data or {},
            "event_time": _iso(event_time),
        }
        event_hash = sha256_hex((prev_hash + canonical_bytes(body).decode("utf-8")).encode("utf-8"))
        conn.execute(
            sa.text("""
                INSERT INTO license_audit_events
                    (event_type, actor, payload, prev_hash, event_hash, created_at)
                VALUES
                    (:etype, :actor, CAST(:payload AS JSONB), :prev, :hash, :created)
            """),
            {
                "etype": event_type,
                "actor": actor,
                "payload": json.dumps(body, ensure_ascii=False),
                "prev": prev_hash,
                "hash": event_hash,
                "created": event_time,
            },
        )

    def record_audit(self, event_type: str, actor: Optional[str], payload: Optional[Dict[str, Any]] = None) -> None:
        with self.engine.begin() as conn:
            self._audit(conn, event_type, actor, payload)

    def reload(self, actor: str = "system", force: bool = False) -> Dict[str, Any]:
        with self.lock:
            with self.engine.begin() as conn:
                installation_id = self._installation_id(conn)
                new_state: Dict[str, Any] = {
                    "status": "MISSING",
                    "license_id": None,
                    "license_hash": None,
                    "plan_code": None,
                    "customer": None,
                    "installation_id": installation_id,
                    "allowed_plans": [],
                    "signed_limits": {},
                    "features": [],
                    "issued_at": None,
                    "expires_at": None,
                    "grace_until": None,
                    "last_verified_at": datetime.now(timezone.utc),
                    "error_message": "license file is missing",
                    "raw_license": None,
                }
                policy_reset = False
                if not self.license_file.exists():
                    if not self.required:
                        new_state.update({
                            "status": "DEVELOPMENT",
                            "plan_code": None,
                            "error_message": "license required=false; development mode",
                        })
                else:
                    try:
                        doc = json.loads(self.license_file.read_text(encoding="utf-8"))
                    except Exception as exc:
                        new_state["status"] = "INVALID"
                        new_state["error_message"] = f"license file is not valid JSON: {exc}"
                        doc = None
                    if doc is not None:
                        payload, error = self._verify_document(doc, installation_id)
                        if error:
                            new_state["status"] = "INVALID"
                            new_state["error_message"] = error
                        else:
                            status, expires, grace_until, status_error = self._status_for(payload)
                            new_state.update({
                                "status": status,
                                "license_id": payload.get("license_id"),
                                "license_hash": sha256_hex(canonical_bytes(payload)),
                                "plan_code": payload.get("plan_code"),
                                "customer": payload.get("customer"),
                                "allowed_plans": payload.get("allowed_plans") or [payload.get("plan_code")],
                                "signed_limits": payload.get("limits") or {},
                                "features": payload.get("features") or [],
                                "issued_at": _parse_dt(payload.get("issued_at")),
                                "expires_at": expires,
                                "grace_until": grace_until,
                                "error_message": status_error,
                                "raw_license": doc,
                            })
                            if status in OPERATIONAL:
                                policy_reset = self._reconcile_policy(conn, payload)

                conn.execute(
                    sa.text("""
                        UPDATE system_license_state
                        SET status = :status, license_id = :license_id, license_hash = :license_hash,
                            plan_code = :plan_code, customer = :customer,
                            installation_id = CAST(:installation_id AS UUID),
                            allowed_plans = CAST(:allowed_plans AS JSONB),
                            signed_limits = CAST(:signed_limits AS JSONB),
                            features = CAST(:features AS JSONB),
                            issued_at = :issued_at, expires_at = :expires_at,
                            grace_until = :grace_until, last_verified_at = :verified_at,
                            error_message = :error_message,
                            raw_license = CAST(:raw_license AS JSONB),
                            updated_at = NOW()
                        WHERE id = 1
                    """),
                    {
                        "status": new_state["status"],
                        "license_id": new_state["license_id"],
                        "license_hash": new_state["license_hash"],
                        "plan_code": new_state["plan_code"],
                        "customer": new_state["customer"],
                        "installation_id": installation_id,
                        "allowed_plans": json.dumps(new_state["allowed_plans"]),
                        "signed_limits": json.dumps(new_state["signed_limits"]),
                        "features": json.dumps(new_state["features"]),
                        "issued_at": new_state["issued_at"],
                        "expires_at": new_state["expires_at"],
                        "grace_until": new_state["grace_until"],
                        "verified_at": new_state["last_verified_at"],
                        "error_message": new_state["error_message"],
                        "raw_license": json.dumps(new_state["raw_license"], ensure_ascii=False) if new_state["raw_license"] else None,
                    },
                )
                changed = new_state["status"] != self._last_audit_status or new_state["license_hash"] != self._last_audit_hash
                if changed or force:
                    event_type = {
                        "VALID": "LICENSE_VALID",
                        "GRACE": "LICENSE_GRACE",
                        "EXPIRED": "LICENSE_EXPIRED",
                        "INVALID": "LICENSE_INVALID",
                        "MISSING": "LICENSE_MISSING",
                        "DEVELOPMENT": "LICENSE_DEVELOPMENT",
                    }.get(new_state["status"], "LICENSE_CHECK")
                    self._audit(conn, event_type, actor, {
                        "status": new_state["status"],
                        "license_id": new_state["license_id"],
                        "license_hash": new_state["license_hash"],
                        "plan_code": new_state["plan_code"],
                        "error": new_state["error_message"],
                        "installation_id": installation_id,
                        "policy_reset": policy_reset,
                    })
                    self._last_audit_status = new_state["status"]
                    self._last_audit_hash = new_state["license_hash"]
                self.state = new_state
            return dict(self.state)

    # ------------------------------------------------------------------
    # Enforcement
    # ------------------------------------------------------------------
    def is_operational(self) -> bool:
        state = self.state
        if state.get("status") not in OPERATIONAL:
            return False
        grace_until = _parse_dt(state.get("grace_until"))
        if grace_until and datetime.now(timezone.utc) > grace_until:
            state["status"] = "EXPIRED"
            state["error_message"] = "license grace period ended"
            return False
        return True

    def assert_operational(self) -> None:
        if not self.is_operational():
            status = self.state.get("status") or "UNKNOWN"
            detail = self.state.get("error_message") or status
            raise LicenseError(
                f"License Degraded ({status}): {detail}",
                status_code=503,
                code="LICENSE_DEGRADED",
            )

    def validate_plan_limits(self, plan_code: str, limits: Dict[str, Any]) -> None:
        self.assert_operational()
        allowed = set(self.state.get("allowed_plans") or [])
        if plan_code not in allowed:
            raise LicenseError(
                f"License plan {plan_code} is not included in the signed entitlement "
                f"(allowed: {', '.join(sorted(allowed)) or 'none'})",
                status_code=403,
                code="LICENSE_PLAN_NOT_ENTITLED",
            )
        ceiling = self.state.get("signed_limits") or {}
        for field in QUOTA_FIELDS:
            if field not in ceiling:
                raise LicenseError(
                    f"Signed license has no ceiling for {field}",
                    status_code=403,
                    code="LICENSE_ENTITLEMENT_INCOMPLETE",
                )
            if int(limits[field]) > int(ceiling[field]):
                raise LicenseError(
                    f"License entitlement exceeded: {field}={limits[field]} > signed ceiling {ceiling[field]}",
                    status_code=403,
                    code="LICENSE_ENTITLEMENT_EXCEEDED",
                )

    def verify_audit_chain(self, conn=None, limit: Optional[int] = None) -> Dict[str, Any]:
        if conn is None:
            with self.engine.connect() as own:
                return self.verify_audit_chain(own, limit)
        sql = "SELECT id, payload, prev_hash, event_hash FROM license_audit_events ORDER BY id"
        rows = conn.execute(sa.text(sql)).mappings().all()
        prev = ZERO_HASH
        if limit and len(rows) > limit:
            start = len(rows) - limit
            prev = rows[start - 1]["event_hash"]
            rows = rows[start:]
        for row in rows:
            body = row["payload"]
            expected = sha256_hex((prev + canonical_bytes(body).decode("utf-8")).encode("utf-8"))
            if row["prev_hash"] != prev or row["event_hash"] != expected:
                return {
                    "valid": False,
                    "events_checked": len(rows),
                    "broken_at": row["id"],
                    "error": "hash chain mismatch",
                }
            prev = row["event_hash"]
        return {"valid": True, "events_checked": len(rows), "broken_at": None, "error": None}
