"""Genera el set de licencias de despliegue y pruebas (uso del vendor).

Requiere la clave privada Ed25519. No se ejecuta dentro de la app en runtime.
"""
import argparse
import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.licensing import LICENSE_TIERS, QUOTA_FIELDS


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign(payload, private_key):
    return {
        "payload": payload,
        "signature": base64.b64encode(private_key.sign(canonical_bytes(payload))).decode("ascii"),
    }


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--installation-id", required=True)
    parser.add_argument("--private", default="/license/private/private_key.pem")
    parser.add_argument("--out-dir", default="/license/public")
    args = parser.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    private = serialization.load_pem_private_key(Path(args.private).read_bytes(), password=None)
    if not isinstance(private, Ed25519PrivateKey):
        raise SystemExit("private key must be Ed25519")
    now = datetime.now(timezone.utc)
    installation = args.installation_id

    def payload(license_id, plan, allowed_plans, limits, expires, grace_days, test=False):
        return {
            "license_id": license_id,
            "customer": "Harness2 On-Premise",
            "installation_id": installation,
            "plan_code": plan,
            "allowed_plans": allowed_plans,
            "limits": {field: int(limits[field]) for field in QUOTA_FIELDS},
            "features": ["rag", "hermes_pool", "license_audit"],
            "issued_at": iso(now),
            "expires_at": iso(expires),
            "grace_days": grace_days,
            "test": test,
        }

    enterprise = LICENSE_TIERS["ENTERPRISE_10"]
    production = payload(
        "prod-enterprise-10-2026", "ENTERPRISE_10", ["ENTERPRISE_10"],
        enterprise, now + timedelta(days=365), 30,
    )
    all_ceiling = LICENSE_TIERS["MAX_20"]
    all_plans = payload(
        "test-all-plans-2026", "ENTERPRISE_10",
        ["PERSONAL_1", "TEAM_3", "BUSINESS_5", "ENTERPRISE_10", "MAX_20"],
        all_ceiling, now + timedelta(days=365), 30, test=True,
    )
    grace = payload(
        "test-grace-2026", "ENTERPRISE_10", ["ENTERPRISE_10"],
        enterprise, now - timedelta(days=1), 30, test=True,
    )
    expired = payload(
        "test-expired-2026", "ENTERPRISE_10", ["ENTERPRISE_10"],
        enterprise, now - timedelta(days=2), 0, test=True,
    )
    mismatch = payload(
        "test-installation-mismatch", "ENTERPRISE_10", ["ENTERPRISE_10"],
        enterprise, now + timedelta(days=30), 30, test=True,
    )
    mismatch["installation_id"] = "00000000-0000-0000-0000-000000000000"

    files = {
        "license.json": sign(production, private),
        "license.production.json": sign(production, private),
        "test-all-plans.json": sign(all_plans, private),
        "test-grace.json": sign(grace, private),
        "test-expired.json": sign(expired, private),
        "test-installation-mismatch.json": sign(mismatch, private),
    }
    for name, document in files.items():
        (out / name).write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tampered = json.loads(json.dumps(files["test-all-plans.json"]))
    tampered["payload"]["limits"]["max_active_projects"] = 999
    (out / "test-tampered.json").write_text(json.dumps(tampered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("licenses written:")
    for file in sorted(out.glob("*.json")):
        print(" -", file)


if __name__ == "__main__":
    main()
