"""Herramienta de licencias Harness2 (uso del vendor, nunca dentro de la app).

Ejemplos:
  python license_tool.py keygen --private private.pem --public public.pem
  python license_tool.py sign --private private.pem --payload payload.json --out license.json
  python license_tool.py verify --public public.pem --license license.json
"""
import argparse
import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def load_private(path):
    key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit("private key must be Ed25519")
    return key


def load_public(path):
    key = serialization.load_pem_public_key(Path(path).read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise SystemExit("public key must be Ed25519")
    return key


def cmd_keygen(args):
    private = Ed25519PrivateKey.generate()
    Path(args.private).write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    Path(args.public).write_bytes(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    print("keypair generated")


def cmd_sign(args):
    private = load_private(args.private)
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    signature = private.sign(canonical_bytes(payload))
    document = {
        "payload": payload,
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    Path(args.out).write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"license written to {args.out}")


def cmd_verify(args):
    public = load_public(args.public)
    document = json.loads(Path(args.license).read_text(encoding="utf-8"))
    payload = document.get("payload") or {}
    signature = base64.b64decode(document.get("signature") or "")
    public.verify(signature, canonical_bytes(payload))
    print("signature valid")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def cmd_show(args):
    document = json.loads(Path(args.license).read_text(encoding="utf-8"))
    print(json.dumps(document.get("payload") or {}, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Harness2 license tool")
    sub = parser.add_subparsers(dest="command", required=True)

    keygen = sub.add_parser("keygen")
    keygen.add_argument("--private", required=True)
    keygen.add_argument("--public", required=True)
    keygen.set_defaults(func=cmd_keygen)

    sign = sub.add_parser("sign")
    sign.add_argument("--private", required=True)
    sign.add_argument("--payload", required=True)
    sign.add_argument("--out", required=True)
    sign.set_defaults(func=cmd_sign)

    verify = sub.add_parser("verify")
    verify.add_argument("--public", required=True)
    verify.add_argument("--license", required=True)
    verify.set_defaults(func=cmd_verify)

    show = sub.add_parser("show")
    show.add_argument("--license", required=True)
    show.set_defaults(func=cmd_show)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
