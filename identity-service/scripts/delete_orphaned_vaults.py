#!/usr/bin/env python3
"""Delete orphaned vault data and drop the legacy plaintext `vault` field.

F141 cleanup. Two concerns:

  1. Orphaned data — documents whose `userId` references a user that no longer
     exists in the `users` collection (e.g. vaults left behind when tester
     accounts were removed). These are deleted, cascade-removing their GridFS
     files, push subscriptions, trusted links, known devices, passkeys, and
     password resets.

  2. Legacy plaintext blob — pre-F41 vault docs carried a top-level `vault`
     field. Any *remaining* vault (after orphan deletion) that still has a
     top-level `vault` key AND a present `content` gets that field `$unset`
     (safe: `content` is the encrypted source of truth).

Never touches `users` docs or any data belonging to a live user.

Run from identity-service/ (each line copyable on its own):
    python3 scripts/delete_orphaned_vaults.py                 # dry-run
    python3 scripts/delete_orphaned_vaults.py --delete --yes  # execute
On the GCE VM, secrets are self-fetched from Secret Manager automatically
(project ID discovered from the metadata server); no args needed.
"""

import argparse

from bson import ObjectId
from gridfs import GridFS

from _gcp_secrets import load_secrets
from _mongo import connect

_REFERENCE_COLLECTIONS = (
    "vaults",
    "push_subscriptions",
    "trusted_links",
    "known_devices",
    "webauthn_credentials",
    "password_resets",
)


def _orphaned_ids(db):
    """Return (valid_id_strings, sorted orphaned id strings).

    userId is stored as an ObjectId in the reference collections, and as its
    string form in `fs.files.metadata.userId`. Everything is normalized to the
    string form here so the same logical user is never counted twice.
    """
    valid = {u["_id"] for u in db["users"].find({})}
    valid_str = {str(i) for i in valid}
    orphaned = set()
    for col in _REFERENCE_COLLECTIONS:
        for doc in db[col].find({}):
            uid = doc.get("userId")
            if uid is not None and str(uid) not in valid_str:
                orphaned.add(str(uid))
    for fdoc in db["fs.files"].find({}):
        owner = (fdoc.get("metadata") or {}).get("userId")
        if owner is not None and owner not in valid_str:
            orphaned.add(owner)
    return valid_str, sorted(orphaned)


def _uid_filter(sid):
    """Match a userId regardless of whether it's stored as a string or ObjectId."""
    try:
        return {"$in": [sid, ObjectId(sid)]}
    except Exception:
        return sid


def _related_counts(db, sid):
    filt = _uid_filter(sid)
    return {
        "vault": db["vaults"].count_documents({"userId": filt}),
        "push_subs": db["push_subscriptions"].count_documents({"userId": filt}),
        "trusted_links": db["trusted_links"].count_documents({"userId": filt}),
        "known_devices": db["known_devices"].count_documents({"userId": filt}),
        "passkeys": db["webauthn_credentials"].count_documents({"userId": filt}),
        "resets": db["password_resets"].count_documents({"userId": filt}),
        "files": db["fs.files"].count_documents({"metadata.userId": filt}),
    }


def _delete_orphan(db, sid):
    filt = _uid_filter(sid)

    files_deleted = 0
    for fdoc in db["fs.files"].find({"metadata.userId": filt}):
        try:
            GridFS(db).delete(fdoc["_id"])
            files_deleted += 1
        except Exception:
            pass

    return {
        "vault": db["vaults"].delete_many({"userId": filt}).deleted_count,
        "push_subs": db["push_subscriptions"].delete_many({"userId": filt}).deleted_count,
        "trusted_links": db["trusted_links"].delete_many({"userId": filt}).deleted_count,
        "known_devices": db["known_devices"].delete_many({"userId": filt}).deleted_count,
        "passkeys": db["webauthn_credentials"].delete_many({"userId": filt}).deleted_count,
        "resets": db["password_resets"].delete_many({"userId": filt}).deleted_count,
        "files": files_deleted,
    }


def _legacy_vault_docs(db):
    return list(db["vaults"].find({"vault": {"$exists": True}}))


def _report_counts(db, orphaned):
    if not orphaned:
        print("No orphaned userIds found.")
        return
    print(f"Orphaned userIds: {len(orphaned)}")
    for sid in orphaned:
        c = _related_counts(db, sid)
        print(f"  - {sid}")
        print(f"    vault={c['vault']} push={c['push_subs']} trusted={c['trusted_links']} "
              f"devices={c['known_devices']} passkeys={c['passkeys']} resets={c['resets']} files={c['files']}")


def main():
    parser = argparse.ArgumentParser(description="Delete orphaned vault data + drop legacy `vault` field.")
    parser.add_argument("--delete", action="store_true", help="Actually delete (default is dry-run).")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation (required with --delete).")
    parser.add_argument("--gcp-project-id", help="GCP project ID — self-fetch secrets from Secret Manager")
    args = parser.parse_args()

    load_secrets(args.gcp_project_id, names=("MONGO_URI",))
    db = connect()

    valid_str, orphaned = _orphaned_ids(db)
    legacy = _legacy_vault_docs(db)

    print("=" * 70)
    print("Orphaned vault cleanup — F141")
    print("=" * 70)
    _report_counts(db, orphaned)

    print()
    print(f"Legacy `vault` field present on {len(legacy)} remaining vault doc(s):")
    for doc in legacy:
        has_content = "content" in doc
        print(f"  - {doc.get('_id')}  userId={doc.get('userId')}  content={'yes' if has_content else 'NO'}")
    if not legacy:
        print("  (none)")

    if not args.delete:
        print("\nNo changes made. To execute, run: --delete --yes")
        return

    if not args.yes:
        raise SystemExit("ERROR: --delete requires --yes to confirm.")

    # ── Phase A: delete orphaned data ─────────────────────────────────────────
    for sid in orphaned:
        r = _delete_orphan(db, sid)
        print(f"Deleted {sid}: vault={r['vault']} push={r['push_subs']} trusted={r['trusted_links']} "
              f"devices={r['known_devices']} passkeys={r['passkeys']} resets={r['resets']} files={r['files']}")

    # ── Phase B: unset legacy `vault` field on remaining vaults ──────────────
    unset_count = 0
    for doc in db["vaults"].find({"vault": {"$exists": True}}):
        if "content" in doc:
            db["vaults"].update_one({"_id": doc["_id"]}, {"$unset": {"vault": ""}})
            unset_count += 1
            print(f"  $unset legacy 'vault' on {doc.get('_id')}")
        else:
            print(f"  SKIP {doc.get('_id')}: has legacy 'vault' but no 'content' — left untouched")

    print(f"\nDone. {len(orphaned)} orphaned id(s) removed; {unset_count} legacy 'vault' field(s) unset.")


if __name__ == "__main__":
    main()

