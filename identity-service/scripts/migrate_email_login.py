#!/usr/bin/env python3
"""F113 — Migrate accounts to email-as-login.

Email is now the canonical login identifier (replaces the old `username` field).
This script must be run ONCE against MongoDB Atlas before the app's startup
logic tries to build the unique index on `users.email` (a duplicate email
would make that index fail to build).

It:
  1. Assigns distinct emails to the 7 accounts that currently share one address,
     using Gmail plus-aliases (buat.nonton8282+tester01@gmail.com …) so they
     all still land in the same inbox but are distinct login strings.
  2. Backfills the legacy `username` field = email (kept harmless, no longer
     used for login).
  3. Verifies no duplicate `email` values remain — aborts otherwise.

Run from the identity-service/ directory:
    source .venv/bin/activate
    MONGO_URI="mongodb+srv://..." python3 scripts/migrate_email_login.py
"""

import os
from collections import Counter

from pymongo import MongoClient

# Distinct emails for the accounts that currently share buat.nonton8282@gmail.com.
# Gmail +aliases deliver to the same inbox but are distinct login strings.
SHARED_EMAIL = "buat.nonton8282@gmail.com"
EMAIL_MAP = {
    "tester_01": "buat.nonton8282+tester01@gmail.com",
    "tester_02": "buat.nonton8282+tester02@gmail.com",
    "tester_03": "buat.nonton8282+tester03@gmail.com",
    "tester_04": "buat.nonton8282+tester04@gmail.com",
    "tester_05": "buat.nonton8282+tester05@gmail.com",
    "tester_06": "buat.nonton8282+tester06@gmail.com",
    "anggi": "anggita.bayu@gmail.com",
}


def main() -> None:
    mongo_uri = os.environ.get("MONGO_URI")
    if not mongo_uri:
        print("ERROR: MONGO_URI environment variable not set.")
        print("Usage: MONGO_URI='mongodb+srv://...' python3 scripts/migrate_email_login.py")
        return

    print("Connecting to MongoDB...")
    client = MongoClient(mongo_uri)
    db = client["emergency_exit"]
    users = db["users"]

    # 1. Assign distinct emails to the accounts currently sharing one address.
    updated = 0
    for username, email in EMAIL_MAP.items():
        result = users.update_one(
            {"username": username},
            {"$set": {"email": email}},
        )
        if result.matched_count == 0:
            print(f"  WARNING: '{username}' not found — skipped")
        else:
            updated += 1
            print(f"  Email set: {username} -> {email}")

    # 2. Backfill legacy `username` = email for every account that has one.
    backfilled = 0
    for doc in users.find({}):
        email = doc.get("email")
        if not email:
            print(f"  WARNING: account {doc.get('_id')} ({doc.get('username','?')}) has no email — cannot migrate")
            continue
        email = email.strip().lower()
        if doc.get("username") != email:
            users.update_one({"_id": doc["_id"]}, {"$set": {"username": email}})
            backfilled += 1
    print(f"{backfilled} account(s) backfilled (username = email)")

    # 3. Verify no duplicate emails remain before the unique index is built.
    emails = [u.get("email") for u in users.find({}) if u.get("email")]
    dupes = [email for email, count in Counter(emails).items() if count > 1]
    if dupes:
        print("\nERROR: duplicate emails still present — unique index will fail:")
        for email in dupes:
            print(f"  - {email}")
        print("Resolve duplicates and re-run before deploying.")
        return

    client.close()
    print(f"\n{updated} shared-email account(s) migrated, {backfilled} backfilled.")
    print("No duplicate emails. Safe to deploy — startup will build the unique index.")


if __name__ == "__main__":
    main()
