#!/usr/bin/env python3
"""F138 — migrate plaintext push subscriptions to encrypted-at-rest.

Before F138, `push_subscriptions` stored the full `subscription` object
(`{endpoint, keys:{p256dh, auth}}`) in plaintext — a Mongo-only attacker could
use it to send phishing pushes to the holder. F138 encrypts the whole blob and
adds a keyed `endpointHash` (HMAC-SHA256) blind index so subscribe-dedupe and
unsubscribe-by-endpoint still work without storing the endpoint in plaintext.

For every push_subscriptions doc lacking `subscriptionEncrypted`:
  1. encrypt the `subscription` JSON → `subscriptionEnc`
  2. compute `endpointHash` from `subscription.endpoint`
  3. $set the encrypted blob + hash + flag, $unset the plaintext `subscription`

Run from identity-service/ (each line copyable on its own):
    source .venv/bin/activate
    python3 scripts/migrate_encrypt_push_subs.py --mongo-uri "mongodb+srv://..." --key "..." --dry-run
    python3 scripts/migrate_encrypt_push_subs.py --mongo-uri "mongodb+srv://..." --key "..."
Or via env vars (MONGO_URI / VAULT_ENCRYPTION_KEY) as before.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pymongo import MongoClient

DB_NAME = "emergency_exit"


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


def _endpoint_blind_index(endpoint: str, key_hex: str) -> str:
    key = bytes.fromhex(key_hex)
    subkey = hmac.new(key, b"kinlight:push-endpoint:v1", hashlib.sha256).digest()
    return hmac.new(subkey, endpoint.encode("utf-8"), hashlib.sha256).hexdigest()


def main():
    parser = argparse.ArgumentParser(description="F138: encrypt push subscriptions at rest")
    parser.add_argument("--mongo-uri", help="MongoDB connection string (overrides MONGO_URI env var)")
    parser.add_argument("--key", help="VAULT_ENCRYPTION_KEY (64 hex chars; overrides env var)")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    args = parser.parse_args()

    db = _connect(args.mongo_uri)
    cipher = _cipher(args.key)
    key_hex = args.key or os.environ.get("VAULT_ENCRYPTION_KEY", "")

    migrated = 0
    skipped = 0
    for doc in db["push_subscriptions"].find({"subscriptionEncrypted": {"$ne": True}}):
        subscription = doc.get("subscription")
        if not isinstance(subscription, dict) or not subscription.get("endpoint"):
            print(f"  WARNING: push sub {doc.get('_id')} has no subscription.endpoint — skipping")
            skipped += 1
            continue

        set_doc = {
            "subscriptionEnc": _encrypt_string(cipher, json.dumps(subscription, separators=(",", ":"))),
            "subscriptionEncrypted": True,
            "endpointHash": _endpoint_blind_index(subscription["endpoint"], key_hex),
        }

        if args.dry_run:
            print(f"  [dry-run] push sub {doc.get('_id')}: would encrypt subscription + endpointHash")
            migrated += 1
            continue

        db["push_subscriptions"].update_one(
            {"_id": doc["_id"]},
            {"$set": set_doc, "$unset": {"subscription": ""}},
        )
        migrated += 1

    if args.dry_run:
        print(f"Dry run complete. {migrated} subscription(s) would be migrated, {skipped} skipped.")
    else:
        print(f"Done. {migrated} subscription(s) migrated, {skipped} skipped.")


if __name__ == "__main__":
    main()
