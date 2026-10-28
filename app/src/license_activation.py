"""Activación online híbrida con lease Ed25519 y gracia offline."""
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Dict, Optional

import sqlalchemy as sa

from src.licensing import QUOTA_FIELDS, LicenseError
from src.signed_license import LicenseManager, canonical_bytes, _parse_dt


class ActivationManager:
    def __init__(self, engine: sa.Engine, license_manager: LicenseManager):
        self.engine = engine
        self.license_manager = license_manager
        self.url = os.getenv("HARNESS_ACTIVATION_URL", "").strip().rstrip("/")
        self.required = os.getenv("HARNESS_ACTIVATION_REQUIRED", "false").strip().lower() in ("1", "true", "yes")
        self.token = os.getenv("HARNESS_ACTIVATION_TOKEN", "").strip()
        self.timeout = int(os.getenv("HARNESS_ACTIVATION_TIMEOUT", "15"))
        self.grace_days = int(os.getenv("HARNESS_ACTIVATION_GRACE_DAYS", "7"))
        self.lock = RLock()
        self.state: Dict[str, Any] = {
            "status": "NOT_CONFIGURED",
            "server_url": self.url or None,
            "license_id": None,
            "installation_id": None,
            "lease_id": None,
            "lease_expires_at": None,
            "lease_grace_until": None,
            "last_success_at": None,
            "last_attempt_at": None,
            "last_error": "activation not loaded",
            "lease_payload": None,
            "lease_signature": None,
        }

    def get_state(self, conn=None) -> Dict[str, Any]:
        if conn is None:
            with self.engine.connect() as own:
                return self.get_state(own)
        row = conn.execute(
            sa.text("""
                SELECT status, server_url, license_id, installation_id::text,
                       lease_id, lease_expires_at, lease_grace_until,
                       last_success_at, last_attempt_at, last_error,
                       lease_payload, lease_signature, updated_at
                FROM license_activation_state WHERE id = 1
            """)
        ).mappings().fetchone()
        if not row:
            return dict(self.state)
        data = dict(row)
        self.state = data
        return data

    def _is_test_license(self) -> bool:
        raw = self.license_manager.state.get("raw_license") or {}
        payload = raw.get("payload") if isinstance(raw, dict) else {}
        return bool(payload.get("test")) if isinstance(payload, dict) else False

    def _stored_lease_valid(self) -> bool:
        payload = self.state.get("lease_payload")
        signature = self.state.get("lease_signature")
        installation = self.state.get("installation_id") or self.license_manager.state.get("installation_id")
        license_id = self.state.get("license_id") or self.license_manager.state.get("license_id")
        if not payload or not signature or not installation or not license_id:
            return False
        try:
            self._verify_lease({"payload": payload, "signature": signature}, str(installation), str(license_id))
            return True
        except Exception:
            return False

    def is_operational(self) -> bool:
        if self._is_test_license():
            return True
        if not self.url:
            return not self.required
        status = self.state.get("status")
        now = datetime.now(timezone.utc)
        if status in ("ACTIVE", "OFFLINE_GRACE") and not self._stored_lease_valid():
            return False
        if status == "ACTIVE":
            expires = _parse_dt(self.state.get("lease_expires_at"))
            if expires and now <= expires:
                return True
            grace = _parse_dt(self.state.get("lease_grace_until"))
            if grace and now <= grace:
                self.state["status"] = "OFFLINE_GRACE"
                return True
            return False
        if status == "OFFLINE_GRACE":
            grace = _parse_dt(self.state.get("lease_grace_until"))
            return bool(grace and now <= grace)
        return False

    def assert_operational(self) -> None:
        if not self.is_operational():
            status = self.state.get("status") or "UNKNOWN"
            detail = self.state.get("last_error") or status
            raise LicenseError(
                f"License Activation Required ({status}): {detail}",
                status_code=503,
                code="LICENSE_ACTIVATION_REQUIRED",
            )

    def _verify_lease(self, document: Any, installation_id: str, license_id: str) -> Dict[str, Any]:
        if not isinstance(document, dict):
            raise ValueError("lease document is invalid")
        payload = document.get("payload")
        signature_b64 = document.get("signature")
        if not isinstance(payload, dict) or not signature_b64:
            raise ValueError("lease must contain payload and signature")
        if not self.license_manager.public_key:
            raise ValueError("license public key is not configured")
        import base64
        self.license_manager.public_key.verify(
            base64.b64decode(signature_b64), canonical_bytes(payload)
        )
        if str(payload.get("installation_id")) != installation_id:
            raise ValueError("lease installation_id does not match")
        if str(payload.get("license_id")) != license_id:
            raise ValueError("lease license_id does not match")
        expires = _parse_dt(payload.get("expires_at"))
        if not expires or expires <= datetime.now(timezone.utc):
            raise ValueError("lease is expired")
        allowed = set(self.license_manager.state.get("allowed_plans") or [])
        if payload.get("plan_code") not in allowed:
            raise ValueError("lease plan is not allowed by signed license")
        limits = payload.get("limits") or {}
        ceiling = self.license_manager.state.get("signed_limits") or {}
        for field in QUOTA_FIELDS:
            if field not in limits or field not in ceiling:
                raise ValueError(f"lease limits missing {field}")
            if int(limits[field]) > int(ceiling[field]):
                raise ValueError(f"lease limits exceed signed ceiling for {field}")
        return payload

    def _store(self, conn, **values) -> None:
        params = {
            "status": values.get("status"),
            "server_url": self.url or None,
            "license_id": values.get("license_id"),
            "installation_id": values.get("installation_id"),
            "lease_id": values.get("lease_id"),
            "lease_expires_at": values.get("lease_expires_at"),
            "lease_grace_until": values.get("lease_grace_until"),
            "last_success_at": values.get("last_success_at"),
            "last_attempt_at": values.get("last_attempt_at", datetime.now(timezone.utc)),
            "last_error": values.get("last_error"),
            "lease_payload": json.dumps(values.get("lease_payload"), ensure_ascii=False) if values.get("lease_payload") else None,
            "lease_signature": values.get("lease_signature"),
        }
        conn.execute(
            sa.text("""
                UPDATE license_activation_state
                SET status = :status, server_url = :server_url, license_id = :license_id,
                    installation_id = CAST(:installation_id AS UUID), lease_id = :lease_id,
                    lease_expires_at = :lease_expires_at, lease_grace_until = :lease_grace_until,
                    last_success_at = :last_success_at, last_attempt_at = :last_attempt_at,
                    last_error = :last_error,
                    lease_payload = CAST(:lease_payload AS JSONB),
                    lease_signature = :lease_signature, updated_at = NOW()
                WHERE id = 1
            """),
            params,
        )
        self.state = {**self.state, **values, "server_url": self.url or None}

    def activate(
        self,
        actor: str = "system",
        force: bool = False,
        audit_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self.lock:
            now = datetime.now(timezone.utc)
            installation = self.license_manager.state.get("installation_id")
            license_id = self.license_manager.state.get("license_id")
            if not installation or not license_id:
                with self.engine.begin() as conn:
                    self._store(conn, status="ERROR", last_error="signed license is not loaded",
                                last_attempt_at=now)
                return dict(self.state)

            with self.engine.connect() as conn:
                self.get_state(conn)
            if self.state.get("status") in ("ACTIVE", "OFFLINE_GRACE") and not self._stored_lease_valid():
                with self.engine.begin() as conn:
                    self._store(
                        conn, status="ERROR", license_id=license_id,
                        installation_id=installation,
                        last_error="stored lease signature is invalid",
                        last_attempt_at=now,
                        lease_payload=self.state.get("lease_payload"),
                        lease_signature=self.state.get("lease_signature"),
                        lease_expires_at=_parse_dt(self.state.get("lease_expires_at")),
                        lease_grace_until=_parse_dt(self.state.get("lease_grace_until")),
                        last_success_at=self.state.get("last_success_at"),
                    )
                self.license_manager.record_audit(
                    "LICENSE_LEASE_INVALID", actor,
                    {"license_id": license_id, "reason": "stored lease signature invalid"},
                    context=audit_context,
                )
                return dict(self.state)

            if self._is_test_license():
                with self.engine.begin() as conn:
                    self._store(conn, status="TEST_BYPASS", license_id=license_id,
                                installation_id=installation, last_error="test license bypasses activation",
                                last_attempt_at=now)
                self.license_manager.record_audit(
                    "LICENSE_ACTIVATION_SKIPPED", actor,
                    {"reason": "test_license", "license_id": license_id},
                    context=audit_context,
                )
                return dict(self.state)

            if not self.url:
                status = "ERROR" if self.required else "NOT_CONFIGURED"
                with self.engine.begin() as conn:
                    self._store(
                        conn, status=status, license_id=license_id, installation_id=installation,
                        last_error="HARNESS_ACTIVATION_URL is not configured", last_attempt_at=now,
                    )
                return dict(self.state)

            request_body = {"license_id": license_id, "installation_id": installation}
            headers = {"Content-Type": "application/json"}
            if self.token:
                headers["X-Harness-Activation-Token"] = self.token
            req = urllib.request.Request(
                self.url + "/v1/activate",
                data=json.dumps(request_body).encode("utf-8"),
                method="POST",
                headers=headers,
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                lease = data.get("lease")
                payload = self._verify_lease(lease, installation, license_id)
                expires = _parse_dt(payload["expires_at"])
                grace_until = expires + timedelta(days=self.grace_days)
                previous = self.state.get("status")
                with self.engine.begin() as conn:
                    self._store(
                        conn, status="ACTIVE", license_id=license_id,
                        installation_id=installation, lease_id=payload.get("lease_id"),
                        lease_expires_at=expires, lease_grace_until=grace_until,
                        last_success_at=now, last_attempt_at=now, last_error=None,
                        lease_payload=payload, lease_signature=lease.get("signature"),
                    )
                self.license_manager.record_audit(
                    "LICENSE_ACTIVATED" if previous != "ACTIVE" else "LICENSE_LEASE_RENEWED",
                    actor,
                    {"license_id": license_id, "lease_id": payload.get("lease_id"),
                     "expires_at": payload.get("expires_at"), "plan_code": payload.get("plan_code")},
                    context=audit_context,
                )
                return dict(self.state)
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                code = ""
                try:
                    parsed = json.loads(raw) or {}
                    code = parsed.get("code") or (parsed.get("detail") or {}).get("code", "")
                except Exception:
                    pass
                if exc.code == 403 and code == "LICENSE_REVOKED":
                    with self.engine.begin() as conn:
                        self._store(
                            conn, status="REVOKED", license_id=license_id,
                            installation_id=installation, last_error="activation revoked by vendor",
                            last_attempt_at=now,
                        )
                    self.license_manager.record_audit(
                        "LICENSE_REVOKED", actor, {"license_id": license_id},
                        context=audit_context,
                    )
                    return dict(self.state)
                return self._offline_or_error(actor, license_id, installation, now,
                                              f"activation HTTP {exc.code}: {raw[:300]}", audit_context)
            except Exception as exc:
                return self._offline_or_error(actor, license_id, installation, now, str(exc), audit_context)

    def _offline_or_error(
        self,
        actor: str,
        license_id: str,
        installation: str,
        now: datetime,
        error: str,
        audit_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        grace = _parse_dt(self.state.get("lease_grace_until"))
        expires = _parse_dt(self.state.get("lease_expires_at"))
        if grace and now <= grace:
            with self.engine.begin() as conn:
                self._store(
                    conn, status="OFFLINE_GRACE", license_id=license_id,
                    installation_id=installation, last_error=error[:1000],
                    last_attempt_at=now,
                    lease_payload=self.state.get("lease_payload"),
                    lease_signature=self.state.get("lease_signature"),
                    lease_expires_at=expires, lease_grace_until=grace,
                    last_success_at=self.state.get("last_success_at"),
                )
            self.license_manager.record_audit(
                "LICENSE_OFFLINE_GRACE", actor,
                {"license_id": license_id, "error": error[:500], "lease_expires_at": str(expires)},
                context=audit_context,
            )
        else:
            with self.engine.begin() as conn:
                self._store(
                    conn, status="EXPIRED", license_id=license_id,
                    installation_id=installation, last_error=error[:1000],
                    last_attempt_at=now,
                    lease_payload=self.state.get("lease_payload"),
                    lease_signature=self.state.get("lease_signature"),
                    lease_expires_at=expires, lease_grace_until=grace,
                    last_success_at=self.state.get("last_success_at"),
                )
            self.license_manager.record_audit(
                "LICENSE_ACTIVATION_EXPIRED", actor,
                {"license_id": license_id, "error": error[:500]},
                context=audit_context,
            )
        return dict(self.state)
