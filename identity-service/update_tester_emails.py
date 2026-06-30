#!/usr/bin/env python3
"""Add email addresses to all tester + admin accounts in MongoDB.

Run once from the identity-service/ directory:
    source .venv/bin/activate
    MONGO_URI="mongodb+srv://..." python3 update_tester_emails.py

Updates:
  tester_01 through tester_06 → buat.nonton8282@gmail.com
  anggi (admin)              → buat.nonton8282@gmail.com
"""

import os
from pymongo import MongoClient

ACCOUNTS = [
    "tester_01", "tester_02", "tester_03",
    "tester_04", "tester_05", "tester_06",
    "anggi"
]

EMAIL = "buat.nonton8282@gmail.com"


def main() -> None:
    mongo_uri = os.environ.get("MONGO_URI")
    if not mongo_uri:
        print("ERROR: MONGO_URI environment variable not set.")
        print("Usage: MONGO_URI='mongodb+srv://...' python3 update_tester_emails.py")
        return

    print("Connecting to MongoDB...")
    client = MongoClient(mongo_uri)
    db = client["emergency_exit"]
    users = db["users"]

    updated = 0
    for username in ACCOUNTS:
        result = users.update_one(
            {"username": username},
            {"$set": {"email": EMAIL}}
        )
        if result.matched_count == 0:
            print(f"  WARNING: '{username}' not found — skipped")
        else:
            updated += 1
            print(f"  Updated: {username}")

    client.close()

    if updated == 0:
        print("\nNo accounts updated. Check MONGO_URI and account names.")
        return

    print(f"\n{updated} account(s) now have email: {EMAIL}")


if __name__ == "__main__":
    main()
