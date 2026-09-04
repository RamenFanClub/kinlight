#!/usr/bin/env python3
"""F111 — validate the notification pipeline end-to-end.

Drives the real admin HTTP endpoints (login → force-overdue → trigger-pulse),
then verifies from MongoDB that the hourly pulse scan actually processed the
target vault and updated its heartbeat. The final "email actually arrived"
check is human — this script lists which contact inboxes to check and points at
the delivery log line.

WARNING: this sends REAL emails. Use a TEST holder whose contacts are TEST email
addresses you control. Never point it at a live account with real contacts, or
you will send a false alarm.

Run from identity-service/:
    source .venv/bin/activate
    KINLIGHT_ADMIN_EMAIL=... KINLIGHT_ADMIN_PASSWORD=... TARGET=<holder-email> \
        python3 scripts/check_pipeline.py

On the GCE VM the secrets self-fetch from Secret Manager (project ID
auto-discovered), including `kinlight-admin-email` / `kinlight-admin-password`.
The holder defaults to the admin's own account; set the `TARGET` env var (or
`--target`) to make a specific account overdue.
"""

import argparse
import os

import requests

from _gcp_secrets import load_secrets
from _mongo import connect, decrypt_content, get_cipher

DEFAULT_BASE_URL = "https://api.kinlight.app"


def _uid_filter(user_id):
    try:
        from bson import ObjectId
        return {"$in": [str(user_id), ObjectId(str(user_id))]}
    except Exception:
        return {"$in": [str(user_id)]}


def _post(base_url, path, payload, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.post(f"{base_url}{path}", json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="F111: validate the notification pipeline end-to-end.")
    parser.add_argument("--target", help="Email of the holder to make overdue (default: admin's own email)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"API base URL (default {DEFAULT_BASE_URL})")
    parser.add_argument("--admin-email", help="Admin email (overrides env / Secret Manager)")
    parser.add_argument("--admin-password", help="Admin password (overrides env / Secret Manager)")
    parser.add_argument("--gcp-project-id", help="GCP project ID — self-fetch secrets from Secret Manager")
    args = parser.parse_args()

    load_secrets(
        args.gcp_project_id,
        names=("MONGO_URI", "VAULT_ENCRYPTION_KEY", "KINLIGHT_ADMIN_EMAIL", "KINLIGHT_ADMIN_PASSWORD"),
    )

    admin_email = args.admin_email or os.environ.get("KINLIGHT_ADMIN_EMAIL", "")
    admin_password = args.admin_password or os.environ.get("KINLIGHT_ADMIN_PASSWORD", "")
    target = (args.target or os.environ.get("TARGET", "") or "").strip().lower() or admin_email

    if not admin_email or not admin_password:
        raise SystemExit("ERROR: admin credentials missing — set kinlight-admin-email / kinlight-admin-password "
                         "in Secret Manager, or KINLIGHT_ADMIN_EMAIL / KINLIGHT_ADMIN_PASSWORD env vars.")

    db = connect()

    print("=" * 70)
    print("F111 — notification pipeline validation")
    print("=" * 70)
    print(f"  base URL : {args.base_url}")
    print(f"  admin    : {admin_email}")
    print(f"  target   : {target}")
    print()

    # 1. Admin login
    print("1/5  POST /auth/login …")
    login = _post(args.base_url, "/auth/login", {"email": admin_email, "password": admin_password})
    token = login.get("token")
    if not token:
        raise SystemExit("ERROR: login returned no token — check admin credentials.")
    print("      ok — token issued")

    # 2. Force the target vault overdue
    print("2/5  POST /admin/force-overdue …")
    _post(args.base_url, "/admin/force-overdue", {"target": target}, token)
    print("      ok — lastCheckin backdated to 2020")

    # 3. Trigger the pulse scan (synchronous — emails are attempted before it returns)
    print("3/5  POST /admin/trigger-pulse …")
    _post(args.base_url, "/admin/trigger-pulse", {}, token)
    print("      ok — pulse scan ran")

    # 4. Verify server-side state in MongoDB
    print("4/5  Verify MongoDB state …")
    cipher = get_cipher()
    user = db["users"].find_one({"email": target})
    if not user:
        raise SystemExit(f"ERROR: target {target} not found in users collection.")
    vault = db["vaults"].find_one({"userId": _uid_filter(user["_id"])})
    if not vault:
        raise SystemExit(f"ERROR: no vault found for target {target}.")

    notified = bool(vault.get("overdueNotificationSent", False))
    heartbeat = db["system"].find_one({"_id": "pulse_scanner"}) or {}

    content = decrypt_content(cipher, vault.get("content"), str(user["_id"])) if cipher else {}
    contacts = content.get("kin", []) if isinstance(content, dict) else []

    print(f"      overdueNotificationSent = {notified}")
    print(f"      pulse heartbeat lastRun = {heartbeat.get('lastRun')} (checked {heartbeat.get('vaultsChecked')} vaults)")
    print(f"      notifyProto = {vault.get('notifyProto')}")
    print(f"      contacts    = {len(contacts)}")
    for c in contacts:
        print(f"        - {c.get('first')} {c.get('last')} <{c.get('email')}>")

    # 5. Verdict
    print("5/5  Verdict")
    print("=" * 70)
    if notified:
        print("PASS — the scan marked the vault as notified (emails were attempted).")
    else:
        print("WARN — overdueNotificationSent is still False (no contacts? escalate protocol? check logs).")

    print()
    print("Final human check — confirm the email(s) actually arrived:")
    if contacts:
        inboxes = {c.get("email") for c in contacts if c.get("email")}
        print(f"  Check inbox: {', '.join(sorted(inboxes))}")
    print("  Server log:  docker logs kinlight-app | grep -i 'Notification sent'")
    print("  (look for 'Notification sent to <first> at <email>' — a failed Resend call logs a warning instead)")
    print()
    print("To reset the target back to normal afterwards, check in from the app, or:")
    print(f"  POST /auth/login + POST /checkin as {target}")


if __name__ == "__main__":
    main()
