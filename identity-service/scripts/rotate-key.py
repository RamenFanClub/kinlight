#!/usr/bin/env python3
"""
Kinlight — Encryption Key Rotation Script (F109)

Migrates all vault content, GridFS files, encrypted user PII (F133), device PII
(F137), and push subscriptions (F138) from the old VAULT_ENCRYPTION_KEY to a new
one. Uses the same AES-256-GCM primitives as main.py.

Idempotent — marks each document with `encryptionKeyVersion` so re-runs skip
already-migrated data.

Usage (each line copyable on its own):
    # Dry run — shows what would change without writing
    python3 rotate-key.py --mongo-uri "mongodb+srv://..." --old-key <64-char-hex> --new-key <64-char-hex>

    # Execute the rotation
    python3 rotate-key.py --mongo-uri "mongodb+srv://..." --old-key <64-char-hex> --new-key <64-char-hex> --execute

MONGO_URI env var is used when --mongo-uri is omitted.

Requires:
    pip install pymongo cryptography  (already in requirements.txt)
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys

from bson import ObjectId
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from gridfs import GridFS
from pymongo import MongoClient

# ── helpers ───────────────────────────────────────────────────────────────────

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
NC = "\033[0m"


def _cipher(key_hex: str) -> AESGCM:
    """Return an AESGCM cipher from a 64-char hex key."""
    if len(key_hex) != 64:
        raise ValueError(f"Key must be 64 hex chars, got {len(key_hex)}")
    return AESGCM(bytes.fromhex(key_hex))


def decrypt_vault_content(stored, cipher: AESGCM, user_id: str = None):
    """
    Decrypt vault content — mirrors main.py decrypt_content().
    Handles: dict (plaintext passthrough), str (encrypted), None (empty).
    F134: tries AAD=user_id, falling back to legacy None AAD on InvalidTag.
    """
    if stored is None:
        return {}
    if isinstance(stored, dict):
        return stored  # pre-encryption vault — passthrough
    raw = base64.b64decode(stored)
    nonce, ciphertext = raw[:12], raw[12:]
    if user_id:
        try:
            plaintext = cipher.decrypt(nonce, ciphertext, user_id.encode("utf-8"))
        except InvalidTag:
            plaintext = cipher.decrypt(nonce, ciphertext, None)
    else:
        plaintext = cipher.decrypt(nonce, ciphertext, None)
    return json.loads(plaintext.decode("utf-8"))


def encrypt_vault_content(content_dict: dict, cipher: AESGCM, user_id: str = None) -> str:
    """Encrypt vault content — mirrors main.py encrypt_content() (F134 AAD)."""
    plaintext = json.dumps(content_dict, separators=(",", ":")).encode("utf-8")
    nonce = os.urandom(12)
    aad = user_id.encode("utf-8") if user_id else None
    ciphertext = cipher.encrypt(nonce, plaintext, aad)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_file_bytes(encrypted: bytes, cipher: AESGCM, user_id: str = None) -> bytes:
    """Decrypt file bytes — mirrors main.py decrypt_bytes() (F134 AAD)."""
    nonce, ciphertext = encrypted[:12], encrypted[12:]
    if user_id:
        try:
            return cipher.decrypt(nonce, ciphertext, user_id.encode("utf-8"))
        except InvalidTag:
            return cipher.decrypt(nonce, ciphertext, None)
    return cipher.decrypt(nonce, ciphertext, None)


def encrypt_file_bytes(plaintext: bytes, cipher: AESGCM, user_id: str = None) -> bytes:
    """Encrypt file bytes — mirrors main.py encrypt_bytes() (F134 AAD)."""
    nonce = os.urandom(12)
    aad = user_id.encode("utf-8") if user_id else None
    ciphertext = cipher.encrypt(nonce, plaintext, aad)
    return nonce + ciphertext


def decrypt_string(encrypted: str, cipher: AESGCM) -> str:
    """Decrypt a base64 string — mirrors main.py _decrypt_string() (F132)."""
    raw = base64.b64decode(encrypted)
    return cipher.decrypt(raw[:12], raw[12:], None).decode("utf-8")


def encrypt_string(plaintext: str, cipher: AESGCM) -> str:
    """Encrypt a string → base64 — mirrors main.py _encrypt_string() (F132)."""
    nonce = os.urandom(12)
    ciphertext = cipher.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def endpoint_blind_index(endpoint: str, key_hex: str) -> str:
    """F138: keyed HMAC-SHA256 blind index of a push endpoint."""
    key = bytes.fromhex(key_hex)
    subkey = hmac.new(key, b"kinlight:push-endpoint:v1", hashlib.sha256).digest()
    return hmac.new(subkey, endpoint.encode("utf-8"), hashlib.sha256).hexdigest()


# ── main ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Rotate the vault encryption key across all user data."
    )
    parser.add_argument(
        "--old-key", required=True, metavar="HEX",
        help="Current (old) VAULT_ENCRYPTION_KEY to decrypt with.",
    )
    parser.add_argument(
        "--new-key", required=True, metavar="HEX",
        help="New VAULT_ENCRYPTION_KEY to encrypt with.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually write changes. Without this flag, dry-run only.",
    )
    parser.add_argument(
        "--skip-files",
        action="store_true",
        help="Only rotate vault content, skip GridFS files.",
    )
    parser.add_argument(
        "--target-version",
        type=int,
        default=1,
        help="encryptionKeyVersion to set on migrated documents (default: 1).",
    )
    parser.add_argument(
        "--mongo-uri",
        help="MongoDB connection string (overrides MONGO_URI env var).",
    )
    args = parser.parse_args()

    # ── validate keys ─────────────────────────────────────────────────────────

    for label, key in [("old-key", args.old_key), ("new-key", args.new_key)]:
        if len(key) != 64 or not all(c in "0123456789abcdefABCDEF" for c in key):
            print(f"{RED}ERROR: {label} must be 64 hex characters.{NC}")
            sys.exit(1)

    if args.old_key == args.new_key:
        print(f"{RED}ERROR: old-key and new-key are identical — nothing to do.{NC}")
        sys.exit(1)

    # ── connect to MongoDB ────────────────────────────────────────────────────

    mongo_uri = args.mongo_uri or os.environ.get("MONGO_URI", "")
    if not mongo_uri:
        print(f"{RED}ERROR: MONGO_URI not set (use --mongo-uri or MONGO_URI env var).{NC}")
        sys.exit(1)

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
    try:
        client.admin.command("ping")
    except Exception as e:
        print(f"{RED}ERROR: Cannot connect to MongoDB: {e}{NC}")
        sys.exit(1)

    db_name = mongo_uri.rsplit("/", 1)[-1].split("?")[0]
    db = client[db_name]

    old_cipher = _cipher(args.old_key)
    new_cipher = _cipher(args.new_key)

    mode = f"{GREEN}EXECUTE{NC}" if args.execute else f"{YELLOW}DRY RUN{NC}"
    print(f"\n{BOLD}Kinlight — Encryption Key Rotation{NC}  [{mode}]")
    print(f"{'─' * 60}")
    print(f"  Database:  {BOLD}{db_name}{NC}")
    print(f"  Version:   {BOLD}→ {args.target_version}{NC}")
    print(f"  Vaults:    yes")
    print(f"  Files:     {'no (--skip-files)' if args.skip_files else 'yes'}")
    print()

    # ── vaults ────────────────────────────────────────────────────────────────

    vaults = list(db.vaults.find({}))
    vault_updated = 0
    vault_skipped = 0
    vault_plaintext = 0

    for doc in vaults:
        vid = doc.get("userId", doc.get("_id", "?"))
        user_id = str(doc["userId"]) if doc.get("userId") else None
        current_version = doc.get("encryptionKeyVersion", 0)

        if current_version >= args.target_version:
            vault_skipped += 1
            continue

        stored = doc.get("content")
        if stored is None:
            vault_skipped += 1
            continue

        if isinstance(stored, dict):
            # Pre-encryption vault — just encrypt it with the new key
            vault_plaintext += 1
            if args.execute:
                encrypted = encrypt_vault_content(stored, new_cipher, user_id)
                db.vaults.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {
                        "content": encrypted,
                        "encryptionKeyVersion": args.target_version,
                    }},
                )
            continue

        # Encrypted vault — decrypt with old, re-encrypt with new
        try:
            content = decrypt_vault_content(stored, old_cipher, user_id)
        except Exception as e:
            print(f"  {RED}FAIL{NC} vault '{vid}': cannot decrypt — {e}")
            print(f"    This may mean old-key is wrong. Aborting.")
            sys.exit(1)

        if args.execute:
            new_blob = encrypt_vault_content(content, new_cipher, user_id)
            db.vaults.update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "content": new_blob,
                    "encryptionKeyVersion": args.target_version,
                }},
            )

        vault_updated += 1

    file_updated = 0
    file_skipped = 0
    file_failed = 0

    # ── GridFS files ──────────────────────────────────────────────────────────

    if not args.skip_files:
        fs = GridFS(db)
        file_docs = list(db["fs.files"].find({}))

        for fdoc in file_docs:
            fid = fdoc["_id"]
            fid_str = str(fid)
            owner = (fdoc.get("metadata") or {}).get("userId")
            owner_str = str(owner) if owner else None
            current_version = fdoc.get("metadata", {}).get("encryptionKeyVersion", 0)

            if current_version >= args.target_version:
                file_skipped += 1
                continue

            try:
                grid_out = fs.get(fid)
                encrypted_data = grid_out.read()
            except Exception as e:
                print(f"  {RED}FAIL{NC} file '{fid_str}': cannot read — {e}")
                file_failed += 1
                continue

            try:
                plaintext = decrypt_file_bytes(encrypted_data, old_cipher, owner_str)
            except Exception as e:
                print(f"  {RED}FAIL{NC} file '{fid_str}': cannot decrypt — {e}")
                print(f"    This may mean old-key is wrong. Aborting.")
                sys.exit(1)

            if args.execute:
                new_data = encrypt_file_bytes(plaintext, new_cipher, owner_str)
                metadata = fdoc.get("metadata", {}) or {}
                metadata["encryptionKeyVersion"] = args.target_version

                # F132: filenames are encrypted at rest — re-encrypt under the new key.
                # Legacy plaintext filenames are encrypted here as well, closing the gap.
                metadata.pop("filename", None)
                filename_encrypted = bool(metadata.get("filenameEncrypted"))
                raw_name = fdoc.get("filename", "")
                if filename_encrypted:
                    try:
                        plain_name = decrypt_string(raw_name, old_cipher)
                    except Exception as e:
                        print(f"  {RED}FAIL{NC} file '{fid_str}': cannot decrypt filename — {e}")
                        file_failed += 1
                        continue
                else:
                    plain_name = raw_name or "file"
                new_name = encrypt_string(plain_name, new_cipher)
                metadata["filenameEncrypted"] = True

                # Replace: delete old gridFS entry, upload new with same metadata
                fs.delete(fid)
                fs.put(
                    new_data,
                    _id=fid,
                    filename=new_name,
                    contentType=fdoc.get("contentType", ""),
                    metadata=metadata,
                    uploadDate=fdoc.get("uploadDate"),
                )

            file_updated += 1

    # ── user PII (F133) ──────────────────────────────────────────────────────

    user_updated = 0
    user_skipped = 0
    user_plaintext = 0

    user_docs = list(db.users.find({}))
    for udoc in user_docs:
        uid = udoc.get("_id")
        current_version = udoc.get("encryptionKeyVersion", 0)

        if current_version >= args.target_version:
            user_skipped += 1
            continue

        if not udoc.get("piiEncrypted"):
            # Legacy plaintext PII — handled by scripts/migrate_encrypt_user_pii.py.
            user_plaintext += 1
            continue

        set_doc = {}
        for field in ("name", "ageGroup", "notes"):
            value = udoc.get(field)
            if isinstance(value, str) and value:
                try:
                    plain_value = decrypt_string(value, old_cipher)
                except Exception as e:
                    print(f"  {RED}FAIL{NC} user '{uid}': cannot decrypt {field} — {e}")
                    print(f"    This may mean old-key is wrong. Aborting.")
                    sys.exit(1)
                set_doc[field] = encrypt_string(plain_value, new_cipher)

        if args.execute:
            set_doc["encryptionKeyVersion"] = args.target_version
            db.users.update_one({"_id": uid}, {"$set": set_doc})

        user_updated += 1

    # ── device PII (F137) ────────────────────────────────────────────────────

    device_updated = 0
    device_skipped = 0
    device_plaintext = 0
    cred_updated = 0

    for ddoc in db["known_devices"].find({}):
        did = ddoc.get("_id")
        current_version = ddoc.get("encryptionKeyVersion", 0)
        if current_version >= args.target_version:
            device_skipped += 1
            continue
        if not ddoc.get("piiEncrypted"):
            device_plaintext += 1
            continue
        set_doc = {}
        for field in ("deviceName", "userAgent", "ip"):
            value = ddoc.get(field)
            if isinstance(value, str) and value:
                try:
                    set_doc[field] = encrypt_string(decrypt_string(value, old_cipher), new_cipher)
                except Exception as e:
                    print(f"  {RED}FAIL{NC} known_device '{did}': cannot decrypt {field} — {e}")
                    sys.exit(1)
        if args.execute:
            set_doc["encryptionKeyVersion"] = args.target_version
            db["known_devices"].update_one({"_id": did}, {"$set": set_doc})
        device_updated += 1

    for cdoc in db["webauthn_credentials"].find({}):
        cid = cdoc.get("_id")
        current_version = cdoc.get("encryptionKeyVersion", 0)
        if current_version >= args.target_version:
            continue
        if not cdoc.get("piiEncrypted"):
            continue
        value = cdoc.get("deviceName")
        if isinstance(value, str) and value:
            try:
                new_value = encrypt_string(decrypt_string(value, old_cipher), new_cipher)
            except Exception as e:
                print(f"  {RED}FAIL{NC} webauthn_credential '{cid}': cannot decrypt deviceName — {e}")
                sys.exit(1)
            if args.execute:
                db["webauthn_credentials"].update_one(
                    {"_id": cid},
                    {"$set": {"deviceName": new_value, "encryptionKeyVersion": args.target_version}},
                )
            cred_updated += 1

    # ── push subscriptions (F138) ────────────────────────────────────────────

    push_updated = 0
    push_skipped = 0
    push_plaintext = 0

    for pdoc in db["push_subscriptions"].find({}):
        pid = pdoc.get("_id")
        current_version = pdoc.get("encryptionKeyVersion", 0)
        if current_version >= args.target_version:
            push_skipped += 1
            continue
        if not pdoc.get("subscriptionEncrypted"):
            push_plaintext += 1
            continue
        try:
            subscription = json.loads(decrypt_string(pdoc["subscriptionEnc"], old_cipher))
            new_blob = encrypt_string(json.dumps(subscription, separators=(",", ":")), new_cipher)
            new_hash = endpoint_blind_index(subscription["endpoint"], args.new_key)
        except Exception as e:
            print(f"  {RED}FAIL{NC} push_subscription '{pid}': cannot decrypt — {e}")
            sys.exit(1)
        if args.execute:
            db["push_subscriptions"].update_one(
                {"_id": pid},
                {"$set": {
                    "subscriptionEnc": new_blob,
                    "endpointHash": new_hash,
                    "encryptionKeyVersion": args.target_version,
                }},
            )
        push_updated += 1

    # ── summary ───────────────────────────────────────────────────────────────

    print(f"{BOLD}Summary:{NC}")
    print(f"  Vaults processed:     {len(vaults)}")
    print(f"  Vaults rotated:       {vault_updated}")
    if vault_plaintext:
        print(f"    (plaintext→encrypt: {vault_plaintext})")
    print(f"  Vaults skipped:       {vault_skipped}  (already at version {args.target_version} or no content)")

    print(f"  Users processed:      {len(user_docs)}")
    print(f"  Users rotated:        {user_updated}")
    if user_plaintext:
        print(f"    (legacy plaintext, left for migration: {user_plaintext})")
    print(f"  Users skipped:        {user_skipped}  (already at version {args.target_version})")

    print(f"  Devices rotated:      {device_updated}")
    if device_plaintext:
        print(f"    (legacy plaintext, left for migration: {device_plaintext})")
    print(f"  Credentials rotated:  {cred_updated}")
    print(f"  Push subs rotated:    {push_updated}")
    if push_plaintext:
        print(f"    (legacy plaintext, left for migration: {push_plaintext})")

    if not args.skip_files:
        print(f"  Files processed:      {len(file_docs)}")
        print(f"  Files rotated:        {file_updated}")
        print(f"  Files skipped:        {file_skipped}  (already at version {args.target_version})")
        if file_failed:
            print(f"  {RED}Files FAILED:         {file_failed}{NC}")

    if not args.execute:
        print(f"\n{YELLOW}DRY RUN — no changes written.{NC}")
        print(f"Run with {BOLD}--execute{NC} to apply the rotation.")
    else:
        print(f"\n{GREEN}Rotation complete.{NC}")
        if (vault_updated > 0 or file_updated > 0 or user_updated > 0
                or device_updated > 0 or cred_updated > 0 or push_updated > 0):
            print(f"{BOLD}Next steps:{NC}")
            print(f"  1. Update VAULT_ENCRYPTION_KEY to the new key in ALL locations:")
            print(f"     — GCE VM Docker env (docker run -e VAULT_ENCRYPTION_KEY=...)")
            print(f"     — GitHub Secrets (Settings > Secrets > VAULT_ENCRYPTION_KEY)")
            print(f"     — Your .env file (if any)")
            print(f"  2. Restart the server: docker stop kinlight-app && docker rm kinlight-app && docker run ...")
            print(f"  3. Run a new backup:   ./scripts/backup-key.sh")
            print(f"  4. Verify:              ./scripts/restore-key.sh --asc vault-key-*.asc --verify")
        print(f"{YELLOW}IMPORTANT: Do NOT update env vars until AFTER rotation completes.{NC}")
        print(f"           The old key is still needed for any un-migrated data.")


if __name__ == "__main__":
    main()
