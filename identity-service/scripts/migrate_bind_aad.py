#!/usr/bin/env python3
"""F134 — bind encrypted blobs to their owner via GCM AAD.

Before F134, `encrypt_content()` and `encrypt_bytes()` used `None` AAD, so a
vault `content` blob or GridFS file body could be swapped between users and
still decrypt under the single global key. F134 binds each blob to its owner by
using `AAD = str(userId)`.

This script force-migrates existing data now instead of waiting for each
record's next save:

  Vaults:      decrypt legacy `content` (None AAD) and re-encrypt with
               AAD = str(vault.userId). Also encrypts plaintext dict `content`
               (pre-F04 records) with AAD in the same pass.
  GridFS files: decrypt legacy bytes (None AAD) and re-encrypt with
               AAD = str(metadata.userId).

Run from identity-service/:
    source .venv/bin/activate
    MONGO_URI="mongodb+srv://..." VAULT_ENCRYPTION_KEY="..." \
        python3 scripts/migrate_bind_aad.py --dry-run
    MONGO_URI="mongodb+srv://..." VAULT_ENCRYPTION_KEY="..." \
        python3 scripts/migrate_bind_aad.py
    # skip GridFS files:
    MONGO_URI="mongodb+srv://..." VAULT_ENCRYPTION_KEY="..." \
        python3 scripts/migrate_bind_aad.py --skip-files
"""

import argparse

from gridfs import GridFS

from _gcp_secrets import load_secrets
from _mongo import connect, decrypt_bytes, decrypt_content, encrypt_bytes, encrypt_content, get_cipher


def main():
    parser = argparse.ArgumentParser(description="F134: bind encrypted blobs to their owner via AAD")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    parser.add_argument("--skip-files", action="store_true", help="Only migrate vault content, skip GridFS files")
    parser.add_argument("--gcp-project-id", help="GCP project ID — self-fetch secrets from Secret Manager")
    args = parser.parse_args()

    load_secrets(args.gcp_project_id, names=("MONGO_URI", "VAULT_ENCRYPTION_KEY"))

    db = connect()
    cipher = get_cipher()

    # ── vault content ─────────────────────────────────────────────────────────
    vaults_migrated = 0
    vaults_skipped = 0
    for doc in db["vaults"].find({}):
        uid = doc.get("userId")
        stored = doc.get("content")
        if not uid or stored is None:
            vaults_skipped += 1
            continue
        try:
            content = decrypt_content(cipher, stored, str(uid))
        except Exception as e:
            print(f"  WARNING: vault {uid} failed to decrypt — skipping ({e})")
            vaults_skipped += 1
            continue
        if not isinstance(content, dict):
            vaults_skipped += 1
            continue
        if args.dry_run:
            print(f"  [dry-run] vault {uid}: would re-encrypt content with AAD")
            vaults_migrated += 1
            continue
        db["vaults"].update_one(
            {"_id": doc["_id"]},
            {"$set": {"content": encrypt_content(cipher, content, str(uid))}},
        )
        vaults_migrated += 1

    # ── GridFS files ──────────────────────────────────────────────────────────
    files_migrated = 0
    files_skipped = 0
    files_failed = 0

    if not args.skip_files:
        fs = GridFS(db)
        for fdoc in db["fs.files"].find({}):
            fid = fdoc["_id"]
            owner = (fdoc.get("metadata") or {}).get("userId")
            if not owner:
                files_skipped += 1
                continue
            try:
                grid_out = fs.get(fid)
                encrypted_data = grid_out.read()
            except Exception as e:
                print(f"  WARNING: file {fid} cannot read — skipping ({e})")
                files_failed += 1
                continue
            try:
                plaintext = decrypt_bytes(cipher, encrypted_data, str(owner))
            except Exception as e:
                print(f"  WARNING: file {fid} failed to decrypt — skipping ({e})")
                files_failed += 1
                continue
            if args.dry_run:
                print(f"  [dry-run] file {fid}: would re-encrypt bytes with AAD")
                files_migrated += 1
                continue
            new_data = encrypt_bytes(cipher, plaintext, str(owner))
            metadata = fdoc.get("metadata", {}) or {}
            fs.delete(fid)
            fs.put(
                new_data,
                _id=fid,
                filename=fdoc.get("filename", ""),
                contentType=fdoc.get("contentType", ""),
                metadata=metadata,
                uploadDate=fdoc.get("uploadDate"),
            )
            files_migrated += 1

    if args.dry_run:
        print(f"Dry run complete. {vaults_migrated} vault(s) and {files_migrated} file(s) would be migrated.")
    else:
        print(f"Done. Vaults: {vaults_migrated} migrated, {vaults_skipped} skipped. "
              f"Files: {files_migrated} migrated, {files_skipped} skipped, {files_failed} failed.")


if __name__ == "__main__":
    main()
