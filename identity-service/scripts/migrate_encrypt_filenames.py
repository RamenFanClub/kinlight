#!/usr/bin/env python3
"""F132 — migrate GridFS filenames from plaintext to encrypted-at-rest.

Before F132, GridFS stored the uploaded document name in plaintext in both
`fs.files.filename` and `fs.files.metadata.filename`. F132 encrypts the filename
(AES-256-GCM via the existing VAULT_ENCRYPTION_KEY) so a Mongo-only attacker can
no longer read document names like "Will_Final.pdf".

For every fs.files doc that has not yet been migrated (`metadata.filenameEncrypted`
absent/false):
  1. read the plaintext name from `filename` (falling back to `metadata.filename`)
  2. encrypt it
  3. $set filename=encrypted, metadata.filenameEncrypted=True, $unset metadata.filename

Run from identity-service/:
    source .venv/bin/activate
    MONGO_URI="mongodb+srv://..." VAULT_ENCRYPTION_KEY="..." \
        python3 scripts/migrate_encrypt_filenames.py --dry-run
    MONGO_URI="mongodb+srv://..." VAULT_ENCRYPTION_KEY="..." \
        python3 scripts/migrate_encrypt_filenames.py
"""

import argparse

from _gcp_secrets import load_secrets
from _mongo import connect, encrypt_string, get_cipher


def main():
    parser = argparse.ArgumentParser(description="F132: encrypt plaintext GridFS filenames at rest")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    parser.add_argument("--gcp-project-id", help="GCP project ID — self-fetch secrets from Secret Manager")
    args = parser.parse_args()

    load_secrets(args.gcp_project_id, names=("MONGO_URI", "VAULT_ENCRYPTION_KEY"))

    db = connect()
    cipher = get_cipher()
    files = db["fs.files"]

    migrated = 0
    skipped = 0
    for doc in files.find({"metadata.filenameEncrypted": {"$ne": True}}):
        fid = doc["_id"]
        plain_name = doc.get("filename") or (doc.get("metadata") or {}).get("filename") or "file"

        if args.dry_run:
            print(f"  [dry-run] file {fid}: would encrypt filename {plain_name!r}")
            migrated += 1
            continue

        files.update_one(
            {"_id": fid},
            {
                "$set": {
                    "filename": encrypt_string(cipher, plain_name),
                    "metadata.filenameEncrypted": True,
                },
                "$unset": {"metadata.filename": ""},
            },
        )
        migrated += 1

    if args.dry_run:
        print(f"Dry run complete. {migrated} file(s) would be migrated, {skipped} skipped.")
    else:
        print(f"Done. {migrated} file(s) migrated, {skipped} skipped.")


if __name__ == "__main__":
    main()
