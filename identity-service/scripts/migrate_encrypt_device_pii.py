#!/usr/bin/env python3
"""F137 — migrate plaintext device PII to encrypted-at-rest.

Before F137, `known_devices` stored `deviceName`, `userAgent`, and `ip` in
plaintext, and `webauthn_credentials` stored `deviceName` in plaintext — leaking
device/browser/IP PII. F137 encrypts these fields (AES-256-GCM via the existing
VAULT_ENCRYPTION_KEY) with a `piiEncrypted` flag as the decrypt-vs-legacy marker.

For every known_devices doc lacking `piiEncrypted`: encrypt any non-empty
`deviceName`/`userAgent`/`ip`.
For every webauthn_credentials doc lacking `piiEncrypted`: encrypt any non-empty
`deviceName`.

`deviceId`, `userId`, and timestamps stay plaintext (lookup keys / not PII).

Run from identity-service/ (each line copyable on its own):
    source .venv/bin/activate
    python3 scripts/migrate_encrypt_device_pii.py --mongo-uri "mongodb+srv://..." --key "..." --dry-run
    python3 scripts/migrate_encrypt_device_pii.py --mongo-uri "mongodb+srv://..." --key "..."
Or via env vars (MONGO_URI / VAULT_ENCRYPTION_KEY) as before.
"""

import argparse
import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pymongo import MongoClient

DB_NAME = "emergency_exit"
DEVICE_FIELDS = ("deviceName", "userAgent", "ip")
CRED_FIELDS = ("deviceName",)


def _connect(mongo_uri: str = None):
    mongo_uri = mongo_uri or os.environ.get("MONGO_URI")
    if not mongo_uri:
        raise SystemExit("ERROR: MONGO_URI not set (use --mongo-uri or MONGO_URI env var).")
    return MongoClient(mongo_uri)[DB_NAME]


def _cipher(key: str = None):
    key = key or os.environ.get("VAULT_ENCRYPTION_KEY", "")
    if not key:
        raise SystemExit("ERROR: VAULT_ENCRYPTION_KEY not set (use --key or VAULT_ENCRYPTION_KEY env var).")
    return AESGCM(bytes.fromhex(key))


def _encrypt_string(cipher: AESGCM, plaintext: str) -> str:
    nonce = os.urandom(12)
    ciphertext = cipher.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def _encrypt_fields(cipher: AESGCM, doc: dict, fields) -> dict:
    out = {}
    for field in fields:
        value = doc.get(field)
        if isinstance(value, str) and value:
            out[field] = _encrypt_string(cipher, value)
    out["piiEncrypted"] = True
    return out


def main():
    parser = argparse.ArgumentParser(description="F137: encrypt plaintext device PII at rest")
    parser.add_argument("--mongo-uri", help="MongoDB connection string (overrides MONGO_URI env var)")
    parser.add_argument("--key", help="VAULT_ENCRYPTION_KEY (64 hex chars; overrides env var)")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    args = parser.parse_args()

    db = _connect(args.mongo_uri)
    cipher = _cipher(args.key)

    # ── known_devices ─────────────────────────────────────────────────────────
    devices_migrated = 0
    for doc in db["known_devices"].find({"piiEncrypted": {"$ne": True}}):
        set_doc = _encrypt_fields(cipher, doc, DEVICE_FIELDS)
        if args.dry_run:
            print(f"  [dry-run] known_device {doc.get('_id')}: would encrypt {sorted(set(set_doc) - {'piiEncrypted'})}")
            devices_migrated += 1
            continue
        db["known_devices"].update_one({"_id": doc["_id"]}, {"$set": set_doc})
        devices_migrated += 1

    # ── webauthn_credentials ──────────────────────────────────────────────────
    creds_migrated = 0
    for doc in db["webauthn_credentials"].find({"piiEncrypted": {"$ne": True}}):
        set_doc = _encrypt_fields(cipher, doc, CRED_FIELDS)
        if args.dry_run:
            print(f"  [dry-run] webauthn_credential {doc.get('_id')}: would encrypt {sorted(set(set_doc) - {'piiEncrypted'})}")
            creds_migrated += 1
            continue
        db["webauthn_credentials"].update_one({"_id": doc["_id"]}, {"$set": set_doc})
        creds_migrated += 1

    if args.dry_run:
        print(f"Dry run complete. {devices_migrated} device(s) and {creds_migrated} credential(s) would be migrated.")
    else:
        print(f"Done. {devices_migrated} device(s) and {creds_migrated} credential(s) migrated.")


if __name__ == "__main__":
    main()
