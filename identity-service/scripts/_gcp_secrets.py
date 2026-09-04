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
_METADATA_HEADERS = {"Metadata-Flavor": "Google"}

_SECRET_MAP = (
    ("MONGO_URI", "kinlight-mongo-uri"),
    ("VAULT_ENCRYPTION_KEY", "kinlight-vault-encryption-key"),
    ("JWT_SECRET", "kinlight-jwt-secret"),
)


def _fetch_token() -> str:
    resp = requests.get(_METADATA_TOKEN_URL, headers=_METADATA_HEADERS, timeout=3)
    resp.raise_for_status()
    return resp.json()["access_token"]


def _fetch_secret(project_id: str, token: str, secret_name: str) -> str:
    url = (
        "https://secretmanager.googleapis.com/v1/projects/"
        f"{project_id}/secrets/{secret_name}/versions/latest:access"
    )
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=5)
    resp.raise_for_status()
    return base64.b64decode(resp.json()["payload"]["data"]).decode("utf-8")


def load_secrets(project_id: str, names=("MONGO_URI", "VAULT_ENCRYPTION_KEY", "JWT_SECRET")) -> None:
    """Populate os.environ for the mapped secrets, unless already set (env wins).

    `names` restricts which secrets to fetch. No-op when project_id is empty.
    """
    if not project_id:
        return
    token = _fetch_token()
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
