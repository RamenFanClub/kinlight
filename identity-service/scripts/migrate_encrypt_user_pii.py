#!/usr/bin/env python3
"""F133 — migrate plaintext user PII fields to encrypted-at-rest.

Before F133, the `users` collection stored `name`, `ageGroup`, and `notes` as
plaintext PII. F133 encrypts these fields (AES-256-GCM via the existing
VAULT_ENCRYPTION_KEY) so a Mongo-only attacker can no longer read them.

`username` and `email` are intentionally NOT touched here — email is the login
identifier and delivery target, handled separately by F139 (blind index).

For every user doc lacking `piiEncrypted`:
  1. encrypt any non-empty `name`, `ageGroup`, `notes` value
  2. $set piiEncrypted=True

Run from identity-service/:
    source .venv/bin/activate
    MONGO_URI="mongodb+srv://..." VAULT_ENCRYPTION_KEY="..." \
        python3 scripts/migrate_encrypt_user_pii.py --dry-run
    MONGO_URI="mongodb+srv://..." VAULT_ENCRYPTION_KEY="..." \
        python3 scripts/migrate_encrypt_user_pii.py
"""

import argparse

from _gcp_secrets import load_secrets
from _mongo import connect, encrypt_string, get_cipher

PII_FIELDS = ("name", "ageGroup", "notes")


def main():
    parser = argparse.ArgumentParser(description="F133: encrypt plaintext user PII fields at rest")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    parser.add_argument("--gcp-project-id", help="GCP project ID — self-fetch secrets from Secret Manager")
    args = parser.parse_args()

    load_secrets(args.gcp_project_id, names=("MONGO_URI", "VAULT_ENCRYPTION_KEY"))

    db = connect()
    cipher = get_cipher()
    users = db["users"]

    migrated = 0
    skipped = 0
    for doc in users.find({"piiEncrypted": {"$ne": True}}):
        uid = doc.get("_id")
        email = doc.get("email", "?")
        set_doc = {}
        for field in PII_FIELDS:
            value = doc.get(field)
            if isinstance(value, str) and value:
                set_doc[field] = encrypt_string(cipher, value)
        set_doc["piiEncrypted"] = True

        if args.dry_run:
            print(f"  [dry-run] user {uid} ({email}): would encrypt {sorted(set(set_doc) - {'piiEncrypted'})}")
            migrated += 1
            continue

        users.update_one({"_id": uid}, {"$set": set_doc})
        migrated += 1

    if args.dry_run:
        print(f"Dry run complete. {migrated} user(s) would be migrated, {skipped} skipped.")
    else:
        print(f"Done. {migrated} user(s) migrated, {skipped} skipped.")


if __name__ == "__main__":
    main()
