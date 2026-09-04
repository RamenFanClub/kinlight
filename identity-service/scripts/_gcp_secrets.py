#!/usr/bin/env python3
"""Shared GCP Secret Manager self-fetch for one-off scripts (F122 pattern).

Fetches secrets via the GCE VM's instance metadata server so they never appear
in argv, env files, or shell history. Mirrors `main.py:_load_secrets_from_secret_manager`.

Sibling import — run scripts as `python3 scripts/<script>.py` (from identity-service/)
so this module is on `sys.path`. Inside the container, `scripts/` is likewise on
the path when the script is invoked as `python /app/scripts/<script>.py`.
"""

import base64
import os

import requests

_METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/token"
)
_METADATA_PROJECT_ID_URL = (
    "http://metadata.google.internal/computeMetadata/v1/project/project-id"
)
_METADATA_HEADERS = {"Metadata-Flavor": "Google"}

_SECRET_MAP = (
    ("MONGO_URI", "kinlight-mongo-uri"),
    ("VAULT_ENCRYPTION_KEY", "kinlight-vault-encryption-key"),
    ("JWT_SECRET", "kinlight-jwt-secret"),
    ("KINLIGHT_ADMIN_EMAIL", "kinlight-admin-email"),
    ("KINLIGHT_ADMIN_PASSWORD", "kinlight-admin-password"),
)


def _fetch_token() -> str:
    resp = requests.get(_METADATA_TOKEN_URL, headers=_METADATA_HEADERS, timeout=3)
    resp.raise_for_status()
    return resp.json()["access_token"]


def _discover_project_id() -> str:
    """Return the GCP project ID from the instance metadata server, or "" if
    we're not running on GCE (or the metadata server is unreachable)."""
    try:
        resp = requests.get(_METADATA_PROJECT_ID_URL, headers=_METADATA_HEADERS, timeout=2)
        resp.raise_for_status()
        return resp.text.strip()
    except Exception:
        return ""


def _fetch_secret(project_id: str, token: str, secret_name: str) -> str:
    url = (
        "https://secretmanager.googleapis.com/v1/projects/"
        f"{project_id}/secrets/{secret_name}/versions/latest:access"
    )
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=5)
    resp.raise_for_status()
    return base64.b64decode(resp.json()["payload"]["data"]).decode("utf-8")


def load_secrets(project_id: str = None, names=("MONGO_URI", "VAULT_ENCRYPTION_KEY", "JWT_SECRET")) -> None:
    """Populate os.environ for the mapped secrets, unless already set (env wins).

    `names` restricts which secrets to fetch. The project ID is resolved in this
    order: explicit argument → GCP_PROJECT_ID env var → instance-metadata-server
    discovery (so callers never have to put the project ID on the command line).
    No-op (with a clear warning) if it cannot be resolved — e.g. local runs fall
    back to env vars for the secrets themselves.
    """
    if not project_id:
        project_id = os.environ.get("GCP_PROJECT_ID", "")
    discovered = False
    if not project_id:
        project_id = _discover_project_id()
        discovered = bool(project_id)
    if not project_id:
        print("  [WARN] no GCP project ID resolvable (not on GCE?); "
              "pass --gcp-project-id, set GCP_PROJECT_ID, or set MONGO_URI/VAULT_ENCRYPTION_KEY directly.")
        return

    source = "discovered from metadata server" if discovered else "provided"
    print(f"  using GCP project ID {project_id!r} ({source})")

    try:
        token = _fetch_token()
    except Exception as exc:
        print(f"  [WARN] metadata token fetch failed ({exc}); falling back to env vars.")
        return
    for env_name, secret_name in _SECRET_MAP:
        if env_name not in names:
            continue
        if os.environ.get(env_name):
            continue
        try:
            os.environ[env_name] = _fetch_secret(project_id, token, secret_name)
            print(f"  loaded {env_name} from Secret Manager ({secret_name})")
        except Exception as exc:
            print(f"  [WARN] failed to load {env_name} ({secret_name}): {exc}")
