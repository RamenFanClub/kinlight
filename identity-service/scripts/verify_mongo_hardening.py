#!/usr/bin/env python3
"""F141 — round-2 verification gate: Mongo-only compromise threat model.

Read-only audit of the `emergency_exit` database against the F131–F138
encryption-at-rest hardening. Connects with the SAME credentials a Mongo-only
attacker would hold (the scoped `kinlight_app` user, F135) and asserts that a
database dump leaks no plaintext PII, vault content, or filenames.

The VAULT_ENCRYPTION_KEY / JWT_SECRET live in GCP Secret Manager — NOT Mongo —
so a dump should show only ciphertext. This script verifies that claim.

Run from identity-service/:
    source .venv/bin/activate
    MONGO_URI="mongodb+srv://kinlight_app:..." \
        python3 scripts/verify_mongo_hardening.py
    # Optional secret-scan (S6) — pass the real secret values to confirm they
    # never appear in the dump:
    MONGO_URI="mongodb+srv://..." \
        VAULT_ENCRYPTION_KEY="<64-hex>" JWT_SECRET="<jwt>" \
        python3 scripts/verify_mongo_hardening.py

On the GCE VM, pass --gcp-project-id to have the script fetch its own secrets
from Secret Manager via the instance metadata server (mirrors main.py F122), so
no secret ever appears in argv, env files, or shell history:

    docker run --rm -e GCP_PROJECT_ID=<project-id> -v ~/kinlight/identity-service:/app \
        kinlight-api python /app/scripts/verify_mongo_hardening.py --gcp-project-id <project-id>

Exit code is non-zero if any check fails.
"""

import argparse
import base64
import json
import os
import re
import sys

import requests
from pymongo import MongoClient

DB_NAME = "emergency_exit"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
AU_PHONE_RE = re.compile(r"(?:\+?61|0)4\d{8}")
SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

# Fields that F131–F138 encrypt — each should hold opaque ciphertext.
SHOULD_BE_ENCRYPTED_USER = ("name", "ageGroup", "notes")
SHOULD_BE_ENCRYPTED_DEVICE = ("deviceName", "userAgent", "ip")
SHOULD_BE_ENCRYPTED_CRED = ("deviceName",)

# Collections that are expected to exist after all shipped features.
EXPECTED_COLLECTIONS = (
    "users",
    "vaults",
    "fs.files",
    "fs.chunks",
    "trusted_links",
    "push_subscriptions",
    "known_devices",
    "webauthn_credentials",
    "password_resets",
    "system",
)

FAILURES = []  # (slice, message)
WARNINGS = []  # (slice, message)

# ── GCP Secret Manager self-fetch (mirrors main.py F122) ──────────────────────
# When --gcp-project-id is supplied, the script pulls its own secrets from Secret
# Manager using the VM's attached service account (via the instance metadata
# server), so secrets never appear in argv, env files, or shell history.

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


def _fetch_secret_manager_token() -> str:
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
    data = resp.json()
    return base64.b64decode(data["payload"]["data"]).decode("utf-8")


def _load_secrets_from_secret_manager(project_id: str) -> None:
    """Populate os.environ for MONGO_URI / VAULT_ENCRYPTION_KEY / JWT_SECRET from
    Secret Manager, unless the env var is already set (env vars always win)."""
    if not project_id:
        return
    token = _fetch_secret_manager_token()
    for env_name, secret_name in _SECRET_MAP:
        if os.environ.get(env_name):
            continue
        try:
            os.environ[env_name] = _fetch_secret(project_id, token, secret_name)
            print(f"    loaded {env_name} from Secret Manager ({secret_name})")
        except Exception as exc:
            print(f"    [WARN] failed to load {env_name} ({secret_name}): {exc}")


def check(slice_name, ok, msg):
    if ok:
        print(f"  [ok]   {msg}")
    else:
        FAILURES.append((slice_name, msg))
        print(f"  [FAIL] {msg}")


def warn(slice_name, msg):
    WARNINGS.append((slice_name, msg))
    print(f"  [WARN] {msg}")


