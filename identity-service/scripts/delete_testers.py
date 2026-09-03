#!/usr/bin/env python3
"""Delete tester accounts and all their related data (F80 consolidation).

Consolidates the tester pool to a single account. Deletes every
`isTester: true` user except the keeper, and cascade-removes their vault,
push subscriptions, trusted links, known devices, passkeys, password resets,
and GridFS files.

Never touches `isAdmin: true` accounts (e.g. the `anggi` admin).

Usage (from identity-service/):
    source .venv/bin/activate
    MONGO_URI="mongodb+srv://..." python3 scripts/delete_testers.py --dry-run
    MONGO_URI="mongodb+srv://..." python3 scripts/delete_testers.py --delete --yes --keep <email>
"""

import argparse
import os

from gridfs import GridFS
from pymongo import MongoClient

DB_NAME = "emergency_exit"


def _connect():
    mongo_uri = os.environ.get("MONGO_URI")
    if not mongo_uri:
        raise SystemExit("ERROR: MONGO_URI environment variable not set.")
    client = MongoClient(mongo_uri)
    return client, client[DB_NAME]


def _related_counts(db, user_id):
    oid_str = str(user_id)
    return {
        "vault": db["vaults"].count_documents({"userId": user_id}),
        "push_subs": db["push_subscriptions"].count_documents({"userId": user_id}),
        "trusted_links": db["trusted_links"].count_documents({"userId": user_id}),
        "known_devices": db["known_devices"].count_documents({"userId": user_id}),
        "passkeys": db["webauthn_credentials"].count_documents({"userId": user_id}),
        "resets": db["password_resets"].count_documents({"userId": user_id}),
        "files": db["fs.files"].count_documents({"metadata.userId": oid_str}),
    }


def _dry_run(db):
    print("=== Accounts in 'users' ===")
    for u in db["users"].find({}).sort("email", 1):
        flags = []
        if u.get("isAdmin"):
            flags.append("ADMIN")
        if u.get("isTester"):
            flags.append("TESTER")
        flag_s = f" [{', '.join(flags)}]" if flags else ""
        c = _related_counts(db, u["_id"])
        print(f"- {u.get('email', '(no email)')}  name={u.get('name', '?')!r}{flag_s}")
        print(f"    vault={c['vault']} push={c['push_subs']} trusted={c['trusted_links']} "
              f"devices={c['known_devices']} passkeys={c['passkeys']} resets={c['resets']} files={c['files']}")
    print("\nNo changes made. To delete, run: --delete --yes --keep <email>")


def _delete_user(db, u):
    uid = u["_id"]
    oid_str = str(uid)
    email = u.get("email", "?")

    files_deleted = 0
    for fdoc in db["fs.files"].find({"metadata.userId": oid_str}):
        try:
            GridFS(db).delete(fdoc["_id"])
            files_deleted += 1
        except Exception:
            pass

    results = {
        "vault": db["vaults"].delete_many({"userId": uid}).deleted_count,
        "push_subs": db["push_subscriptions"].delete_many({"userId": uid}).deleted_count,
        "trusted_links": db["trusted_links"].delete_many({"userId": uid}).deleted_count,
        "known_devices": db["known_devices"].delete_many({"userId": uid}).deleted_count,
        "passkeys": db["webauthn_credentials"].delete_many({"userId": uid}).deleted_count,
        "resets": db["password_resets"].delete_many({"userId": uid}).deleted_count,
        "files": files_deleted,
        "user": db["users"].delete_one({"_id": uid}).deleted_count,
    }
    return email, results


def main():
    parser = argparse.ArgumentParser(description="Delete tester accounts (F80 consolidation).")
    parser.add_argument("--delete", action="store_true",
                        help="Actually delete (default is dry-run).")
    parser.add_argument("--yes", action="store_true",
                        help="Skip confirmation prompt (required with --delete).")
    parser.add_argument("--keep", help="Email of the tester account to keep.")
    args = parser.parse_args()

    client, db = _connect()

    if not args.delete:
        _dry_run(db)
        client.close()
        return

    if not args.keep:
        client.close()
        raise SystemExit("ERROR: --delete requires --keep <email> (the tester to keep).")
    if not args.yes:
        client.close()
        raise SystemExit("ERROR: --delete requires --yes to confirm.")

    keep_email = args.keep.strip().lower()

    testers = list(db["users"].find({"isTester": True}))
    keeper = [u for u in testers if (u.get("email", "") or "").strip().lower() == keep_email]
    to_delete = [u for u in testers if (u.get("email", "") or "").strip().lower() != keep_email]

    if not keeper:
        client.close()
        raise SystemExit(f"ERROR: --keep {keep_email!r} is not a tester account. Aborting (nothing deleted).")

    print(f"Keeper:  {keeper[0].get('email')}")
    print(f"Deleting {len(to_delete)} tester account(s):")
    for u in to_delete:
        print(f"  - {u.get('email')} (id={u['_id']})")
    print()

    for u in to_delete:
        email, r = _delete_user(db, u)
        print(f"Deleted {email}: user={r['user']} vault={r['vault']} push={r['push_subs']} "
              f"trusted={r['trusted_links']} devices={r['known_devices']} passkeys={r['passkeys']} "
              f"resets={r['resets']} files={r['files']}")

    remaining = db["users"].count_documents({"isTester": True})
    print(f"\nDone. {remaining} tester account(s) remain.")

    client.close()


if __name__ == "__main__":
    main()
