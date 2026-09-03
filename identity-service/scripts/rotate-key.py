#!/usr/bin/env python3
"""
Kinlight — Encryption Key Rotation Script (F109)

Migrates all vault content, GridFS files, and encrypted user PII fields (F133)
from the old VAULT_ENCRYPTION_KEY to a new one. Uses the same AES-256-GCM
primitives as main.py.

Idempotent — marks each document with `encryptionKeyVersion` so re-runs skip
already-migrated data.

Usage:
    # Dry run — shows what would change without writing
    python3 rotate-key.py --old-key <64-char-hex> --new-key <64-char-hex>

    # Execute the rotation
    python3 rotate-key.py --old-key <64-char-hex> --new-key <64-char-hex> --execute

Required env vars:
    MONGO_URI — MongoDB Atlas connection string (same as the running server)

Requires:
    pip install pymongo cryptography  (already in requirements.txt)
"""

import argparse
import base64
import json
import os
import sys

from bson import ObjectId
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
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


def decrypt_vault_content(stored, cipher: AESGCM):
    """
    Decrypt vault content — mirrors main.py decrypt_content().
    Handles: dict (plaintext passthrough), str (encrypted), None (empty).
    """
    if stored is None:
        return {}
    if isinstance(stored, dict):
        return stored  # pre-encryption vault — passthrough
    raw = base64.b64decode(stored)
    nonce, ciphertext = raw[:12], raw[12:]
    plaintext = cipher.decrypt(nonce, ciphertext, None)
    return json.loads(plaintext.decode("utf-8"))


def encrypt_vault_content(content_dict: dict, cipher: AESGCM) -> str:
    """Encrypt vault content — mirrors main.py encrypt_content()."""
    plaintext = json.dumps(content_dict, separators=(",", ":")).encode("utf-8")
    nonce = os.urandom(12)
    ciphertext = cipher.encrypt(nonce, plaintext, None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_file_bytes(encrypted: bytes, cipher: AESGCM) -> bytes:
    """Decrypt file bytes — mirrors main.py decrypt_bytes()."""
    nonce, ciphertext = encrypted[:12], encrypted[12:]
    return cipher.decrypt(nonce, ciphertext, None)


def encrypt_file_bytes(plaintext: bytes, cipher: AESGCM) -> bytes:
    """Encrypt file bytes — mirrors main.py encrypt_bytes()."""
    nonce = os.urandom(12)
    ciphertext = cipher.encrypt(nonce, plaintext, None)
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

    mongo_uri = os.environ.get("MONGO_URI", "")
    if not mongo_uri:
        print(f"{RED}ERROR: MONGO_URI env var is not set.{NC}")
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
                encrypted = encrypt_vault_content(stored, new_cipher)
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
            content = decrypt_vault_content(stored, old_cipher)
        except Exception as e:
            print(f"  {RED}FAIL{NC} vault '{vid}': cannot decrypt — {e}")
            print(f"    This may mean old-key is wrong. Aborting.")
            sys.exit(1)

        if args.execute:
            new_blob = encrypt_vault_content(content, new_cipher)
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
                plaintext = decrypt_file_bytes(encrypted_data, old_cipher)
            except Exception as e:
                print(f"  {RED}FAIL{NC} file '{fid_str}': cannot decrypt — {e}")
                print(f"    This may mean old-key is wrong. Aborting.")
                sys.exit(1)

            if args.execute:
                new_data = encrypt_file_bytes(plaintext, new_cipher)
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
        if vault_updated > 0 or file_updated > 0 or user_updated > 0:
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
