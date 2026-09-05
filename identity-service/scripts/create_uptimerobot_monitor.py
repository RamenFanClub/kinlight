#!/usr/bin/env python3
"""F112 — create (or verify) the UptimeRobot monitor for api.kinlight.app/health.

Idempotent: if a monitor named `kinlight-api` already exists it is left alone
and its ID is printed. Otherwise it creates an HTTP(s) monitor at the 5-minute
free-tier interval, wired to the account's default email alert contact. Any
non-2xx response from `/health` (the F93 503 on a stale pulse scanner) is
treated as DOWN by UptimeRobot, so a single failed check triggers the email.

NOTE (free plan): UptimeRobot's free tier blocks monitor *creation* via the
v2 API — `newMonitor` returns 403 "not allowed to use some settings with your
current plan". Reads (`getMonitors`) work, so this script effectively only
*detects* an existing monitor. Create/edit the monitor manually in the
dashboard instead (see docs/gce-deployment-guide.md "Uptime monitoring").

API key: read from KINLIGHT_UPTIMEROBOT_API_KEY (Secret Manager
`kinlight-uptimerobot-api-key` when run on the VM — see _gcp_secrets.py).

Run from identity-service/:
    source .venv/bin/activate
    KINLIGHT_UPTIMEROBOT_API_KEY=... python3 scripts/create_uptimerobot_monitor.py

On the GCE VM the key self-fetches from Secret Manager (project ID
auto-discovered).
"""

import argparse
import os
import sys

import requests

from _gcp_secrets import load_secrets

API_BASE = "https://api.uptimerobot.com/v2"
MONITOR_NAME = "kinlight-api"
MONITOR_URL = "https://api.kinlight.app/health"
MONITOR_TYPE = 1        # HTTP(s)
MONITOR_INTERVAL = 300  # 5 minutes — free-plan minimum


def _api_key() -> str:
    return os.environ.get("KINLIGHT_UPTIMEROBOT_API_KEY", "")


def _call(method: str, **params) -> dict:
    data = {"api_key": _api_key(), "format": "json", **params}
    resp = requests.post(f"{API_BASE}/{method}", data=data, timeout=30)
    if not resp.ok:
        raise SystemExit(f"ERROR: UptimeRobot {method} returned HTTP {resp.status_code}: {resp.text}")
    return resp.json()


def _find_monitor() -> dict:
    """Return the existing `/health` monitor dict, or None.

    Matches on the URL (authoritative for F112) or the friendly name. The URL
    comparison tolerates a trailing slash on the stored value.
    """
    result = _call("getMonitors")
    for monitor in result.get("monitors", []):
        url = (monitor.get("url") or "").rstrip("/")
        if monitor.get("friendly_name") == MONITOR_NAME or url == MONITOR_URL.rstrip("/"):
            return monitor
    return None


def _default_alert_contact() -> str:
    """Return the alert-contact param for the account's default email contact.

    Empty string means "none" — UptimeRobot then alerts the account default.
    """
    result = _call("getAlertContacts")
    contacts = result.get("alert_contacts", [])
    if not contacts:
        return ""
    return f"{contacts[0]['id']}_0_0"


def main() -> int:
    parser = argparse.ArgumentParser(description="F112: create the UptimeRobot /health monitor.")
    parser.add_argument("--gcp-project-id", help="GCP project ID — self-fetch secrets from Secret Manager")
    args = parser.parse_args()

    load_secrets(args.gcp_project_id, names=("KINLIGHT_UPTIMEROBOT_API_KEY",))

    if not _api_key():
        raise SystemExit("ERROR: KINLIGHT_UPTIMEROBOT_API_KEY missing — set it in Secret Manager "
                         "(`kinlight-uptimerobot-api-key`) or as an env var.")

    print("=" * 70)
    print("F112 — UptimeRobot monitor creation")
    print("=" * 70)

    existing = _find_monitor()
    if existing:
        print(f"monitor already exists — nothing to do (id={existing.get('id')})")
        print(f"  url     : {existing.get('url')}")
        print(f"  interval: {existing.get('interval')}s")
        return 0

    alert_contact = _default_alert_contact()
    if not alert_contact:
        print("  [WARN] no alert contacts found — monitor will alert the account default.")

    result = _call(
        "newMonitor",
        friendly_name=MONITOR_NAME,
        url=MONITOR_URL,
        type=MONITOR_TYPE,
        interval=MONITOR_INTERVAL,
        alert_contacts=alert_contact,
    )

    if result.get("stat") != "ok":
        error = result.get("error", {})
        print(f"ERROR: UptimeRobot rejected the request — "
              f"{error.get('message', error) if isinstance(error, dict) else error}")
        return 1

    monitor = result.get("monitor", {})
    print(f"created monitor (id={monitor.get('id')})")
    print(f"  url     : {MONITOR_URL}")
    print(f"  interval: {MONITOR_INTERVAL}s")
    print(f"  alert   : {alert_contact or 'account default'}")
    print()
    print("Next: run verify_health_alert.py to prove a stale pulse scanner pages you.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
