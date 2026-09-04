#!/usr/bin/env python3
"""F131 — migrate legacy plaintext `log` field into encrypted `content`.

Before F131, the vault activity log was stored as a top-level plaintext field
on the vault document. F131 moves it inside the encrypted `content` dict (so it
is covered by F04 AES-256-GCM). This script force-migrates existing vaults now
instead of waiting for each user's next app save (which also unsets the legacy
field via `vault_sync`).

For every vault doc that still has a top-level `log`:
  1. decrypt `content`
  2. move `log` into the decrypted content (capped at 20)
  3. re-encrypt `content` and `$unset` the top-level `log`

Run from identity-service/:
    source .venv/bin/activate
    MONGO_URI="mongodb+srv://..." VAULT_ENCRYPTION_KEY="..." \
        python3 scripts/migrate_log_to_content.py --dry-run
    MONGO_URI="mongodb+srv://..." VAULT_ENCRYPTION_KEY="..." \
        python3 scripts/migrate_log_to_content.py
"""

import argparse

from _gcp_secrets import load_secrets
from _mongo import connect, decrypt_content, encrypt_content, get_cipher


def main():
    parser = argparse.ArgumentParser(description="F131: migrate plaintext log into encrypted content")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    parser.add_argument("--gcp-project-id", help="GCP project ID — self-fetch secrets from Secret Manager")
    args = parser.parse_args()

    load_secrets(args.gcp_project_id, names=("MONGO_URI", "VAULT_ENCRYPTION_KEY"))

    db = connect()
    cipher = get_cipher()
    vaults = db["vaults"]

    migrated = 0
    skipped = 0
    for doc in vaults.find({"log": {"$exists": True}}):
        oid = doc["_id"]
        log = doc.get("log") or []
        try:
            content = decrypt_content(cipher, doc.get("content"))
        except Exception as exc:
            print(f"  WARNING: vault {oid} failed to decrypt — skipping ({exc})")
            skipped += 1
            continue
        if not isinstance(content, dict):
            print(f"  WARNING: vault {oid} has non-dict content — skipping")
            skipped += 1
            continue

        new_content = content if "log" in content else {**content, "log": log[-20:]}

        if args.dry_run:
            print(f"  [dry-run] vault {oid}: would move {len(log)} log entries into content")
            continue

        vaults.update_one(
            {"_id": oid},
            {"$set": {"content": encrypt_content(cipher, new_content)},
             "$unset": {"log": ""}},
        )
        migrated += 1

    if args.dry_run:
        print(f"Dry run complete. {migrated} vault(s) would be migrated, {skipped} skipped.")
    else:
        print(f"Done. {migrated} vault(s) migrated, {skipped} skipped.")


if __name__ == "__main__":
    main()