def iter_strings(obj):
    """Yield every string value in a nested JSON-like structure."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_strings(v)


def contains_pii(value) -> list:
    """Return a list of PII patterns found in a string (defense-in-depth grep)."""
    if not isinstance(value, str):
        return []
    hits = []
    if EMAIL_RE.search(value):
        hits.append("email")
    if AU_PHONE_RE.search(value):
        hits.append("au-mobile")
    return hits


def is_ciphertext_blob(value) -> bool:
    """True when a string looks like base64(nonce + AES-GCM ciphertext + tag).

    Ciphertext decodes to random binary that is not valid UTF-8; plaintext
    (filenames, names, emails) is human-readable and/or not base64 at all.
    """
    if not isinstance(value, str) or not value:
        return False
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception:
        return False
    try:
        raw.decode("utf-8")
        return False  # decoded to readable text → plaintext, not ciphertext
    except Exception:
        return True


def is_sha256_hex(value) -> bool:
    return isinstance(value, str) and bool(SHA256_HEX_RE.fullmatch(value))


def is_bcrypt(value) -> bool:
    return isinstance(value, str) and (value.startswith("$2b$") or value.startswith("$2a$"))


def connect(db_name, mongo_uri):
    mongo_uri = mongo_uri or os.environ.get("MONGO_URI")
    if not mongo_uri:
        raise SystemExit("ERROR: MONGO_URI not set (use --mongo-uri or MONGO_URI env var).")
    return MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)[db_name]


# ── S0: scope & baseline ──────────────────────────────────────────────────────
def slice_scope(db):
    print("S0 — Scope & read-access baseline")
    present = set(db.list_collection_names())
    check("S0", bool(present), f"connected as a Mongo-only attacker; {len(present)} collections visible")
    for c in EXPECTED_COLLECTIONS:
        count = db[c].count_documents({}) if c in present else 0
        marker = "" if c in present else " (MISSING)"
        print(f"    {c}: {count}{marker}")
    for c in EXPECTED_COLLECTIONS:
        check("S0", c in present, f"expected collection '{c}' present")
    return present


# ── S1: vaults ────────────────────────────────────────────────────────────────
def slice_vaults(db, present):
    print("S1 — vaults collection")
    if "vaults" not in present:
        return
    bad_content = 0
    bad_log = 0
    bad_legacy = 0
    pii_in_content = 0
    for doc in db["vaults"].find({}):
        content = doc.get("content")
        if isinstance(content, dict):
            bad_content += 1
            continue
        if isinstance(content, str):
            if content.strip().startswith("{"):
                bad_content += 1
            elif not is_ciphertext_blob(content):
                bad_content += 1
            elif contains_pii(content):
                pii_in_content += 1
        elif content is not None:
            bad_content += 1
        if "log" in doc:
            bad_log += 1
        if "vault" in doc:
            bad_legacy += 1
    check("S1", bad_content == 0, f"content is opaque ciphertext in all docs ({bad_content} plaintext)")
    check("S1", bad_log == 0, f"no top-level plaintext 'log' field (F131) ({bad_log} found)")
    check("S1", bad_legacy == 0, f"no legacy plaintext 'vault' blob (F41) ({bad_legacy} found)")
    check("S1", pii_in_content == 0, f"no plaintext PII grepped inside content ({pii_in_content})")


# ── S2: users ─────────────────────────────────────────────────────────────────
def slice_users(db, present):
    print("S2 — users collection")
    if "users" not in present:
        return
    unencrypted_pii = 0
    plaintext_password = 0
    email_plaintext = 0
    for doc in db["users"].find({}):
        if not doc.get("piiEncrypted"):
            for f in SHOULD_BE_ENCRYPTED_USER:
                if isinstance(doc.get(f), str) and doc.get(f):
                    unencrypted_pii += 1
                    break
        else:
            for f in SHOULD_BE_ENCRYPTED_USER:
                v = doc.get(f)
                if isinstance(v, str) and v and (not is_ciphertext_blob(v) or contains_pii(v)):
                    unencrypted_pii += 1
                    break
        pw = doc.get("password")
        if pw and not is_bcrypt(pw):
            plaintext_password += 1
        if isinstance(doc.get("email"), str) and doc.get("email"):
            email_plaintext += 1
    check("S2", unencrypted_pii == 0, f"name/ageGroup/notes encrypted (F133) ({unencrypted_pii} unencrypted)")
    check("S2", plaintext_password == 0, f"password is a bcrypt hash ({plaintext_password} plaintext)")
    warn("S2", f"{email_plaintext} user(s) still store plaintext email/username — accepted residual (F139 deferred)")


# ── S3: GridFS ────────────────────────────────────────────────────────────────
def slice_gridfs(db, present):
    print("S3 — GridFS (fs.files / fs.chunks)")
    if "fs.files" not in present:
        return
    plaintext_filename = 0
    stale_meta_filename = 0
    for doc in db["fs.files"].find({}):
        meta = doc.get("metadata") or {}
        fname = doc.get("filename")
        if not meta.get("filenameEncrypted"):
            if isinstance(fname, str) and fname:
                plaintext_filename += 1
        elif isinstance(fname, str) and fname and (not is_ciphertext_blob(fname) or contains_pii(fname)):
            plaintext_filename += 1
        if "filename" in meta:
            stale_meta_filename += 1
    check("S3", plaintext_filename == 0, f"filenames encrypted (F132) ({plaintext_filename} plaintext)")
    check("S3", stale_meta_filename == 0, f"no plaintext metadata.filename left (F132) ({stale_meta_filename})")

    if "fs.chunks" not in present:
        return
    plaintext_chunks = 0
    sampled = 0
    for doc in db["fs.chunks"].find({}).limit(50):
        data = doc.get("data")
        if not isinstance(data, bytes):
            continue
        sampled += 1
        try:
            text = data.decode("utf-8")
        except Exception:
            continue
        if contains_pii(text) or "%PDF" in text or "PK\x03\x04" in text:
            plaintext_chunks += 1
    check("S3", plaintext_chunks == 0, f"chunk bodies opaque (sampled {sampled}) ({plaintext_chunks} plaintext)")


# ── S4: secondary collections ─────────────────────────────────────────────────
def slice_secondary(db, present):
    print("S4 — secondary collections")
    if "push_subscriptions" in present:
        plain_sub = 0
        for doc in db["push_subscriptions"].find({}):
            if not doc.get("subscriptionEncrypted"):
                if isinstance(doc.get("subscription"), dict):
                    plain_sub += 1
            elif not is_ciphertext_blob(doc.get("subscriptionEnc")):
                plain_sub += 1
            if "subscription" in doc:
                plain_sub += 1
        check("S4", plain_sub == 0, f"push subscriptions encrypted (F138) ({plain_sub} plaintext)")

    if "known_devices" in present:
        plain_dev = 0
        for doc in db["known_devices"].find({}):
            if not doc.get("piiEncrypted"):
                if any(isinstance(doc.get(f), str) and doc.get(f) for f in SHOULD_BE_ENCRYPTED_DEVICE):
                    plain_dev += 1
            else:
                for f in SHOULD_BE_ENCRYPTED_DEVICE:
                    v = doc.get(f)
                    if isinstance(v, str) and v and (not is_ciphertext_blob(v) or contains_pii(v)):
                        plain_dev += 1
                        break
        check("S4", plain_dev == 0, f"known_devices PII encrypted (F137) ({plain_dev} plaintext)")

    if "webauthn_credentials" in present:
        plain_cred = 0
        for doc in db["webauthn_credentials"].find({}):
            v = doc.get("deviceName")
            if isinstance(v, str) and v:
                if not doc.get("piiEncrypted") or (not is_ciphertext_blob(v) or contains_pii(v)):
                    plain_cred += 1
        check("S4", plain_cred == 0, f"webauthn deviceName encrypted (F137) ({plain_cred} plaintext)")

    for col, label in (("trusted_links", "trusted links"), ("password_resets", "password resets")):
        if col not in present:
            continue
        bad_hash = 0
        for doc in db[col].find({}):
            th = doc.get("tokenHash")
            if not is_sha256_hex(th):
                bad_hash += 1
        check("S4", bad_hash == 0, f"{label} store SHA-256 token hashes only ({bad_hash} bad)")


# ── S5: infra — DB user role + IP allowlist ───────────────────────────────────
def slice_infra(db):
    print("S5 — Infra assertions (DB user role + IP allowlist)")
    try:
        info = db.command("connectionStatus")
        auth = info.get("authInfo", {}) or {}
        user = (auth.get("authenticatedUsers") or [{}])[0].get("user")
        roles = [r.get("role") for r in (auth.get("authenticatedUserRoles") or [])]
        db_roles = [r.get("db") for r in (auth.get("authenticatedUserRoles") or [])]
        print(f"    authenticated as '{user}' roles={list(zip(roles, db_roles))}")
        ok_roles = all(r == "readWrite" and d == DB_NAME for r, d in zip(roles, db_roles))
        check("S5", ok_roles, f"current user restricted to readWrite@{DB_NAME} (F135)")
    except Exception as e:
        warn("S5", f"could not read connectionStatus: {e}")
    warn("S5", "confirm IP allowlist in Atlas console = 35.212.156.52/32 (+ personal IP), no 0.0.0.0/0 (F136)")


# ── S6: auth/token protections ────────────────────────────────────────────────
def slice_auth(db, present, key, jwt_secret):
    print("S6 — Auth/token protections")
    secrets = [("VAULT_ENCRYPTION_KEY", key), ("JWT_SECRET", jwt_secret)]
    secrets = [(n, s) for n, s in secrets if s]
    if secrets:
        leaked = 0
        for name, secret in secrets:
            for col in present:
                for doc in db[col].find({}):
                    for s in iter_strings(doc):
                        if secret in s:
                            leaked += 1
                            print(f"    [FAIL] {name} value found in {col}._id={doc.get('_id')}")
                            break
        check("S6", leaked == 0, f"encryption/JWT secrets absent from the dump ({leaked} leak(s))")
    else:
        warn("S6", "pass VAULT_ENCRYPTION_KEY / JWT_SECRET to scan for secret leaks (skipped)")
    print("    checklist (covered by pytest): JWT exp/iat (F78), tokenVersion (F105),")
    print("    password policy (F97), account lockout (F86) — see test_main.py")


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="F141: verify MongoDB-compromise hardening")
    parser.add_argument("--mongo-uri", help="MongoDB connection string (overrides MONGO_URI env var)")
    parser.add_argument("--db-name", default=DB_NAME, help=f"database name (default {DB_NAME})")
    parser.add_argument("--key", help="VAULT_ENCRYPTION_KEY (for S6 secret scan; falls back to env var)")
    parser.add_argument("--jwt-secret", help="JWT_SECRET (for S6 secret scan; falls back to env var)")
    parser.add_argument("--gcp-project-id", help="GCP project ID — self-fetch secrets from Secret Manager")
    parser.add_argument("--collections", help="comma-separated subset of slices to run (S0..S6)")
    args = parser.parse_args()

    if args.gcp_project_id:
        _load_secrets_from_secret_manager(args.gcp_project_id)

    key = args.key or os.environ.get("VAULT_ENCRYPTION_KEY", "")
    jwt_secret = args.jwt_secret or os.environ.get("JWT_SECRET", "")

    db = connect(args.db_name, args.mongo_uri)

    slices = {
        "S0": lambda: slice_scope(db),
        "S1": lambda: slice_vaults(db, db.list_collection_names()),
        "S2": lambda: slice_users(db, db.list_collection_names()),
        "S3": lambda: slice_gridfs(db, db.list_collection_names()),
        "S4": lambda: slice_secondary(db, db.list_collection_names()),
        "S5": slice_infra,
        "S6": lambda: slice_auth(db, db.list_collection_names(), key, jwt_secret),
    }
    if args.collections:
        want = {s.strip().upper() for s in args.collections.split(",")}
        slices = {k: v for k, v in slices.items() if k in want}

    print("=" * 70)
    print("F141 — MongoDB-compromise hardening verification")
    print("=" * 70)

    present = db.list_collection_names()
    for name, fn in slices.items():
        if name == "S0":
            fn()
            continue
        fn()
        print()

    print("=" * 70)
    if FAILURES:
        print(f"RESULT: FAIL — {len(FAILURES)} failing check(s):")
        for sl, msg in FAILURES:
            print(f"  [{sl}] {msg}")
    else:
        print("RESULT: PASS — no plaintext PII/content/filenames found.")
    for sl, msg in WARNINGS:
        print(f"  [{sl}] note: {msg}")
    print("=" * 70)

    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
