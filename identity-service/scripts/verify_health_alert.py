#!/usr/bin/env python3
"""F112 — prove the UptimeRobot alert chain end-to-end.

Drives the real admin HTTP endpoints to flip `/health` into its 503 state
(a backdated pulse scanner heartbeat, F93), holds it there long enough for an
UptimeRobot poll (5-min interval) to land, then restores it. The final "the
DOWN email actually arrived" check is human — this script keeps the 503 window
open and tells you to watch your inbox.

WARNING: this deliberately makes the production monitor look DOWN. Only run it
when you are able to confirm the alert and restore the heartbeat afterwards.
The script restores automatically before exiting.

Run from identity-service/:
    source .venv/bin/activate
    python3 scripts/verify_health_alert.py [--hold-seconds 360]

Admin credentials self-fetch from Secret Manager (project ID auto-discovered),
including `kinlight-admin-email` / `kinlight-admin-password`.
"""

import argparse
import os
import sys
import time

import requests

from _gcp_secrets import load_secrets

DEFAULT_BASE_URL = "https://api.kinlight.app"
DEFAULT_HOLD_SECONDS = 360  # 6 minutes — a 5-min UptimeRobot poll lands inside the window


def _post(base_url, path, payload, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.post(f"{base_url}{path}", json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _health_status(base_url):
    resp = requests.get(f"{base_url}/health", timeout=30)
    return resp.status_code, resp.json()


def main() -> int:
    parser = argparse.ArgumentParser(description="F112: verify the UptimeRobot alert chain.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"API base URL (default {DEFAULT_BASE_URL})")
    parser.add_argument("--hold-seconds", type=int, default=DEFAULT_HOLD_SECONDS,
                        help=f"seconds to keep /health in 503 (default {DEFAULT_HOLD_SECONDS})")
    parser.add_argument("--admin-email", help="Admin email (overrides env / Secret Manager)")
    parser.add_argument("--admin-password", help="Admin password (overrides env / Secret Manager)")
    parser.add_argument("--gcp-project-id", help="GCP project ID — self-fetch secrets from Secret Manager")
    args = parser.parse_args()

    load_secrets(
        args.gcp_project_id,
        names=("KINLIGHT_ADMIN_EMAIL", "KINLIGHT_ADMIN_PASSWORD"),
    )

    admin_email = args.admin_email or os.environ.get("KINLIGHT_ADMIN_EMAIL", "")
    admin_password = args.admin_password or os.environ.get("KINLIGHT_ADMIN_PASSWORD", "")

    if not admin_email or not admin_password:
        raise SystemExit("ERROR: admin credentials missing — set kinlight-admin-email / kinlight-admin-password "
                         "in Secret Manager, or KINLIGHT_ADMIN_EMAIL / KINLIGHT_ADMIN_PASSWORD env vars.")

    print("=" * 70)
    print("F112 — UptimeRobot alert-chain verification")
    print("=" * 70)
    print(f"  base URL : {args.base_url}")
    print(f"  admin    : {admin_email}")
    print(f"  hold     : {args.hold_seconds}s")
    print()

    # 1. Admin login
    print("1/5  POST /auth/login …")
    login = _post(args.base_url, "/auth/login", {"email": admin_email, "password": admin_password})
    token = login.get("token")
    if not token:
        raise SystemExit("ERROR: login returned no token — check admin credentials.")
    print("      ok — token issued")

    # 2. Healthy baseline
    print("2/5  GET /health (baseline) …")
    code, body = _health_status(args.base_url)
    healthy = body.get("pulseScanner", {}).get("healthy")
    print(f"      HTTP {code} — pulseScanner.healthy={healthy}")
    if code != 200 or healthy is not True:
        print("      [WARN] expected a healthy 200 baseline; proceeding anyway but the 503 contrast may be muted.")

    # 3. Break it — backdate the pulse scanner heartbeat
    print("3/5  POST /admin/force-stale-pulse …")
    _post(args.base_url, "/admin/force-stale-pulse", {}, token)
    code, _ = _health_status(args.base_url)
    if code != 503:
        print(f"      [ERROR] /health returned {code}, expected 503 — aborting.")
        return 1
    print(f"      ok — /health now returns {code}")

    # 4. Hold the 503 state long enough for an UptimeRobot poll
    print(f"4/5  Holding 503 for {args.hold_seconds}s — watch your inbox for the DOWN alert …")
    deadline = time.time() + args.hold_seconds
    while time.time() < deadline:
        remaining = int(deadline - time.time())
        time.sleep(min(30, remaining))
        code, _ = _health_status(args.base_url)
        if code != 503:
            print(f"      [WARN] /health flipped back to {code} early — the hourly pulse scan re-ran. "
                  "Re-run force-stale-pulse (or this script) to extend the window.")
        else:
            print(f"      still 503 … {remaining}s remaining")

    # 5. Restore
    print("5/5  POST /admin/trigger-pulse (restore) …")
    _post(args.base_url, "/admin/trigger-pulse", {}, token)
    code, body = _health_status(args.base_url)
    print(f"      HTTP {code} — pulseScanner.healthy={body.get('pulseScanner', {}).get('healthy')}")
    if code == 200:
        print("      restored to healthy.")
    else:
        print("      [WARN] /health did not return 200 — check the scanner ran.")

    print()
    print("Final human check — did the UptimeRobot DOWN alert arrive?")
    print("  - Expect one email within ~5 minutes of the 503 window opening.")
    print("  - If nothing arrived, check UptimeRobot → the monitor's alert contact + your spam folder.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
