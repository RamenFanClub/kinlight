#!/usr/bin/env python3
"""F112 — test whether the UptimeRobot API key has read-write access.

Prints the key length + tail (so you can match it against the key in the
UptimeRobot dashboard without exposing it), confirms the key is valid via
`getAccountDetails` (a read call), then probes write access with `newMonitor`
and dumps the raw response body.

How to read the output:
  - getAccountDetails HTTP 200 but newMonitor HTTP 403 -> key is read-only /
    monitor-specific (only `get*` methods allowed).
  - newMonitor body "api_key is wrong" / "no api_key"  -> the secret holds the
    wrong or stale key (compare `key tail` / `key length` against the dashboard).
  - key length 37 or `key tail` shows a newline      -> trailing whitespace in
    the stored value.
  - newMonitor HTTP 200 + stat ok                     -> read-write confirmed,
    and the `kinlight-api` monitor was just created.

Run from identity-service/ (or inside the container):
    python scripts/test_uptimerobot_key.py
"""

import os
import sys

import requests

from _gcp_secrets import load_secrets

API_BASE = "https://api.uptimerobot.com/v2"
MONITOR_NAME = "kinlight-api"
MONITOR_URL = "https://api.kinlight.app/health"


def main() -> int:
    load_secrets(None, names=("KINLIGHT_UPTIMEROBOT_API_KEY",))
    key = os.environ.get("KINLIGHT_UPTIMEROBOT_API_KEY", "")
    if not key:
        raise SystemExit("ERROR: KINLIGHT_UPTIMEROBOT_API_KEY missing from Secret Manager/env.")

    print(f"key length: {len(key)}")
    print(f"key tail  : {key[-4:]!r}")

    read = requests.post(
        f"{API_BASE}/getAccountDetails",
        data={"api_key": key, "format": "json"},
        timeout=30,
    )
    print(f"getAccountDetails: HTTP {read.status_code}")
    print(read.text[:400])

    write = requests.post(
        f"{API_BASE}/newMonitor",
        data={
            "api_key": key,
            "format": "json",
            "friendly_name": MONITOR_NAME,
            "url": MONITOR_URL,
            "type": 1,
            "interval": 300,
        },
        timeout=30,
    )
    print(f"newMonitor: HTTP {write.status_code}")
    print(write.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
