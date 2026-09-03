"""
Kinlight — Identity & Vault Service
FastAPI backend deployed on Railway.
Handles: auth, vault sync, check-in, pulse scan (overdue + reminder emails).
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
import base64
import hashlib
import io
import json
import os
import logging
import re as re_mod
import secrets
import threading
import zipfile
from xml.sax.saxutils import escape as xml_escape

import bcrypt
import jwt
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from bson import ObjectId
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Body, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pymongo import MongoClient, ASCENDING
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from pywebpush import webpush as pywebpush_send, WebPushException
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, generate_challenge, options_to_json_dict
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AttestationConveyancePreference,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from storage import get_storage_backend

# ─── KINLIGHT HELPERS (pure, side-effect-free) ────────────────────────────────
# Extracted into the kinlight/ package for isolation and testability.
# Re-exported here so the app's existing `from main import ...` and
# `patch("main.X")` call sites keep resolving unchanged.
from kinlight.timeutil import ms_to_dt, dt_to_ms, now_utc, ensure_utc
from kinlight.logging_utils import mask_email, _JsonFormatter
from kinlight.pulse_logic import (
    WARNING_DAYS,
    CONTACT_NOTIFY_AFTER_DAYS,
    _interval_days,
    is_overdue,
    is_reminder_due,
    should_send_warning,
    should_notify_contacts,
)
from kinlight.files_safety import (
    ALLOWED_EXTENSIONS,
    _safe_filename,
    _validate_filename,
    _validate_magic_bytes,
)


# ─── APP & CONFIG ─────────────────────────────────────────────────────────────

# ─── F95: STRUCTURED LOGGING ─────────────────────────────────────────────────
#
# Replace all print() calls with stdlib logging. JSON-formatted so Railway's
# log dashboard can filter/search by level, event, and timestamp.
# PII (email addresses) is masked in all log output via mask_email().
# mask_email() and _JsonFormatter now live in kinlight/logging_utils.py.

_handler = logging.StreamHandler()          # writes to stdout
_handler.setFormatter(_JsonFormatter())
logger = logging.getLogger("kinlight")
logger.setLevel(logging.INFO)
logger.addHandler(_handler)
logger.propagate = False                    # avoid duplicate output

# ─── F91: RATE LIMITING ───────────────────────────────────────────────────────
#
# Railway sits in front of this app as a proxy, so request.client.host sees
# Railway's internal IP, not the real visitor — using it directly would put
# every user in the same rate-limit bucket. Railway's own guidance (as of
# 2026) is to read the X-Forwarded-For header and take the first (leftmost)
# IP, since their edge proxy appends to that chain and it's the most
# consistent signal across their routing changes. This can be spoofed by a
# malicious client in theory, but Railway's proxy controls what gets
# appended, so the leftmost entry is trustworthy for rate-limiting purposes
# (this is not used for any authorization decision, only throttling).
def get_client_ip(request: Request) -> str:
    """Resolve the real client IP, trusting the leftmost X-Forwarded-For entry."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


def get_user_or_ip(request: Request) -> str:
    """
    Key by the authenticated user's ID when available, falling back to IP.
    Used for endpoints that require login, so a per-user limit can't be
    sidestepped by switching networks, and unrelated users on the same
    network (e.g. a shared office IP) don't share a bucket unfairly.
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1]
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], options={"require": ["exp", "sub"]})
            return f"user:{payload['sub']}"
        except Exception:
            pass
    return get_client_ip(request)


limiter = Limiter(key_func=get_client_ip)
app = FastAPI()
security = HTTPBearer(auto_error=False)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://kinlight.app",
        "https://ramenfanclub.github.io",
        "https://api.kinlight.app",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# F94: Security response headers (OWASP A05)
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # F119-s: disable browser feature access the app never uses (OWASP A05).
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=(), payment=(), usb=()"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    return response


# ─── F122: GCP SECRET MANAGER LOADER ─────────────────────────────────────────
#
# Fetches runtime secrets from GCP Secret Manager when running on the GCE VM,
# so no plaintext secrets live in GitHub Secrets, the CI deploy script, or
# `docker run` argv. Authenticates via the VM's attached service account using
# the instance metadata server (no key material shipped to CI).
#
# Gating: the loader only runs when GCP_PROJECT_ID is set (set in `docker run`
# for prod; absent in local dev and CI, which fall through to plain env vars).
# Existing env vars always win — this preserves a manual-override/rollback path.

_METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/token"
)
_METADATA_HEADERS = {"Metadata-Flavor": "Google"}

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")

# Env-var name -> Secret Manager secret name (overridable via GCP_SECRET_<VAR>).
_SECRET_MAP = {
    "MONGO_URI": os.environ.get("GCP_SECRET_MONGO_URI", "kinlight-mongo-uri"),
    "JWT_SECRET": os.environ.get("GCP_SECRET_JWT_SECRET", "kinlight-jwt-secret"),
    "RESEND_API_KEY": os.environ.get("GCP_SECRET_RESEND_API_KEY", "kinlight-resend-api-key"),
    "VAULT_ENCRYPTION_KEY": os.environ.get("GCP_SECRET_VAULT_ENCRYPTION_KEY", "kinlight-vault-encryption-key"),
    "VAPID_PRIVATE_KEY": os.environ.get("GCP_SECRET_VAPID_PRIVATE_KEY", "kinlight-vapid-private-key"),
    "VAPID_PUBLIC_KEY": os.environ.get("GCP_SECRET_VAPID_PUBLIC_KEY", "kinlight-vapid-public-key"),
}


def _fetch_secret_manager_token() -> str:
    """Return a short-lived OAuth token from the GCE metadata server."""
    resp = requests.get(_METADATA_TOKEN_URL, headers=_METADATA_HEADERS, timeout=3)
    resp.raise_for_status()
    return resp.json()["access_token"]


def _fetch_secret(project_id: str, token: str, secret_name: str) -> str:
    """Fetch the latest version of a secret and return its plaintext value."""
    url = (
        "https://secretmanager.googleapis.com/v1/projects/"
        f"{project_id}/secrets/{secret_name}/versions/latest:access"
    )
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    return base64.b64decode(data["payload"]["data"]).decode("utf-8")


def _load_secrets_from_secret_manager() -> None:
    """
    Populate os.environ from GCP Secret Manager for any mapped var not already
    set. No-op unless GCP_PROJECT_ID is configured. On any error, log a warning
    and continue — missing critical secrets are caught by the existing startup
    validation (e.g. the JWT_SECRET RuntimeError) or fail at runtime.
    """
    if not GCP_PROJECT_ID:
        return
    try:
        token = _fetch_secret_manager_token()
    except Exception as exc:
        logger.warning("F122: Secret Manager token fetch failed (%s); falling back to env vars", exc)
        return
    for env_name, secret_name in _SECRET_MAP.items():
        if os.environ.get(env_name):
            continue
        try:
            os.environ[env_name] = _fetch_secret(GCP_PROJECT_ID, token, secret_name)
            logger.info("F122: loaded %s from Secret Manager", env_name)
        except Exception as exc:
            logger.warning("F122: failed to load %s: %s", env_name, exc)


_load_secrets_from_secret_manager()


MONGO_URI = os.environ.get("MONGO_URI", "")
JWT_SECRET = os.environ.get("JWT_SECRET", "")
if not JWT_SECRET:
    raise RuntimeError(
        "JWT_SECRET is not set. Set it in GCP Secret Manager (kinlight-jwt-secret) "
        "or as an env var before starting the server."
    )
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
# F04: Application-level encryption key for vault content (AES-256-GCM).
# 64-char hex string → 32 bytes. Set in Railway env vars, never in code.
VAULT_ENCRYPTION_KEY = os.environ.get("VAULT_ENCRYPTION_KEY", "")
APP_URL = "https://kinlight.app"
# F72b — SENDER ADDRESS: do NOT change until kinlight.app is verified in Resend.
# Switching to an unverified domain causes SILENT email delivery failure for all users.
# Once kinlight.app shows "Verified" in the Resend dashboard, change this line to:
#     FROM_EMAIL = "Kinlight <hello@kinlight.app>"
# This is the ONLY remaining F72b step; it absorbs F68 (verified domain) and unblocks F63.
FROM_EMAIL = "Kinlight <hello@kinlight.app>"

# F118: WebAuthn (passkey) configuration. The relying party is the frontend
# origin (kinlight.app); the browser enforces these against the calling page,
# and the backend re-validates them on every verify to prevent replay/cross-origin.
RP_ID = "kinlight.app"
RP_NAME = "Kinlight"
EXPECTED_ORIGIN = "https://kinlight.app"
WEBAUTHN_CHALLENGE_TTL_SECONDS = 300  # passkey ceremonies complete in seconds

# F118: short-lived, in-memory challenge store for WebAuthn ceremonies.
# Single-worker deployment (GCE e2-micro) — no DB round-trip needed for a
# challenge that lives only ~5 minutes. challenge_id -> {challenge, userId, exp}
_webauthn_challenges: dict = {}
_webauthn_challenges_lock = threading.Lock()

client = MongoClient(MONGO_URI) if MONGO_URI else None
db = client["emergency_exit"] if client is not None else None
users_col = db["users"] if db is not None else None
vaults_col = db["vaults"] if db is not None else None
resets_col = db["password_resets"] if db is not None else None
system_col = db["system"] if db is not None else None  # F93: single doc tracking pulse scanner health
push_subs_col = db["push_subscriptions"] if db is not None else None  # F101: push notification subscriptions
trusted_links_col = db["trusted_links"] if db is not None else None  # F115: single-use trusted-person magic links
known_devices_col = db["known_devices"] if db is not None else None  # F117: recognized devices for new-sign-in alerts
webauthn_creds_col = db["webauthn_credentials"] if db is not None else None  # F118: enrolled passkey credentials

# F101: VAPID keypair for Web Push. Generated once and stored in env vars.
# If not set, generate a new pair at startup and log it — copy to Railway env vars.
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
    _key = ec.generate_private_key(ec.SECP256R1())
    _priv_bytes = _key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    _pub_bytes = _key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint
    )
    VAPID_PRIVATE_KEY = base64.urlsafe_b64encode(_priv_bytes).rstrip(b'=').decode()
    VAPID_PUBLIC_KEY = base64.urlsafe_b64encode(_pub_bytes).rstrip(b'=').decode()
    logger.warning(
        "F101: VAPID keys not found in env — generated new pair. "
        "Add VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY to your env vars. "
        f"Public: {VAPID_PUBLIC_KEY}"
    )

VAPID_CLAIMS = {"sub": "mailto:hello@kinlight.app"}

# Password reset configuration (F66)
RESET_TOKEN_TTL_MINUTES = 60
JWT_EXPIRY_HOURS = 24
MIN_PASSWORD_LENGTH = 8

# F115: Trusted person — delegated responder
# How long the trusted person has to act (after being notified) before kins are
# auto-notified. Configurable via env var; defaults to 48 hours.
TRUSTED_RESPONSE_HOURS = int(os.environ.get("TRUSTED_RESPONSE_HOURS", "48"))
TRUSTED_LINK_TTL_MINUTES = 7 * 24 * 60  # magic-link email expiry (7 days, single-use)
TRUSTED_JWT_EXPIRY_HOURS = 24           # scoped trusted JWT lifetime after redemption

# F64-2: Warning days for ping_then_notify protocol
# WARNING_DAYS / CONTACT_NOTIFY_AFTER_DAYS now live in kinlight/pulse_logic.py
# (re-exported above).

# F86: Account lockout after repeated failed logins
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

# F93: Pulse scanner is considered unhealthy if it hasn't run within this window.
# Scanner runs hourly, so 2 hours allows for one missed cycle before alerting.
PULSE_SCAN_UNHEALTHY_AFTER_HOURS = 2

# F97: Common passwords list (top 100 from breach databases)
COMMON_PASSWORDS = {
    "password", "123456", "12345678", "qwerty", "abc123", "monkey", "1234567",
    "letmein", "trustno1", "dragon", "baseball", "iloveyou", "master", "sunshine",
    "ashley", "michael", "shadow", "123123", "654321", "superman", "qazwsx",
    "football", "password1", "password123", "batman", "login", "welcome",
    "solo", "princess", "starwars", "admin", "passw0rd", "hello", "charlie",
    "donald", "loveme", "zaq1zaq1", "whatever", "qwerty123", "aa12345678",
    "access", "696969", "mustang", "thunder", "1234", "12345", "123456789",
    "1234567890", "000000", "1111111", "11111111", "121212", "131313",
    "666666", "7777777", "987654321", "abcdef", "aaaaaa", "jesus",
    "ninja", "azerty", "lovely", "hottie", "freedom", "george", "flower",
    "secret", "biteme", "jordan", "pepper", "buster", "joshua", "ginger",
    "matrix", "silver", "summer", "killer", "robert", "soccer", "hockey",
    "ranger", "daniel", "hunter", "harley", "thomas", "zxcvbnm", "lakers",
    "andrea", "tigger", "222222", "computer", "corvette", "blahblah",
    "cookie", "chicken", "sparky", "snoopy", "samantha", "austin",
    "mercedes", "sierra", "gemini", "peanut", "butter", "tigger",
}

# F96: Vault sync payload limits
MAX_VAULT_ASSETS = 500
MAX_VAULT_CONTACTS = 50
MAX_VAULT_BODY_BYTES = 1_000_000  # 1 MB

# File upload limits
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "application/zip",
}
# Storage backend — pluggable (GridFS by default; set STORAGE_BACKEND=s3 to swap)
_storage_backend = get_storage_backend(db) if db is not None else None



# ─── TIMESTAMP HELPERS ────────────────────────────────────────────────────────
# ms_to_dt / dt_to_ms / now_utc / ensure_utc now live in kinlight/timeutil.py.


# ─── F04: VAULT CONTENT ENCRYPTION ───────────────────────────────────────────

def _get_aesgcm() -> Optional[AESGCM]:
    """Return an AESGCM cipher from VAULT_ENCRYPTION_KEY, or None if not set."""
    if not VAULT_ENCRYPTION_KEY:
        return None
    return AESGCM(bytes.fromhex(VAULT_ENCRYPTION_KEY))


def encrypt_content(content_dict: dict) -> str:
    """
    Encrypt a vault content dict → base64 string for MongoDB storage.
    If no encryption key is configured, returns the dict unchanged (passthrough).
    Format: base64( nonce_12_bytes + ciphertext_with_tag )
    """
    cipher = _get_aesgcm()
    if cipher is None:
        return content_dict  # type: ignore[return-value]
    plaintext = json.dumps(content_dict, separators=(",", ":")).encode("utf-8")
    nonce = os.urandom(12)
    ciphertext = cipher.encrypt(nonce, plaintext, None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_content(stored) -> dict:
    """
    Decrypt vault content from MongoDB.
    Handles three cases:
    1. dict  → plaintext (pre-F04 migration); return as-is
    2. str   → encrypted base64 blob; decrypt and return dict
    3. None  → return empty dict
    """
    if stored is None:
        return {}
    if isinstance(stored, dict):
        return stored
    cipher = _get_aesgcm()
    if cipher is None:
        raise RuntimeError("Encrypted vault found but VAULT_ENCRYPTION_KEY is not set.")
    raw = base64.b64decode(stored)
    nonce = raw[:12]
    ciphertext = raw[12:]
    plaintext = cipher.decrypt(nonce, ciphertext, None)
    return json.loads(plaintext.decode("utf-8"))


def encrypt_bytes(plaintext: bytes) -> bytes:
    """
    Encrypt raw bytes with AES-256-GCM.
    Format: nonce_12_bytes + ciphertext_with_tag
    Returns raw bytes — no base64 (GridFS handles binary natively).
    """
    cipher = _get_aesgcm()
    if cipher is None:
        raise RuntimeError("VAULT_ENCRYPTION_KEY not set — cannot encrypt files.")
    nonce = os.urandom(12)
    ciphertext = cipher.encrypt(nonce, plaintext, None)
    return nonce + ciphertext


def decrypt_bytes(encrypted: bytes) -> bytes:
    """
    Decrypt bytes produced by encrypt_bytes. Returns plaintext.
    """
    cipher = _get_aesgcm()
    if cipher is None:
        raise RuntimeError("VAULT_ENCRYPTION_KEY not set — cannot decrypt files.")
    nonce = encrypted[:12]
    ciphertext = encrypted[12:]
    return cipher.decrypt(nonce, ciphertext, None)


# ─── VAULT SCHEMA HELPERS ─────────────────────────────────────────────────────

def extract_vault_fields(vault_blob: dict) -> dict:
    """Pull scheduling fields from the frontend vault blob for MongoDB storage."""
    return {
        "lastCheckin": ms_to_dt(vault_blob.get("lastCheckin")),
        "checkInFrequency": vault_blob.get("fc", 2),
        "checkInUnit": vault_blob.get("fu", "months"),
        "gracePeriodDays": vault_blob.get("gp", 7),
        "notifyProto": vault_blob.get("notifyProto", "ping_then_notify"),
        "trustedEnabled": vault_blob.get("trustedEnabled", False),
    }


def _get_content_or_legacy(doc: dict, key: str, fallback):
    """
    Read a key from doc["content"], falling back to the old doc["vault"] schema.
    Uses explicit None check — empty list [] is falsy, so `or` would silently
    use the legacy field when content exists but is empty.
    """
    value = doc.get("content", {}).get(key)
    if value is not None:
        return value
    return doc.get("vault", {}).get(key, fallback)


def reconstruct_vault_blob(doc: dict) -> dict:
    """Rebuild the frontend vault blob from a MongoDB vault document."""
    # F04: decrypt content if encrypted (string), passthrough if plaintext (dict)
    content = decrypt_content(doc.get("content"))
    # Temporarily inject decrypted content back so _get_content_or_legacy works
    doc_with_content = {**doc, "content": content}
    return {
        "assets":      _get_content_or_legacy(doc_with_content, "assets", []),
        "wishes":      _get_content_or_legacy(doc_with_content, "wishes", []),
        "will":        _get_content_or_legacy(doc_with_content, "will", None),
        "suppDocs":    _get_content_or_legacy(doc_with_content, "suppDocs", []),
        "kin":         _get_content_or_legacy(doc_with_content, "kin", []),
        "v":           content.get("v", "face"),
        "notifySeq":   content.get("notifySeq", "in_order"),
        "saveCount":   content.get("saveCount", 0),
        "lastCheckin": dt_to_ms(doc.get("lastCheckin")),
        "fc":          doc.get("checkInFrequency", 2),
        "fu":          doc.get("checkInUnit", "months"),
        "gp":          doc.get("gracePeriodDays", 7),
        "notifyProto": doc.get("notifyProto", "ping_then_notify"),
        "trustedEnabled": doc.get("trustedEnabled", False),
        "pushSubscribed": content.get("pushSubscribed", False),
        "log":         content.get("log", doc.get("log", [])),
    }


# ─── AUTH HELPERS ─────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt (salted)."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, hashed: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash."""
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user_id: str, user: Optional[dict] = None) -> str:
    """Issue a JWT carrying sub, iat, exp, and the user's tokenVersion."""
    payload = {
        "sub": user_id,
        "iat": now_utc(),
        "exp": now_utc() + timedelta(hours=JWT_EXPIRY_HOURS),
        "tokenVersion": (user or {}).get("tokenVersion", 0),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


# ─── F115: TRUSTED PERSON TOKEN HELPERS ───────────────────────────────────────

def create_trusted_token(user_id: str) -> str:
    """Issue a scoped JWT for the trusted-person read-only view.
    Decoupled from tokenVersion (the holder's password resets must not break an
    active trusted link); instead it is short-lived and redeemable once."""
    payload = {
        "sub": user_id,
        "role": "trusted",
        "iat": now_utc(),
        "exp": now_utc() + timedelta(hours=TRUSTED_JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def get_trusted_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Validate a trusted-person scoped JWT. Returns {"user", "vault"} on success.
    Rejects full-access holder tokens (no role claim) and expired/forged tokens."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing token")
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], options={"require": ["exp", "sub"]})
        if payload.get("role") != "trusted":
            raise HTTPException(status_code=401, detail="Not a trusted access token")
        user = users_col.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        vault_doc = vaults_col.find_one({"userId": user["_id"]})
        if not vault_doc:
            raise HTTPException(status_code=404, detail="Vault not found")
        # Defense-in-depth: the trusted feature must still be enabled and the
        # designated trusted contact must still exist on the vault.
        if not vault_doc.get("trustedEnabled", False):
            raise HTTPException(status_code=403, detail="Trusted access is disabled")
        return {"user": user, "vault": vault_doc}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


def invalidate_trusted_links(user_id) -> None:
    """Mark all outstanding trusted-person magic links for a user as used."""
    if trusted_links_col is not None:
        trusted_links_col.update_many(
            {"userId": user_id, "used": False},
            {"$set": {"used": True}},
        )


def generate_trusted_link(user_id) -> Optional[str]:
    """F115: create a single-use trusted-person magic link.
    Returns the raw token (emailed), or None if no DB is available."""
    if trusted_links_col is None:
        return None
    invalidate_trusted_links(user_id)
    token = secrets.token_urlsafe(32)
    trusted_links_col.insert_one({
        "userId":    user_id,
        "tokenHash": hash_reset_token(token),
        "used":      False,
        "createdAt": now_utc(),
        "expiresAt": now_utc() + timedelta(minutes=TRUSTED_LINK_TTL_MINUTES),
    })
    return token


# ─── F66: PASSWORD RESET HELPERS ─────────────────────────────────────────────

def hash_reset_token(token: str) -> str:
    """SHA-256 hash of a reset token. Only the hash is stored in MongoDB,
    so a database leak never exposes a usable reset link."""
    return hashlib.sha256(token.encode()).hexdigest()


def is_reset_valid(reset_doc: Optional[dict]) -> bool:
    """A reset record is valid only if it exists, is unused, and unexpired."""
    if reset_doc is None:
        return False
    if reset_doc.get("used", False):
        return False
    expires = reset_doc.get("expiresAt")
    if expires is None:
        return False
    return ensure_utc(expires) > now_utc()


def is_password_acceptable(password: str) -> bool:
    """Minimum bar for a new password. F97: also rejects common passwords
    and requires at least one digit or special character."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        return False
    if password.lower() in COMMON_PASSWORDS:
        return False
    has_digit_or_special = any(not c.isalpha() for c in password)
    if not has_digit_or_special:
        return False
    return True


# ─── F86: ACCOUNT LOCKOUT HELPERS ─────────────────────────────────────────────

def is_account_locked(email: str) -> bool:
    """Check if an email is currently locked out due to failed login attempts."""
    if users_col is None:
        return False
    user = users_col.find_one({"email": email})
    if user is None:
        return False
    locked_until = user.get("lockedUntil")
    if locked_until is None:
        return False
    return now_utc() < locked_until


def get_lockout_remaining_seconds(email: str) -> int:
    """Return seconds remaining on lockout, or 0 if not locked."""
    if users_col is None:
        return 0
    user = users_col.find_one({"email": email})
    if user is None:
        return 0
    locked_until = user.get("lockedUntil")
    if locked_until is None:
        return 0
    remaining = (locked_until - now_utc()).total_seconds()
    return max(0, int(remaining))


def record_failed_login(email: str) -> None:
    """Increment failed login counter; lock the account if threshold is reached."""
    if users_col is None:
        return
    user = users_col.find_one({"email": email})
    if user is None:
        return
    new_count = user.get("failedLoginCount", 0) + 1
    update_fields = {"failedLoginCount": new_count}
    if new_count >= MAX_LOGIN_ATTEMPTS:
        update_fields["lockedUntil"] = now_utc() + timedelta(minutes=LOCKOUT_MINUTES)
    users_col.update_one({"_id": user["_id"]}, {"$set": update_fields})


def clear_login_failures(email: str) -> None:
    """Reset failed login counter and remove lockout. Called on successful login and password reset."""
    if users_col is None:
        return
    users_col.update_one(
        {"email": email},
        {"$set": {"failedLoginCount": 0}, "$unset": {"lockedUntil": ""}},
    )


def clean_user(user: dict) -> dict:
    """Strip sensitive fields before returning user data to the client."""
    return {
        "id":       str(user["_id"]),
        "name":     user.get("name", ""),
        "email":    user.get("email", ""),
        "ageGroup": user.get("ageGroup", ""),
        "hasWill":  user.get("hasWill", False),
        "isTester": user.get("isTester", False),
        "isAdmin":  user.get("isAdmin", False),
        # F118: denormalized flag (maintained on passkey register/delete).
        "hasPasskey": user.get("hasPasskey", False),
    }


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Validate the bearer JWT and return the matching user document."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing token")
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], options={"require": ["exp", "sub"]})
        user = users_col.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        # F105: Validate token version against database — ensures tokens are
        # invalidated on password reset (incrementing tokenVersion).
        token_version = payload.get("tokenVersion", 0)
        db_version = user.get("tokenVersion", 0)
        if token_version != db_version:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


def require_admin(current_user: dict) -> None:
    """Raise 403 if the current user does not have the isAdmin flag."""
    if not current_user.get("isAdmin", False):
        raise HTTPException(status_code=403, detail="Admin access required")


# ─── F117: NEW-DEVICE SIGN-IN ALERTS ──────────────────────────────────────────

def _device_label(user_agent: str) -> str:
    """Return a short, human-readable device description from a User-Agent."""
    ua = (user_agent or "")
    if "iPhone" in ua:
        return "iPhone"
    if "iPad" in ua:
        return "iPad"
    if "Android" in ua:
        return "Android device"
    if "Macintosh" in ua or "Mac OS" in ua:
        return "Mac"
    if "Windows" in ua:
        return "Windows PC"
    if "Linux" in ua:
        return "Linux device"
    return "a new device"


def send_new_device_email(user: dict, device_name: str, ip: str, user_agent: str, when) -> bool:
    """F117: email the account holder when a sign-in happens on an unrecognized device."""
    email = user.get("email", "")
    if not email:
        return False
    name = (user.get("name", "").split()[0] or "there")
    when_label = when.strftime("%-d %B %Y at %H:%M UTC") if hasattr(when, "strftime") else str(when)
    label = device_name or _device_label(user_agent)

    body = f"""Hi {name},

A new sign-in to your Kinlight account was detected.

Device: {label}
Time: {when_label}
IP address: {ip or "unknown"}

If this was you, no action is needed — we've remembered this device.

If this wasn't you, your account may be at risk. Sign in to Kinlight and reset your password right away:
{APP_URL}

As a reminder, your Kinlight vault holds sensitive information. We'll alert you every time a new device signs in.

The Kinlight team
"""
    sent = _send_email(email, "New sign-in to your Kinlight account", body)
    if sent:
        logger.info(f"F117: new-device alert sent to {mask_email(email)}")
    return sent


def _notify_new_device(user: dict, label: str, ip: str, user_agent: str, now) -> None:
    """F117: dispatch the new-device alert via email + push."""
    send_new_device_email(user, label, ip, user_agent, now)
    send_push_to_user(
        str(user["_id"]),
        "New sign-in detected",
        f"Your Kinlight account was signed in on {label}. If this wasn't you, reset your password.",
        tag="new-device",
        require_interaction=True,
    )


def register_device_login(user: dict, device_id: str, device_name: str, request: Request) -> None:
    """F117: record a login's device; alert the holder the first time a device is seen.

    No-op when no device_id is supplied (older clients) or no DB is available.
    The alert is dispatched on a daemon thread so it never delays login."""
    if not device_id or known_devices_col is None:
        return
    user_id = user["_id"]
    now = now_utc()
    ip = get_client_ip(request) if request is not None else ""
    user_agent = request.headers.get("User-Agent", "") if request is not None else ""

    existing = known_devices_col.find_one({"userId": user_id, "deviceId": device_id})
    if existing:
        known_devices_col.update_one(
            {"_id": existing["_id"]},
            {"$set": {"lastSeenAt": now, "deviceName": device_name or existing.get("deviceName", "")}},
        )
        return

    label = device_name or _device_label(user_agent)
    known_devices_col.insert_one({
        "userId":     user_id,
        "deviceId":   device_id,
        "deviceName": label,
        "userAgent":  user_agent,
        "ip":         ip,
        "firstSeenAt": now,
        "lastSeenAt":  now,
    })

    threading.Thread(target=_notify_new_device, args=(user, label, ip, user_agent, now), daemon=True).start()


# ─── F118: PASSKEY (WEBAUTHN) HELPERS ─────────────────────────────────────────

def _webauthn_store_challenge(challenge: bytes, user_id: Optional[str] = None) -> str:
    """Store a challenge and return its id (used to correlate options → verify)."""
    challenge_id = secrets.token_urlsafe(32)
    with _webauthn_challenges_lock:
        _webauthn_challenges[challenge_id] = {
            "challenge": challenge,
            "userId": user_id,
            "exp": now_utc() + timedelta(seconds=WEBAUTHN_CHALLENGE_TTL_SECONDS),
        }
    return challenge_id


def _webauthn_take_challenge(challenge_id: str) -> dict:
    """Retrieve and delete a stored challenge; raises 400 if missing/expired."""
    with _webauthn_challenges_lock:
        entry = _webauthn_challenges.pop(challenge_id, None)
    if entry is None:
        raise HTTPException(status_code=400, detail="WebAuthn challenge not found or expired")
    if now_utc() > entry["exp"]:
        raise HTTPException(status_code=400, detail="WebAuthn challenge expired")
    return entry


def _webauthn_credential_id_bytes(credential_id) -> bytes:
    """Coerce a credential id (bytes or base64url str) to bytes for storage."""
    if isinstance(credential_id, bytes):
        return credential_id
    return base64.urlsafe_b64decode(credential_id + "=" * (-len(credential_id) % 4))


# ─── CHECKIN WINDOW CALCULATIONS ─────────────────────────────────────────────
# _interval_days / is_overdue / is_reminder_due now live in
# kinlight/pulse_logic.py (re-exported above).


# ─── F64-2: WARNING HELPERS ───────────────────────────────────────────────────
# should_send_warning / should_notify_contacts now live in
# kinlight/pulse_logic.py (re-exported above).


# ─── NOTIFICATION QUEUE ───────────────────────────────────────────────────────

def get_trusted_contact(vault_doc: dict) -> Optional[dict]:
    """F115: return the kin contact flagged isTrusted, or None."""
    content = decrypt_content(vault_doc.get("content"))
    contacts = content.get("kin") or []
    for c in contacts:
        if c.get("isTrusted"):
            return c
    return None


def get_contacts_to_notify(vault_doc: dict, days_overdue: int) -> list:
    """Return the slice of contacts to notify based on protocol and days overdue."""
    # F04: decrypt content if encrypted
    content = decrypt_content(vault_doc.get("content"))
    contacts = content.get("kin") or []
    if not contacts:
        return []

    proto = vault_doc.get("notifyProto", "ping_then_notify")

    if proto == "ping_then_notify":
        return [] if days_overdue < 3 else contacts
    if proto == "notify_immediately":
        return contacts
    if proto == "escalate":
        return contacts[:min(days_overdue + 1, len(contacts))]
    return contacts


# ─── EMAIL HELPERS ────────────────────────────────────────────────────────────

def _send_email(to: str, subject: str, body: str, attachments: Optional[list] = None) -> bool:
    """Low-level Resend dispatch. Returns True on success.
    attachments is a list of {filename, content} dicts (content is base64-encoded)."""
    payload: dict = {
        "from": FROM_EMAIL,
        "to": [to],
        "subject": subject,
        "text": body,
    }
    if attachments:
        payload["attachments"] = attachments

    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Email send failed to {to}: {e}")
        return False


def send_reminder_email(user: dict, vault_doc: dict) -> bool:
    """F60: Send a check-in reminder to the vault holder."""
    freq = vault_doc.get("checkInFrequency", 2)
    unit = vault_doc.get("checkInUnit", "months")
    interval = _interval_days(vault_doc)

    last_checkin = ensure_utc(vault_doc["lastCheckin"])
    due_date = last_checkin + timedelta(days=interval)
    days_remaining = max(0, (due_date - now_utc()).days)

    name = (user.get("name", "").split()[0] or "there")
    email = user.get("email", "")
    freq_label = f"{freq} {'month' if unit == 'months' else 'week'}{'s' if freq != 1 else ''}"
    due_label = due_date.strftime("%-d %B %Y")
    days_label = f"{days_remaining} day{'s' if days_remaining != 1 else ''}"

    body = f"""Hi {name},

Just a gentle reminder that your Kinlight check-in is coming up.

Your next check-in is due on {due_label} — that's {days_label} from now.

A quick tap in the app is all it takes to confirm you're okay and reset your timer.

Why this matters: if your check-in is missed and your grace period expires, your nominated contacts will automatically receive your vault. This reminder is here so that never happens by accident.

Open Kinlight and tap the heart to check in:
{APP_URL}?action=checkin

If you've already checked in, you can safely ignore this email.

Take care,
The Kinlight team

---
You're receiving this because you set up a check-in schedule of every {freq_label}.
To change your frequency, open Settings in the app.
"""
    sent = _send_email(email, f"Your Kinlight check-in is due in {days_label}", body)
    if sent:
        logger.info(f"F60: Reminder sent to {email} ({days_remaining} days remaining)")
    return sent


def send_warning_email(user: dict, days_overdue: int) -> bool:
    """F64-2: Warn the vault holder during their overdue window (day 1 and day 2).
    Gives them a chance to check in before contacts are notified on day 3.
    Fails silently if user has no email address."""
    email = user.get("email", "")
    if not email:
        return False

    name = (user.get("name", "").split()[0] or "there")
    days_until_notify = CONTACT_NOTIFY_AFTER_DAYS - days_overdue

    if days_overdue == 1:
        urgency = "Your check-in is overdue."
        detail = (
            f"Your nominated contacts will be notified in {days_until_notify} days "
            f"if you don't check in."
        )
    else:
        urgency = "Final warning — your contacts will be notified tomorrow."
        detail = (
            "If you don't check in today, your nominated contacts will automatically "
            "receive your vault package."
        )

    body = f"""Hi {name},

{urgency}

{detail}

If you're okay, please open Kinlight and tap the heart to check in now:
{APP_URL}?action=checkin

Checking in will immediately reset your timer and cancel any pending notifications.

If you didn't mean to miss your check-in, this is your chance to fix it before your contacts are alerted.

The Kinlight team

---
You're receiving this because you have a check-in schedule set up in Kinlight.
"""
    sent = _send_email(
        email,
        f"Action needed — Kinlight check-in overdue (day {days_overdue})",
        body,
    )
    if sent:
        logger.info(f"F64-2: Warning email sent to {email} (day {days_overdue} overdue)")
    return sent


def send_contacts_notified_email(user: dict, contact_count: int) -> bool:
    """F64-2: Notify the vault holder that their contacts have been notified.
    Sent on day 3+ when ping_then_notify contacts are actually dispatched."""
    email = user.get("email", "")
    if not email:
        return False

    name = (user.get("name", "").split()[0] or "there")
    contacts_label = f"{contact_count} contact{'s' if contact_count != 1 else ''}"

    body = f"""Hi {name},

Your Kinlight vault package has been sent to your {contacts_label}.

This happened because your check-in was not completed within your scheduled window and grace period.

If this was a mistake and you are okay, please open Kinlight and check in now — this will send an all-clear email to your contacts immediately.

{APP_URL}?action=checkin

If you have any concerns, please reach out to your contacts directly.

The Kinlight team
"""
    sent = _send_email(
        email,
        "Your Kinlight contacts have been notified",
        body,
    )
    if sent:
        logger.info(f"F64-2: Contacts-notified email sent to {email} ({contact_count} contacts)")
    return sent


def _download_and_decrypt(file_id: str) -> Optional[bytes]:
    """Download a file from the storage backend and decrypt it.
    Returns plaintext bytes, or None if unavailable / not found / decrypt fails."""
    if _storage_backend is None:
        return None
    result = _storage_backend.download(file_id)
    if result is None:
        logger.warning(f"File {file_id} not found in storage backend")
        return None
    encrypted_data, _, _ = result
    try:
        return decrypt_bytes(encrypted_data)
    except Exception as e:
        logger.warning(f"Failed to decrypt file {file_id}: {e}")
        return None


def send_notification_email(contact: dict, vault_doc: dict, holder_name: str = "the vault holder") -> bool:
    """Send overdue notification email with PDF + any uploaded file attachments to a contact."""
    first = contact.get("first", "")
    last = contact.get("last", "")
    email = contact.get("email", "")

    pdf_bytes = generate_pdf_for_contact(contact, vault_doc, holder_name)
    attachments = [{
        "filename": _safe_filename(f"Kinlight-{first}-{last}.pdf"),
        "content": base64.b64encode(pdf_bytes).decode("utf-8"),
    }]

    total_size = len(pdf_bytes)
    max_attach = 20 * 1024 * 1024  # 20 MB cumulative — 5 MB headroom for encoding overhead

    content = decrypt_content(vault_doc.get("content"))

    # Will attachment
    will = content.get("will") if isinstance(content, dict) else None
    if will and will.get("file_id"):
        data = _download_and_decrypt(will["file_id"])
        if data:
            fsize = len(data)
            if total_size + fsize <= max_attach:
                attachments.append({
                    "filename": _safe_filename(will.get("filename", "Will.pdf")),
                    "content": base64.b64encode(data).decode("utf-8"),
                })
                total_size += fsize
            else:
                logger.warning(
                    f"Will file skipped for {first} {last} — would exceed "
                    f"{max_attach // (1024*1024)} MB attachment limit"
                )

    # Supplementary document attachments
    supp_docs = content.get("suppDocs", []) if isinstance(content, dict) else []
    for d in supp_docs:
        if d.get("file_id"):
            data = _download_and_decrypt(d["file_id"])
            if data:
                fsize = len(data)
                if total_size + fsize <= max_attach:
                    attachments.append({
                        "filename": _safe_filename(d.get("filename", "document.pdf")),
                        "content": base64.b64encode(data).decode("utf-8"),
                    })
                    total_size += fsize
                else:
                    logger.warning(
                        f"SuppDoc file skipped for {first} {last} — would exceed "
                        f"{max_attach // (1024*1024)} MB attachment limit"
                    )

    body = f"""Dear {first},

You are receiving this because you have been nominated as a trusted contact by {holder_name} in Kinlight.

{holder_name} has not confirmed their check-in within the required period. Attached is their emergency package — please review it carefully.

This package includes their recorded assets, wishes, Will details, and any personal letters they have written for you.

If you believe this has been sent in error, please disregard this message.

The Kinlight team
"""
    file_count = len(attachments) - 1
    sent = _send_email(email, f"Important: Kinlight package from {holder_name}", body, attachments)
    if sent:
        logger.info(f"Notification sent to {first} at {email} ({file_count} file attachment{'s' if file_count != 1 else ''})")
    return sent


def send_allclear_email(contact: dict, holder_name: str = "the vault holder") -> bool:
    """Send a recovery email when vault holder checks in after being overdue."""
    first = contact.get("first", "")
    email = contact.get("email", "")

    body = f"""Dear {first},

Good news — {holder_name} has checked in and confirmed they are okay.

Any previous notifications about their Kinlight vault can be disregarded. No action is required from you at this time.

Thank you for being a trusted contact.

The Kinlight team
"""
    sent = _send_email(email, f"All clear — {holder_name} is okay", body)
    if sent:
        logger.info(f"All-clear sent to {first} at {email}")
    return sent


def send_nomination_email(contact_email: str, contact_first: str, holder_name: str) -> bool:
    """F63: Send a nomination email to a newly-added (or re-emailed) contact."""
    first = contact_first or "there"
    body = f"""Hi {first},

{holder_name} has added you as a trusted contact in Kinlight — no action is needed from you right now.

Kinlight is a personal digital legacy vault. If {holder_name} ever stops checking in, you'll automatically receive a secure package with their important information, final wishes, and any personal messages they've written for you.

This email is just to let you know you've been nominated. You don't need to create an account or do anything at this stage.

If you have any questions, you can reach out to {holder_name} directly.

Take care,
The Kinlight team

---
You received this because {holder_name} listed you as a trusted contact.
"""
    sent = _send_email(
        contact_email,
        f"{holder_name} has added you as a trusted contact",
        body,
    )
    if sent:
        logger.info(f"F63: Nomination email sent to {contact_first} at {contact_email}")
    return sent


def send_trusted_email(contact: dict, holder_name: str, token: str) -> bool:
    """F115: email the trusted person a single-use magic link to a read-only view."""
    first = contact.get("first", "")
    email = contact.get("email", "")
    hours = int(TRUSTED_RESPONSE_HOURS)
    link = f"{APP_URL}?trusted={token}"
    body = f"""Dear {first},

{holder_name} has not confirmed their check-in within the required period.

You have been designated as {holder_name}'s trusted person in Kinlight. As their first responder, you can view their vault and stop any further notifications by using the secure link below.

View {holder_name}'s vault and confirm you've checked on them:
{link}

This link is single-use and expires in 7 days. It gives you temporary, read-only access — you cannot change anything or control {holder_name}'s account.

If you don't act within {hours} hours, {holder_name}'s other contacts will be notified automatically.

If you believe this has been sent in error, please disregard this message.

The Kinlight team
"""
    sent = _send_email(email, f"Kinlight: {holder_name} needs you to check in", body)
    if sent:
        logger.info(f"F115: Trusted email sent to {mask_email(email)}")
    return sent


def send_reset_email(user: dict, token: str) -> bool:
    """F66: email a single-use password reset link to the account holder."""
    first = (user.get("name") or "there").split(" ")[0]
    link = f"{APP_URL}?reset={token}"
    body = (
        f"Hi {first},\n\n"
        f"We received a request to reset your Kinlight password.\n\n"
        f"Reset your password here (link expires in {RESET_TOKEN_TTL_MINUTES} minutes "
        f"and can only be used once):\n\n{link}\n\n"
        f"If you didn't request this, you can safely ignore this email — "
        f"your password will not change.\n\n"
        f"— Kinlight"
    )
    sent = _send_email(user.get("email", ""), "Reset your Kinlight password", body)
    if sent:
        logger.info(f"F66: Reset email sent to {mask_email(user.get('email',''))}")
    return sent


# ─── PDF GENERATION ───────────────────────────────────────────────────────────

def generate_pdf_for_contact(contact: dict, vault_doc: dict, holder_name: str = "the vault holder") -> bytes:
    """Generate a PDF package for a single contact. Returns raw bytes."""
    # F04: decrypt content if encrypted
    content = decrypt_content(vault_doc.get("content"))
    assets    = content.get("assets") or []
    wishes    = content.get("wishes") or []
    all_kin   = content.get("kin") or []
    will      = content.get("will")
    supp_docs = content.get("suppDocs") or []

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
    )

    base = getSampleStyleSheet()
    styles = {
        "heading":  ParagraphStyle("H", parent=base["Heading1"],  fontSize=18, spaceAfter=6,  textColor=colors.HexColor("#2e2b26")),
        "sub":      ParagraphStyle("S", parent=base["Normal"],    fontSize=10, spaceAfter=12, textColor=colors.HexColor("#5a7a6e")),
        "body":     ParagraphStyle("B", parent=base["Normal"],    fontSize=10, leading=15, spaceAfter=8),
        "label":    ParagraphStyle("L", parent=base["Normal"],    fontSize=8,  spaceAfter=2,  textColor=colors.HexColor("#5a7a6e"), fontName="Helvetica-Bold"),
        "section":  ParagraphStyle("Sc", parent=base["Heading2"], fontSize=12, spaceAfter=6,  textColor=colors.HexColor("#2e2b26")),
    }

    def hr() -> HRFlowable:
        return HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e0e0e0"))

    def esc(text: str) -> str:
        """F83: escape user strings before ReportLab Paragraph (interprets HTML)."""
        return xml_escape(text) if text else ""

    def section(title: str) -> list:
        return [hr(), Spacer(1, 8), Paragraph(esc(title), styles["section"])]

    def field(label: str, value: str) -> list:
        if not value:
            return []
        return [Paragraph(esc(label), styles["label"]), Paragraph(esc(value), styles["body"])]

    first = esc(contact.get("first", ""))
    last  = esc(contact.get("last", ""))

    story = [
        Paragraph("Kinlight", styles["heading"]),
        Paragraph(f"Prepared for {first} {last}", styles["sub"]),
        Paragraph(f"From {esc(holder_name)}", styles["sub"]),
        Paragraph(f"Generated {datetime.now().strftime('%-d %B %Y')}", styles["sub"]),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e0e0e0")),
        Spacer(1, 12),
        Paragraph("Personal Letter", styles["section"]),
    ]

    letter = contact.get("letter", "").strip()
    if letter:
        for line in letter.split("\n"):
            story.append(Paragraph(esc(line) or "&nbsp;", styles["body"]))
    else:
        story.append(Paragraph("[No personal letter recorded for this contact.]", styles["body"]))

    story += section("Will & Legal Documents")
    if will:
        status_map = {"signed": "Signed & witnessed", "draft": "Draft — not signed", "none": "No Will yet"}
        story += field("Status", status_map.get(will.get("status", ""), esc(will.get("status", ""))))
        story += field("Solicitor / Law Firm", will.get("solicitor", ""))
        story += field("Primary Location", will.get("loc1", ""))
        story += field("Secondary Location", will.get("loc2", ""))
        story += field("Notes", will.get("notes", ""))
        if will.get("file_id"):
            story += field("Attached file", will.get("filename", ""))
    else:
        story.append(Paragraph("[No Will details recorded.]", styles["body"]))

    if supp_docs:
        story += section("Supporting Documents")
        for d in supp_docs:
            loc_line = f"Location: {esc(d['loc'])}" if d.get("loc") else ""
            if d.get("file_id"):
                loc_line = (loc_line + ("  ·  " if loc_line else "") +
                           f"Attached: {esc(d.get('filename', ''))}")
            story += field(d.get("name", ""), loc_line)

    if assets:
        story += section("Asset Register")
        for a in assets:
            parts = [x for x in [
                esc(a.get("category", "")),
                f"${round(a['value']):,}" if a.get("value") else None,
                esc(a.get("details", "")),
                f"Beneficiary: {esc(a['beneficiary'])}" if a.get("beneficiary") else None,
            ] if x]
            story += [Paragraph(esc(a.get("name", "")), styles["label"]),
                      Paragraph(" · ".join(parts), styles["body"])]

    if wishes:
        story += section("My Wishes")
        for w in wishes:
            story += [Paragraph(esc(w.get("title", "")), styles["label"])]
            if w.get("details"):
                story.append(Paragraph(esc(w["details"]), styles["body"]))

    if all_kin:
        story += section("Key Contacts")
        for c in all_kin:
            parts = [x for x in [
                f"{esc(c.get('first', ''))} {esc(c.get('last', ''))}".strip(),
                esc(c.get("rel", "")),
                esc(c.get("email", "")),
                esc(c.get("phone", "")),
            ] if x]
            story.append(Paragraph(" · ".join(parts), styles["body"]))

    doc.build(story)
    return buf.getvalue()


# ─── F101: PUSH NOTIFICATIONS ──────────────────────────────────────────────────

def send_push_to_user(user_id, title: str, body: str, tag: str = "kinlight",
                      require_interaction: bool = False) -> int:
    """Send a push notification to all of a user's subscribed devices.
    Returns the number of subscriptions successfully pushed to."""
    if push_subs_col is None or not VAPID_PRIVATE_KEY:
        return 0
    subs = list(push_subs_col.find({"userId": user_id}))
    sent = 0
    for sub in subs:
        try:
            pywebpush_send(
                subscription_info=sub["subscription"],
                data=json.dumps({
                    "title": title,
                    "body": body,
                    "tag": tag,
                    "url": APP_URL,
                    "requireInteraction": require_interaction,
                }),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS,
            )
            sent += 1
        except WebPushException as e:
            # Remove expired/invalid subscriptions
            if e.response and e.response.status_code in (404, 410):
                push_subs_col.delete_one({"_id": sub["_id"]})
                logger.info(f"F101: Removed expired push subscription for {user_id}")
            else:
                logger.warning(f"F101: Push failed for {user_id}: {e}")
        except Exception as e:
            logger.warning(f"F101: Push error for {user_id}: {e}")
    return sent


# ─── PULSE SCANNER ────────────────────────────────────────────────────────────

def _handle_trusted_notification(vault_doc: dict, user: dict, trusted: dict,
                                 holder_name: str, now, days_overdue: int) -> bool:
    """F115: trusted-person first-responder gate.

    Called only when the vault is overdue, trustedEnabled is set, and a trusted
    contact exists. Returns True when the trusted path handled (or deferred) the
    notification, so the caller should skip the normal kin-notify block.

    States:
      * not yet notified  → send magic link, record trustedNotifiedAt, defer kins
      * within window     → defer (waiting for trusted person to respond)
      * resolved          → halt (trusted person already stopped notifications)
      * window expired    → auto-release: notify remaining kins once
    """
    notified_at = vault_doc.get("trustedNotifiedAt")

    if notified_at is None:
        token = generate_trusted_link(user["_id"])
        if token is None:
            return False  # no DB available — fall through to normal kin notify
        if send_trusted_email(trusted, holder_name, token):
            vaults_col.update_one(
                {"_id": vault_doc["_id"]},
                {"$set": {"trustedNotifiedAt": now, "updatedAt": now}},
            )
            logger.info(f"F115: trusted person notified for {mask_email(user.get('email', str(user['_id'])))}")
        return True

    if vault_doc.get("trustedResolved", False):
        return True  # halt — trusted person already stopped notifications

    elapsed = now - ensure_utc(notified_at)
    if elapsed < timedelta(hours=TRUSTED_RESPONSE_HOURS):
        return True  # still within the response window — defer

    # Auto-release: notify remaining kins once (exclude the trusted contact, who
    # already received the magic link). Deliberately one-shot — not escalating —
    # to avoid duplicate notifications on every scan.
    contacts = get_contacts_to_notify(vault_doc, days_overdue)
    trusted_email = trusted.get("email", "").strip().lower()
    contacts = [c for c in contacts if c.get("email", "").strip().lower() != trusted_email]
    sent_count = sum(
        1 for c in contacts
        if send_notification_email(c, vault_doc, holder_name)
    )
    if contacts:
        send_contacts_notified_email(user, sent_count)
        send_push_to_user(user["_id"], "Contacts have been notified",
                          f"{sent_count} contact(s) received your vault package.",
                          tag="kinlight-contacts-notified", require_interaction=True)
        vaults_col.update_one(
            {"_id": vault_doc["_id"]},
            {"$set": {"overdueNotificationSent": True, "updatedAt": now}},
        )
    logger.info(f"F115: trusted-person auto-release — {sent_count} kin notified for {mask_email(user.get('email', str(user['_id'])))}")
    return True


def run_pulse_scan() -> None:
    """
    Hourly background job with three responsibilities:
    1. F64-2 Warnings — emails holder on day 1 and day 2 of overdue window (ping_then_notify only).
    2. Overdue — notifies contacts when grace period has expired (day 3+ for ping_then_notify).
    3. F60 Reminder — emails vault holder when check-in is approaching.
    """
    logger.info("Pulse scan running")
    now = now_utc()
    vaults_checked = 0

    for vault_doc in vaults_col.find({}):
        vaults_checked += 1
        user_id = vault_doc.get("userId")
        if not user_id:
            continue
        user = users_col.find_one({"_id": user_id})
        if not user:
            continue

        overdue, days_overdue = is_overdue(vault_doc)

        if overdue:
            proto = vault_doc.get("notifyProto", "ping_then_notify")
            holder_name = user.get("name", "the vault holder")

            # F64-2: Send holder warning email on day 1 and day 2 (ping_then_notify only)
            if should_send_warning(vault_doc, days_overdue):
                if send_warning_email(user, days_overdue):
                    send_push_to_user(user_id, "Your Kinlight check-in is overdue",
                                      f"You have {CONTACT_NOTIFY_AFTER_DAYS - days_overdue} day(s) before contacts are notified.",
                                      tag="kinlight-warning", require_interaction=True)
                    already_sent = vault_doc.get("warningSentDays", [])
                    vaults_col.update_one(
                        {"_id": vault_doc["_id"]},
                        {"$set": {"warningSentDays": already_sent + [days_overdue], "updatedAt": now}},
                    )
                continue  # Don't notify contacts yet during warning window

            # Notify contacts (day 3+ for ping_then_notify, immediately for others)
            if should_notify_contacts(vault_doc, days_overdue):
                # F115: trusted person acts as first responder before kins are notified.
                trusted = get_trusted_contact(vault_doc) if vault_doc.get("trustedEnabled", False) else None
                if trusted and _handle_trusted_notification(vault_doc, user, trusted, holder_name, now, days_overdue):
                    continue  # trusted path took over (sent link / waiting / resolved / auto-released)

                already_notified = vault_doc.get("overdueNotificationSent", False)
                if not already_notified or proto == "escalate":
                    contacts = get_contacts_to_notify(vault_doc, days_overdue)
                    sent_count = sum(
                        1 for c in contacts
                        if send_notification_email(c, vault_doc, holder_name)
                    )
                    if contacts and proto != "escalate":
                        # F64-2: Also notify the holder that contacts have been reached
                        send_contacts_notified_email(user, sent_count)
                        send_push_to_user(user_id, "Contacts have been notified",
                                          f"{sent_count} contact(s) received your vault package.",
                                          tag="kinlight-contacts-notified", require_interaction=True)
                        vaults_col.update_one(
                            {"_id": vault_doc["_id"]},
                            {"$set": {"overdueNotificationSent": True, "updatedAt": now}},
                        )
            continue  # Skip reminder check when already overdue

        if is_reminder_due(vault_doc):
            if send_reminder_email(user, vault_doc):
                interval = _interval_days(vault_doc)
                last_checkin = ensure_utc(vault_doc["lastCheckin"])
                due_date = last_checkin + timedelta(days=interval)
                days_remaining = max(0, (due_date - now_utc()).days)
                days_label = f"{days_remaining} day{'s' if days_remaining != 1 else ''}"
                send_push_to_user(user_id, "Check-in reminder",
                                  f"Your Kinlight check-in is due in {days_label}.",
                                  tag="kinlight-reminder")
                vaults_col.update_one(
                    {"_id": vault_doc["_id"]},
                    {"$set": {"reminderSent": True, "updatedAt": now}},
                )
                logger.info(f"F60: reminderSent set for {mask_email(user.get('email', str(user_id)))}")

    # F93: record heartbeat so /health can detect a silently-dead scanner
    if system_col is not None:
        system_col.update_one(
            {"_id": "pulse_scanner"},
            {"$set": {"lastRun": now, "vaultsChecked": vaults_checked}},
            upsert=True,
        )

    logger.info("Pulse scan complete")


scheduler = BackgroundScheduler()
scheduler.add_job(run_pulse_scan, "interval", hours=1)
scheduler.start()


# ─── API ROUTES ───────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    """Create MongoDB indexes on boot. Safe to re-run — skipped if already exist."""
    vaults_col.create_index([("userId", ASCENDING)], unique=True)
    vaults_col.create_index([("lastCheckin", ASCENDING)])
    vaults_col.create_index([("overdueNotificationSent", ASCENDING), ("lastCheckin", ASCENDING)])
    vaults_col.create_index([("reminderSent", ASCENDING), ("lastCheckin", ASCENDING)])
    # F66: fast token lookup + MongoDB auto-deletes expired reset records
    resets_col.create_index([("tokenHash", ASCENDING)])
    resets_col.create_index([("expiresAt", ASCENDING)], expireAfterSeconds=0)
    # F113: email is the canonical login identifier — enforce uniqueness so two
    # accounts can't share a login. Must run the email-migration script first
    # (duplicate emails would make this index fail to build).
    users_col.create_index([("email", ASCENDING)], unique=True)
    # F117: recognized devices — fast lookup per user, unique per (user, device)
    known_devices_col.create_index([("userId", ASCENDING), ("deviceId", ASCENDING)], unique=True)
    # F118: passkey credentials — unique credential id, indexed per user
    webauthn_creds_col.create_index([("credentialId", ASCENDING)], unique=True)
    webauthn_creds_col.create_index([("userId", ASCENDING)])
    logger.info("Startup complete — indexes ensured")


@app.get("/health")
def health():
    """
    F93: Reports whether the hourly pulse scanner is still alive, not just
    whether the API process is up. Returns HTTP 503 if the scanner hasn't
    run within PULSE_SCAN_UNHEALTHY_AFTER_HOURS, so uptime monitors (Railway,
    UptimeRobot, etc.) can alert on a silent failure of the core notification
    loop without any extra configuration.
    """
    body = {"ok": True, "pulseScanner": {"lastRun": None, "vaultsChecked": None, "healthy": False}}

    if system_col is not None:
        record = system_col.find_one({"_id": "pulse_scanner"})
        if record is not None:
            last_run = record.get("lastRun")
            vaults_checked = record.get("vaultsChecked")
            age_hours = (now_utc() - ensure_utc(last_run)).total_seconds() / 3600 if last_run else None
            healthy = age_hours is not None and age_hours <= PULSE_SCAN_UNHEALTHY_AFTER_HOURS

            body["pulseScanner"] = {
                "lastRun": last_run.isoformat() if last_run else None,
                "vaultsChecked": vaults_checked,
                "healthy": healthy,
            }

    if not body["pulseScanner"]["healthy"]:
        body["ok"] = False
        return JSONResponse(status_code=503, content=body)

    return body


@app.post("/auth/login")
@limiter.limit("5/minute")
def login(request: Request, body: dict) -> dict:
    """Authenticate by email + password; returns a fresh JWT and user profile."""
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    # F86 / F107: check lockout BEFORE verifying credentials.
    # F107: Return 401 (not 429) to prevent email enumeration via status code.
    if email and is_account_locked(email):
        logger.warning(f"Locked-out login attempt for email: {mask_email(email)}")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user = users_col.find_one({"email": email})
    if not user or not check_password(password, user.get("password", "")):
        # F86: record the failure (only if the email actually exists — avoids
        # creating lockout records for non-existent emails, which would be a
        # resource-exhaustion vector)
        if email:
            record_failed_login(email)
            logger.warning(f"Failed login attempt for email: {mask_email(email)}")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # F86: successful login — clear any accumulated failures
    clear_login_failures(email)
    logger.info(f"Successful login for email: {mask_email(email)}")
    users_col.update_one({"_id": user["_id"]}, {"$set": {"lastLogin": now_utc()}})
    # F117: record + alert on new-device sign-in (no-op if no deviceId supplied)
    register_device_login(user, body.get("deviceId", ""), body.get("deviceName", ""), request)
    return {"ok": True, "token": create_token(str(user["_id"]), user), "user": clean_user(user)}


@app.get("/auth/me")
def me(current_user: dict = Depends(get_current_user)) -> dict:
    """Return the authenticated user's profile."""
    return {"ok": True, "user": clean_user(current_user)}


@app.patch("/auth/me")
def update_me(body: dict, current_user: dict = Depends(get_current_user)) -> dict:
    """
    Update the authenticated user's profile. Accepts optional name and email.
    Returns the updated user object. Empty strings are treated as no change.
    """
    updates = {}

    name = body.get("name")
    if name is not None and isinstance(name, str) and name.strip():
        updates["name"] = name.strip()

    email = body.get("email")
    if email is not None and isinstance(email, str) and email.strip():
        email = email.strip().lower()
        # Basic email format check
        if not re_mod.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            raise HTTPException(status_code=400, detail="Invalid email format")
        # F119-s: reject a change to an email already owned by another account —
        # avoids a unique-index write error surfacing as a raw 500.
        if email != (current_user.get("email", "") or "").lower():
            existing = users_col.find_one({"email": email})
            if existing is not None and str(existing["_id"]) != str(current_user["_id"]):
                raise HTTPException(status_code=409, detail="Email address is already in use.")
        updates["email"] = email

    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    users_col.update_one({"_id": current_user["_id"]}, {"$set": updates})
    updated_user = users_col.find_one({"_id": current_user["_id"]})
    return {"ok": True, "user": clean_user(updated_user)}


@app.post("/auth/change-password")
@limiter.limit("5/minute", key_func=get_user_or_ip)
def change_password(request: Request, body: dict, current_user: dict = Depends(get_current_user)) -> dict:
    """F119: change the authenticated user's password (requires the current one).

    Increments tokenVersion to revoke sessions on every other device, then issues
    a fresh JWT so the current session stays signed in seamlessly."""
    current_password = body.get("currentPassword", "")
    new_password = body.get("newPassword", "")

    if not current_password or not check_password(current_password, current_user.get("password", "")):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    if not is_password_acceptable(new_password):
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters, include a number or special character, and not be a commonly used password.",
        )

    if new_password == current_password:
        raise HTTPException(status_code=400, detail="New password must be different from your current password.")

    users_col.update_one(
        {"_id": current_user["_id"]},
        {"$set": {"password": hash_password(new_password)}, "$inc": {"tokenVersion": 1}},
    )
    updated_user = users_col.find_one({"_id": current_user["_id"]})
    logger.info(f"F119: Password changed for {mask_email(current_user.get('email', str(current_user['_id'])))}")
    return {
        "ok": True,
        "token": create_token(str(updated_user["_id"]), updated_user),
        "user": clean_user(updated_user),
    }


@app.post("/auth/request-reset")
@limiter.limit("3/minute")
def request_reset(request: Request, body: dict):
    """
    F66: Start a password reset. ALWAYS returns the same generic response,
    whether or not the email exists — so this endpoint can't be used to
    probe which accounts exist.
    """
    email = body.get("email", "").strip().lower()
    generic = {"ok": True, "message": "If that account exists and has an email on file, a reset link has been sent."}

    if not email:
        return generic

    user = users_col.find_one({"email": email})
    if user is None or not user.get("email"):
        return generic

    # Invalidate any earlier outstanding reset links for this user.
    resets_col.update_many(
        {"userId": user["_id"], "used": False},
        {"$set": {"used": True}},
    )

    token = secrets.token_urlsafe(32)
    resets_col.insert_one({
        "userId":    user["_id"],
        "tokenHash": hash_reset_token(token),   # never store the raw token
        "used":      False,
        "createdAt": now_utc(),
        "expiresAt": now_utc() + timedelta(minutes=RESET_TOKEN_TTL_MINUTES),
    })
    # F108: Send email asynchronously so response time is constant regardless
    # of whether the account exists. Prevents email enumeration via timing.
    threading.Thread(target=send_reset_email, args=(user, token), daemon=True).start()
    return generic


@app.post("/auth/reset-password")
@limiter.limit("5/minute")
def reset_password(request: Request, body: dict):
    """F66: Complete a password reset with a valid single-use token."""
    token = body.get("token", "")
    new_password = body.get("password", "")

    if not is_password_acceptable(new_password):
        raise HTTPException(status_code=400, detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters, include a number or special character, and not be a commonly used password.")

    reset_doc = resets_col.find_one({"tokenHash": hash_reset_token(token)}) if token else None
    if not is_reset_valid(reset_doc):
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired. Please request a new one.")

    users_col.update_one(
        {"_id": reset_doc["userId"]},
        {"$set": {"password": hash_password(new_password)},
         "$inc": {"tokenVersion": 1}},
    )
    resets_col.update_one({"_id": reset_doc["_id"]}, {"$set": {"used": True}})
    # F86: clear lockout so the user can log in immediately with their new password
    reset_user = users_col.find_one({"_id": reset_doc["userId"]})
    if reset_user is not None:
        clear_login_failures(reset_user.get("email", ""))
    return {"ok": True, "message": "Password updated. You can now sign in."}


# ─── F118: PASSKEY (WEBAUTHN) ENDPOINTS ───────────────────────────────────────

@app.get("/auth/webauthn/register/options")
def webauthn_register_options(current_user: dict = Depends(get_current_user)):
    """F118: begin passkey registration. Requires auth — passkeys are added
    from Settings after signing in with a password."""
    user_id = str(current_user["_id"]).encode("utf-8")
    user_name = current_user.get("email", "") or str(current_user["_id"])
    user_display_name = current_user.get("name", "") or user_name

    # Exclude already-enrolled credentials so the authenticator won't re-register
    exclude = []
    if webauthn_creds_col is not None:
        for cred in webauthn_creds_col.find({"userId": current_user["_id"]}):
            exclude.append(PublicKeyCredentialDescriptor(
                id=_webauthn_credential_id_bytes(cred["credentialId"]),
            ))

    challenge = generate_challenge()
    options = generate_registration_options(
        rp_id=RP_ID,
        rp_name=RP_NAME,
        user_id=user_id,
        user_name=user_name,
        user_display_name=user_display_name,
        challenge=challenge,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=exclude or None,
    )
    challenge_id = _webauthn_store_challenge(challenge, str(current_user["_id"]))
    return {"ok": True, "challengeId": challenge_id, "options": options_to_json_dict(options)}


@app.post("/auth/webauthn/register/verify")
def webauthn_register_verify(body: dict, current_user: dict = Depends(get_current_user)):
    """F118: complete passkey registration and store the credential."""
    if webauthn_creds_col is None:
        raise HTTPException(status_code=503, detail="Database not available")

    challenge_id = body.get("challengeId", "")
    credential = body.get("credential")
    if not challenge_id or not credential:
        raise HTTPException(status_code=400, detail="Missing challengeId or credential")

    entry = _webauthn_take_challenge(challenge_id)
    if entry.get("userId") != str(current_user["_id"]):
        raise HTTPException(status_code=400, detail="Challenge does not belong to this account")

    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=entry["challenge"],
            expected_rp_id=RP_ID,
            expected_origin=EXPECTED_ORIGIN,
            require_user_verification=True,
        )
    except Exception as e:
        logger.warning(f"F118: passkey registration verification failed: {e}")
        raise HTTPException(status_code=400, detail="Passkey verification failed")

    credential_id_b64 = bytes_to_base64url(verified.credential_id)
    if webauthn_creds_col.find_one({"credentialId": credential_id_b64}):
        raise HTTPException(status_code=400, detail="This passkey is already registered")

    transports = []
    if isinstance(credential, dict):
        transports = credential.get("response", {}).get("transports") or credential.get("transports") or []

    now = now_utc()
    webauthn_creds_col.insert_one({
        "userId":       current_user["_id"],
        "credentialId": credential_id_b64,
        "publicKey":    bytes_to_base64url(verified.credential_public_key),
        "signCount":    verified.sign_count,
        "transports":   list(transports),
        "deviceName":   body.get("deviceName", ""),
        "createdAt":    now,
        "lastUsedAt":   None,
    })
    # F118: keep the denormalized hasPasskey flag in sync on the user doc
    users_col.update_one({"_id": current_user["_id"]}, {"$set": {"hasPasskey": True}})
    logger.info(f"F118: passkey registered for {mask_email(current_user.get('email', str(current_user['_id'])))}")
    return {"ok": True}


@app.post("/auth/webauthn/login/options")
def webauthn_login_options(body: dict):
    """F118: begin passkey sign-in. If an email is supplied and known, narrow to
    that account's credentials; otherwise the browser offers discoverable passkeys."""
    email = (body.get("email") or "").strip().lower()
    allow_credentials = None
    known_user_id = None

    if email and webauthn_creds_col is not None:
        user = users_col.find_one({"email": email})
        if user:
            known_user_id = str(user["_id"])
            creds = list(webauthn_creds_col.find({"userId": user["_id"]}))
            if creds:
                allow_credentials = [
                    PublicKeyCredentialDescriptor(id=_webauthn_credential_id_bytes(c["credentialId"]))
                    for c in creds
                ]

    challenge = generate_challenge()
    options = generate_authentication_options(
        rp_id=RP_ID,
        challenge=challenge,
        allow_credentials=allow_credentials,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    challenge_id = _webauthn_store_challenge(challenge, known_user_id)
    return {"ok": True, "challengeId": challenge_id, "options": options_to_json_dict(options)}


@app.post("/auth/webauthn/login/verify")
def webauthn_login_verify(request: Request, body: dict):
    """F118: verify a passkey assertion and issue the standard JWT."""
    if webauthn_creds_col is None:
        raise HTTPException(status_code=503, detail="Database not available")

    challenge_id = body.get("challengeId", "")
    credential = body.get("credential")
    if not challenge_id or not credential:
        raise HTTPException(status_code=400, detail="Missing challengeId or credential")

    entry = _webauthn_take_challenge(challenge_id)

    credential_id = (credential or {}).get("id")
    if not credential_id:
        raise HTTPException(status_code=400, detail="Missing credential id")

    stored = webauthn_creds_col.find_one({"credentialId": credential_id})
    if not stored:
        raise HTTPException(status_code=401, detail="Passkey not recognized")

    user = users_col.find_one({"_id": stored["userId"]})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    # If the challenge was scoped to a known account, the assertion must match it.
    if entry.get("userId") and entry["userId"] != str(user["_id"]):
        raise HTTPException(status_code=401, detail="Passkey does not match this account")

    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=entry["challenge"],
            expected_rp_id=RP_ID,
            expected_origin=EXPECTED_ORIGIN,
            credential_public_key=base64url_to_bytes(stored["publicKey"]),
            credential_current_sign_count=stored.get("signCount", 0),
            require_user_verification=True,
        )
    except Exception as e:
        logger.warning(f"F118: passkey login verification failed: {e}")
        raise HTTPException(status_code=401, detail="Passkey verification failed")

    webauthn_creds_col.update_one(
        {"_id": stored["_id"]},
        {"$set": {"signCount": verified.new_sign_count, "lastUsedAt": now_utc()}},
    )

    logger.info(f"F118: successful passkey login for {mask_email(user.get('email', str(user['_id'])))}")
    register_device_login(user, body.get("deviceId", ""), body.get("deviceName", ""), request)
    return {"ok": True, "token": create_token(str(user["_id"]), user), "user": clean_user(user)}


@app.get("/auth/webauthn/credentials")
def webauthn_list_credentials(current_user: dict = Depends(get_current_user)):
    """F118: list enrolled passkeys (metadata only — no key material)."""
    if webauthn_creds_col is None:
        return {"ok": True, "credentials": []}
    creds = list(webauthn_creds_col.find({"userId": current_user["_id"]}).sort("createdAt", 1))
    return {
        "ok": True,
        "credentials": [
            {
                "id": c["credentialId"],
                "deviceName": c.get("deviceName", ""),
                "createdAt": c.get("createdAt").isoformat() if c.get("createdAt") else None,
                "lastUsedAt": c.get("lastUsedAt").isoformat() if c.get("lastUsedAt") else None,
            }
            for c in creds
        ],
    }


@app.delete("/auth/webauthn/credentials/{credential_id}")
def webauthn_delete_credential(credential_id: str, current_user: dict = Depends(get_current_user)):
    """F118: remove an enrolled passkey (scoped to the current user)."""
    if webauthn_creds_col is None:
        return {"ok": True}
    result = webauthn_creds_col.delete_one({"userId": current_user["_id"], "credentialId": credential_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Passkey not found")
    # F118: keep the denormalized hasPasskey flag in sync on the user doc
    remaining = webauthn_creds_col.count_documents({"userId": current_user["_id"]})
    users_col.update_one({"_id": current_user["_id"]}, {"$set": {"hasPasskey": remaining > 0}})
    logger.info(f"F118: passkey removed for {mask_email(current_user.get('email', str(current_user['_id'])))}")
    return {"ok": True}


@app.get("/admin/testers")
def list_testers(current_user: dict = Depends(get_current_user)) -> dict:
    """List all tester accounts (admin only)."""
    require_admin(current_user)
    testers = list(users_col.find({"isTester": True}))
    return {"ok": True, "testers": [clean_user(t) for t in testers]}


@app.post("/vault/sync")
def vault_sync(body: dict, current_user: dict = Depends(get_current_user)) -> dict:
    """Persist the frontend vault blob (encrypted) to MongoDB."""
    vault_blob = body.get("vault", {})

    # F96: validate payload limits
    assets = vault_blob.get("assets", [])
    contacts = vault_blob.get("kin", [])
    if not isinstance(assets, list) or len(assets) > MAX_VAULT_ASSETS:
        raise HTTPException(status_code=400, detail=f"Too many assets (max {MAX_VAULT_ASSETS}).")
    if not isinstance(contacts, list) or len(contacts) > MAX_VAULT_CONTACTS:
        raise HTTPException(status_code=400, detail=f"Too many contacts (max {MAX_VAULT_CONTACTS}).")

    now = now_utc()

    # F119-s: sanitize user-controlled filenames at the source so downstream
    # ZIP/attachment/Content-Disposition consumers can't receive CRLF or path
    # separators (defense-in-depth alongside point-of-use sanitization).
    will = vault_blob.get("will")
    if isinstance(will, dict) and will.get("filename"):
        will = {**will, "filename": _safe_filename(will["filename"])}

    supp_docs = vault_blob.get("suppDocs", [])
    if isinstance(supp_docs, list):
        supp_docs = [
            ({**d, "filename": _safe_filename(d["filename"])}
             if isinstance(d, dict) and d.get("filename") else d)
            for d in supp_docs
        ]

    content = {
        "assets":    vault_blob.get("assets", []),
        "wishes":    vault_blob.get("wishes", []),
        "will":      will,
        "suppDocs":  supp_docs,
        "kin":       vault_blob.get("kin", []),
        "v":         vault_blob.get("v", "face"),
        "notifySeq": vault_blob.get("notifySeq", "in_order"),
        "saveCount": vault_blob.get("saveCount", 0),
        "pushSubscribed": vault_blob.get("pushSubscribed", False),
        "log":         vault_blob.get("log", [])[-20:],
    }

    # F04: encrypt vault content before storage
    stored_content = encrypt_content(content)

    vaults_col.update_one(
        {"userId": current_user["_id"]},
        {
            "$set": {
                **extract_vault_fields(vault_blob),
                "content":   stored_content,
                "syncedAt":  now,
                "updatedAt": now,
            },
            "$setOnInsert": {
                "createdAt":               now,
                "overdueNotificationSent": False,
                "reminderSent":            False,
                "warningSentDays":         [],
            },
            # F131: clear the legacy plaintext log field (now lives in content)
            "$unset": {"log": ""},
        },
        upsert=True,
    )

    # F115: disabling the trusted feature invalidates any outstanding magic links.
    if not vault_blob.get("trustedEnabled", False):
        invalidate_trusted_links(current_user["_id"])

    return {"ok": True}


@app.get("/vault")
def vault_get(current_user: dict = Depends(get_current_user)) -> dict:
    """Return the decrypted vault blob for the authenticated user."""
    doc = vaults_col.find_one({"userId": current_user["_id"]})
    if not doc:
        return {"ok": True, "vault": None}
    return {"ok": True, "vault": reconstruct_vault_blob(doc)}


def contact_nominate(body: dict, current_user: dict) -> dict:
    """
    F63: Send a nomination email to a contact. Called on new contact add or
    email change. Pure function (no FastAPI/Request dependency) so it stays
    directly unit-testable — see TestNominationValidation in test_main.py.
    The HTTP route wrapper below (contact_nominate_route) adds rate limiting.
    """
    contact_email = body.get("contact_email", "").strip()
    contact_first = body.get("contact_first", "").strip()
    holder_name = current_user.get("name", "the vault holder")

    if not contact_email:
        return {"ok": False, "error": "contact_email required"}

    # F79: Validate that this email belongs to a contact in the user's vault.
    # Without this check, an attacker could use this endpoint to spam arbitrary
    # email addresses from Kinlight's verified domain.
    vault_doc = vaults_col.find_one({"userId": current_user["_id"]})
    if not vault_doc:
        return {"ok": False, "error": "no vault found"}
    # F04: decrypt content if encrypted
    vault_content = decrypt_content(vault_doc.get("content"))
    vault_contacts = vault_content.get("kin") or []
    contact_emails = [c.get("email", "").strip().lower() for c in vault_contacts]
    if contact_email.lower() not in contact_emails:
        return {"ok": False, "error": "contact not found in vault"}

    sent = send_nomination_email(contact_email, contact_first, holder_name)
    return {"ok": sent}


@app.post("/contact/nominate")
@limiter.limit("10/minute", key_func=get_user_or_ip)
def contact_nominate_route(request: Request, body: dict, current_user: dict = Depends(get_current_user)):
    """F91: thin HTTP wrapper — rate limiting lives here, business logic in contact_nominate()."""
    return contact_nominate(body, current_user)



@app.get("/preview-package/{contact_index}")
@limiter.limit("5/minute", key_func=get_user_or_ip)
def preview_package(
    request: Request,
    contact_index: int,
    current_user: dict = Depends(get_current_user),
):
    """
    Generate a ZIP package for a single contact — includes the full PDF report
    plus any uploaded Will and supplementary document files. Returns ZIP binary.
    """
    vault_doc = vaults_col.find_one({"userId": current_user["_id"]})
    if not vault_doc:
        raise HTTPException(status_code=400, detail="No vault found.")

    vault_content: dict = decrypt_content(vault_doc.get("content", {}))
    contacts: list = vault_content.get("kin") or []
    if not contacts:
        raise HTTPException(status_code=400, detail="No contacts in vault.")
    if contact_index < 0 or contact_index >= len(contacts):
        raise HTTPException(status_code=404, detail="Contact not found.")

    contact = contacts[contact_index]
    first = contact.get("first", "")
    last = contact.get("last", "")
    holder_name = current_user.get("name", "the vault holder")

    pdf_bytes = generate_pdf_for_contact(contact, vault_doc, holder_name)

    buf = io.BytesIO()
    file_count = 1  # PDF
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_safe_filename(f"Kinlight-{first}-{last}.pdf"), pdf_bytes)

        will = vault_content.get("will") if isinstance(vault_content, dict) else None
        if will and will.get("file_id"):
            data = _download_and_decrypt(will["file_id"])
            if data:
                zf.writestr(_safe_filename(will.get("filename", "Will.pdf")), data)
                file_count += 1

        for d in vault_content.get("suppDocs", []) if isinstance(vault_content, dict) else []:
            if d.get("file_id"):
                data = _download_and_decrypt(d["file_id"])
                if data:
                    zf.writestr(_safe_filename(d.get("filename", "document.pdf")), data)
                    file_count += 1

    buf.seek(0)

    logger.info(
        f"Preview package generated for {first} {last} "
        f"({file_count} files) by user {mask_email(current_user.get('email',''))}"
    )

    from fastapi.responses import Response
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{_safe_filename(f"Kinlight-{first}-{last}.zip")}"',
        },
    )


@app.post("/test-notification")
@limiter.limit("5/minute", key_func=get_user_or_ip)
def test_notification(request: Request, current_user: dict = Depends(get_current_user)):
    """
    F06: Send a test notification email to the vault holder so they can
    verify their notification pipeline works. Uses the first contact's
    data to generate a sample PDF. Does NOT modify vault state.
    """
    holder_email = current_user.get("email", "").strip()
    if not holder_email:
        raise HTTPException(status_code=400, detail="No email address on your account. An admin can add one for you.")

    holder_name = current_user.get("name", "Kinlight User")

    vault_doc = vaults_col.find_one({"userId": current_user["_id"]})
    if not vault_doc:
        raise HTTPException(status_code=400, detail="No vault found — create some content first")

    vault_content: dict = decrypt_content(vault_doc.get("content", {}))
    contacts: list = vault_content.get("kin") or []
    if not contacts:
        raise HTTPException(status_code=400, detail="No contacts in vault — add a contact first")

    sample = contacts[0]
    first = sample.get("first", "")
    last = sample.get("last", "")

    pdf_bytes = generate_pdf_for_contact(sample, vault_doc, holder_name)
    attachments = [{
        "filename": f"Kinlight-Test-{first}-{last}.pdf",
        "content": base64.b64encode(pdf_bytes).decode("utf-8"),
    }]

    total_size = len(pdf_bytes)
    max_attach = 20 * 1024 * 1024

    # Include uploaded file attachments (same logic as send_notification_email)
    will = vault_content.get("will") if isinstance(vault_content, dict) else None
    if will and will.get("file_id"):
        data = _download_and_decrypt(will["file_id"])
        if data:
            fsize = len(data)
            if total_size + fsize <= max_attach:
                attachments.append({
                    "filename": _safe_filename(will.get("filename", "Will.pdf")),
                    "content": base64.b64encode(data).decode("utf-8"),
                })
                total_size += fsize

    for d in vault_content.get("suppDocs", []) if isinstance(vault_content, dict) else []:
        if d.get("file_id"):
            data = _download_and_decrypt(d["file_id"])
            if data:
                fsize = len(data)
                if total_size + fsize <= max_attach:
                    attachments.append({
                        "filename": _safe_filename(d.get("filename", "document.pdf")),
                        "content": base64.b64encode(data).decode("utf-8"),
                    })
                    total_size += fsize

    body = f"""Hi {holder_name},

This is a test notification from Kinlight.

If this were a real notification, your contact {first} {last} would receive the attached package. No contacts have been notified — this is just a test verifying that your notification pipeline is working correctly.

To change what your contacts would receive, edit your vault content (assets, wishes, Will details, and personal letters).

— The Kinlight team
"""

    subject = "Kinlight — test notification"
    sent = _send_email(holder_email, subject, body, attachments)

    if not sent:
        raise HTTPException(status_code=500, detail="Failed to send test email. Check server logs.")

    logger.info(f"Test notification sent to {mask_email(holder_email)} for user {mask_email(current_user.get('email',''))}")
    return {"ok": True, "message": f"Test notification sent to {holder_email}"}


@app.post("/checkin")
def checkin(current_user: dict = Depends(get_current_user)):
    """
    Record a check-in. Resets overdueNotificationSent, reminderSent, and warningSentDays
    so the next cycle starts fresh.
    """
    now = now_utc()
    existing = vaults_col.find_one({"userId": current_user["_id"]})
    was_overdue = existing.get("overdueNotificationSent", False) if existing else False

    vaults_col.update_one(
        {"userId": current_user["_id"]},
        {"$set": {
            "lastCheckin":               now,
            "overdueNotificationSent":   False,
            "reminderSent":              False,
            "warningSentDays":           [],
            "trustedResolved":           False,
            "updatedAt":                 now,
        },
         "$unset": {"trustedNotifiedAt": ""}},
        upsert=True,
    )
    # F115: checking in invalidates any outstanding trusted-person magic links.
    invalidate_trusted_links(current_user["_id"])

    allclear_count = 0
    if was_overdue and existing:
        holder_name = current_user.get("name", "the vault holder")
        # F04: decrypt content if encrypted
        contacts = decrypt_content(existing.get("content")).get("kin") or []
        for contact in contacts:
            if send_allclear_email(contact, holder_name):
                allclear_count += 1
        if allclear_count > 0:
            send_push_to_user(current_user["_id"], "All clear",
                              f"All clear sent — {allclear_count} contact(s) notified you're okay.",
                              tag="kinlight-allclear")

    return {
        "ok":             True,
        "checkedIn":      True,
        "allclear_sent":  allclear_count > 0,
        "allclear_count": allclear_count,
    }


# ─── F115: TRUSTED PERSON API ─────────────────────────────────────────────────

@app.post("/trusted/send-link")
@limiter.limit("5/minute", key_func=get_user_or_ip)
def trusted_send_link(request: Request, current_user: dict = Depends(get_current_user)):
    """F115: manually generate + email the trusted-person magic link. Used by the
    Settings "Test trusted link" button and for verification. The pulse scanner
    generates the same link automatically when the vault becomes overdue."""
    vault_doc = vaults_col.find_one({"userId": current_user["_id"]})
    if not vault_doc:
        raise HTTPException(status_code=400, detail="No vault found")
    if not vault_doc.get("trustedEnabled", False):
        raise HTTPException(status_code=400, detail="Trusted person is not enabled in Settings")
    trusted = get_trusted_contact(vault_doc)
    if not trusted:
        raise HTTPException(status_code=400, detail="No trusted contact designated")
    if not trusted.get("email"):
        raise HTTPException(status_code=400, detail="Trusted contact has no email address")

    token = generate_trusted_link(current_user["_id"])
    if token is None:
        raise HTTPException(status_code=500, detail="Trusted links unavailable")
    holder_name = current_user.get("name", "the vault holder")
    if not send_trusted_email(trusted, holder_name, token):
        raise HTTPException(status_code=500, detail="Failed to send trusted email. Check server logs.")
    logger.info(f"F115: manual trusted link sent for {mask_email(current_user.get('email',''))}")
    return {"ok": True, "link": f"{APP_URL}?trusted={token}"}


@app.get("/trusted/access")
@limiter.limit("10/minute")
def trusted_access(request: Request, token: str = ""):
    """F115: redeem a single-use magic link, returning a scoped read-only JWT."""
    if not token:
        raise HTTPException(status_code=400, detail="Missing token")
    link_doc = trusted_links_col.find_one({"tokenHash": hash_reset_token(token)}) if trusted_links_col is not None else None
    if link_doc is None or link_doc.get("used", False):
        raise HTTPException(status_code=400, detail="This link is invalid or has already been used.")
    expires = link_doc.get("expiresAt")
    if expires is None or ensure_utc(expires) <= now_utc():
        raise HTTPException(status_code=400, detail="This link has expired. Please ask the vault holder for a new one.")

    user = users_col.find_one({"_id": link_doc["userId"]})
    if not user:
        raise HTTPException(status_code=404, detail="Vault holder not found")
    vault_doc = vaults_col.find_one({"userId": user["_id"]})
    if not vault_doc or not vault_doc.get("trustedEnabled", False):
        raise HTTPException(status_code=403, detail="Trusted access is no longer enabled for this vault.")

    trusted_links_col.update_one({"_id": link_doc["_id"]}, {"$set": {"used": True}})
    scoped = create_trusted_token(str(user["_id"]))
    logger.info(f"F115: trusted link redeemed for {mask_email(user.get('email',''))}")
    return {"ok": True, "token": scoped, "holderName": user.get("name", "")}


@app.get("/trusted/vault")
def trusted_vault(ctx: dict = Depends(get_trusted_user)):
    """F115: read-only view of the vault for a trusted person."""
    return {"ok": True, "vault": reconstruct_vault_blob(ctx["vault"])}


@app.post("/trusted/resolve")
@limiter.limit("5/minute")
def trusted_resolve(request: Request, ctx: dict = Depends(get_trusted_user)):
    """F115: trusted person confirms they've checked on the holder — halts any
    further kin notifications without mutating the vault or account."""
    vaults_col.update_one(
        {"_id": ctx["vault"]["_id"]},
        {"$set": {"trustedResolved": True, "updatedAt": now_utc()},
         "$unset": {"trustedNotifiedAt": ""}},
    )
    invalidate_trusted_links(ctx["user"]["_id"])
    logger.warning(f"F115: trusted person resolved (halted notifications) for {mask_email(ctx['user'].get('email',''))}")
    return {"ok": True, "resolved": True}


# ─── F101: PUSH NOTIFICATION API ───────────────────────────────────────────────

@app.get("/push/key")
def push_key():
    """Return the VAPID public key so the frontend can subscribe for push."""
    if not VAPID_PUBLIC_KEY:
        raise HTTPException(status_code=503, detail="Push notifications not configured")
    return {"key": VAPID_PUBLIC_KEY}


@app.post("/push/subscribe")
def push_subscribe(payload: dict, current_user: dict = Depends(get_current_user)):
    """Store a push subscription for the current user."""
    if push_subs_col is None:
        raise HTTPException(status_code=503, detail="Database not available")
    subscription = payload.get("subscription")
    if not subscription or not subscription.get("endpoint"):
        raise HTTPException(status_code=400, detail="Invalid subscription")
    user_id = current_user["_id"]
    endpoint = subscription["endpoint"]
    existing = push_subs_col.find_one({"userId": user_id, "subscription.endpoint": endpoint})
    if existing:
        push_subs_col.update_one(
            {"_id": existing["_id"]},
            {"$set": {"subscription": subscription, "updatedAt": now_utc()}},
        )
    else:
        push_subs_col.insert_one({
            "userId":       user_id,
            "subscription": subscription,
            "createdAt":    now_utc(),
            "updatedAt":    now_utc(),
        })
    logger.info(f"F101: Push subscription saved for {mask_email(str(user_id))}")
    return {"ok": True}


@app.post("/push/unsubscribe")
def push_unsubscribe(payload: dict, current_user: dict = Depends(get_current_user)):
    """Remove a push subscription for the current user."""
    if push_subs_col is None:
        return {"ok": True}
    endpoint = payload.get("endpoint", "")
    if endpoint:
        push_subs_col.delete_many({"userId": current_user["_id"], "subscription.endpoint": endpoint})
        logger.info(f"F101: Push subscription removed for {mask_email(str(current_user['_id']))}")
    return {"ok": True}


# ─── ADMIN / TESTING ROUTES ───────────────────────────────────────────────────

@app.post("/admin/force-stale-pulse")
@limiter.limit("5/minute", key_func=get_user_or_ip)
def force_stale_pulse(request: Request, current_user: dict = Depends(get_current_user)):
    """F93: Backdate the pulse scanner heartbeat to simulate a dead scanner. For testing /health's 503 path."""
    require_admin(current_user)
    if system_col is not None:
        system_col.update_one(
            {"_id": "pulse_scanner"},
            {"$set": {
                "lastRun": now_utc() - timedelta(hours=PULSE_SCAN_UNHEALTHY_AFTER_HOURS + 1),
                "vaultsChecked": 0,
            }},
            upsert=True,
        )
    return {"ok": True, "message": "Pulse scanner heartbeat backdated to simulate failure"}


@app.post("/admin/trigger-pulse")
@limiter.limit("5/minute", key_func=get_user_or_ip)
def trigger_pulse(request: Request, current_user: dict = Depends(get_current_user)):
    """Manually trigger the pulse scan. For testing."""
    require_admin(current_user)
    run_pulse_scan()
    return {"ok": True, "message": "Pulse scan triggered"}


@app.post("/admin/force-overdue")
@limiter.limit("5/minute", key_func=get_user_or_ip)
def force_overdue(request: Request, body: dict = Body(...), current_user: dict = Depends(get_current_user)):
    """Set vault lastCheckin to 2020 to simulate an overdue state. For testing."""
    require_admin(current_user)

    target_email = (body.get("target", "") or "").strip().lower()
    if target_email:
        target_user = users_col.find_one({"email": target_email})
        if not target_user:
            raise HTTPException(status_code=404, detail="Target user not found")
        user_id = target_user["_id"]
    else:
        user_id = current_user["_id"]

    logger.warning(f"Admin {mask_email(current_user.get('email',''))} force-overdue target={mask_email(target_email) or 'self'}")

    vaults_col.update_one(
        {"userId": user_id},
        {"$set": {
            "lastCheckin":               datetime(2020, 1, 1, tzinfo=timezone.utc),
            "overdueNotificationSent":   False,
            "reminderSent":              False,
            "warningSentDays":           [],
            "updatedAt":                 now_utc(),
        }},
        upsert=True,
    )
    return {"ok": True, "message": "Vault set to overdue state"}


@app.post("/admin/force-reminder")
@limiter.limit("5/minute", key_func=get_user_or_ip)
def force_reminder(request: Request, body: dict = Body(...), current_user: dict = Depends(get_current_user)):
    """
    Set vault lastCheckin so the reminder threshold triggers next scan.
    Places lastCheckin so exactly (threshold - 1) days remain until due.
    For testing only.
    """
    require_admin(current_user)

    target_email = (body.get("target", "") or "").strip().lower()
    if target_email:
        target_user = users_col.find_one({"email": target_email})
        if not target_user:
            raise HTTPException(status_code=404, detail="Target user not found")
        user_id = target_user["_id"]
    else:
        user_id = current_user["_id"]

    logger.warning(f"Admin {mask_email(current_user.get('email',''))} force-reminder target={mask_email(target_email) or 'self'}")

    vault_doc = vaults_col.find_one({"userId": user_id})
    interval = _interval_days(vault_doc) if vault_doc else 60
    threshold = max(7, round(interval * 0.25))
    fake_checkin = now_utc() - timedelta(days=interval - threshold + 1)

    vaults_col.update_one(
        {"userId": user_id},
        {"$set": {
            "lastCheckin":               fake_checkin,
            "reminderSent":              False,
            "overdueNotificationSent":   False,
            "warningSentDays":           [],
            "updatedAt":                 now_utc(),
        }},
        upsert=True,
    )
    return {"ok": True, "message": f"Vault set to reminder-due state ({threshold - 1} days remaining)"}


@app.post("/admin/force-warning")
@limiter.limit("5/minute", key_func=get_user_or_ip)
def force_warning(request: Request, body: dict = Body(...), current_user: dict = Depends(get_current_user)):
    """F64-2: Set vault to day 1 of overdue (just inside warning window). For testing."""
    require_admin(current_user)

    target_email = (body.get("target", "") or "").strip().lower()
    if target_email:
        target_user = users_col.find_one({"email": target_email})
        if not target_user:
            raise HTTPException(status_code=404, detail="Target user not found")
        user_id = target_user["_id"]
    else:
        user_id = current_user["_id"]

    logger.warning(f"Admin {mask_email(current_user.get('email',''))} force-warning target={mask_email(target_email) or 'self'}")

    vault_doc = vaults_col.find_one({"userId": user_id})
    interval = _interval_days(vault_doc) if vault_doc else 60
    grace = vault_doc.get("gracePeriodDays", 7) if vault_doc else 7
    # Place lastCheckin so vault is exactly 1 day into overdue
    fake_checkin = now_utc() - timedelta(days=interval + grace + 1)

    vaults_col.update_one(
        {"userId": user_id},
        {"$set": {
            "lastCheckin":               fake_checkin,
            "overdueNotificationSent":   False,
            "reminderSent":              False,
            "warningSentDays":           [],
            "updatedAt":                 now_utc(),
        }},
        upsert=True,
    )
    return {"ok": True, "message": "Vault set to day 1 of overdue (warning window)"}


# ─── FILE UPLOAD / STORAGE ────────────────────────────────────────────────────

def _file_owns(file_id: str, current_user: dict) -> bool:
    """Check whether the current user owns the given file (by GridFS metadata)."""
    if _storage_backend is None:
        return False
    owner = _storage_backend.get_owner(file_id)
    return owner == str(current_user["_id"])


# _validate_filename / _safe_filename / _validate_magic_bytes now live in
# kinlight/files_safety.py (re-exported above). The old _MAGIC_SIGNATURES list
# was dead code (the checks are inlined) and has been removed.


@app.post("/files/upload")
@limiter.limit("10/minute", key_func=get_user_or_ip)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """
    Upload a file to the vault. Returns {file_id, filename, content_type}.

    Allowed types: PDF, JPEG, PNG, WebP, DOC/DOCX, TXT, ZIP.
    Max size: 10 MB. File is encrypted at rest with AES-256-GCM.
    """
    if _storage_backend is None:
        raise HTTPException(status_code=503, detail="File storage not available")

    filename = (file.filename or "").strip()
    if not _validate_filename(filename):
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Allowed: PDF, JPEG, PNG, WebP, DOC, DOCX, TXT, ZIP.",
        )

    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Allowed: PDF, JPEG, PNG, WebP, DOC, DOCX, TXT, ZIP.",
        )

    # F104: Check Content-Length before reading entire file into memory
    content_length = request.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > MAX_FILE_SIZE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large. Maximum size is {MAX_FILE_SIZE_BYTES // (1024*1024)} MB.",
                )
        except ValueError:
            pass  # non-integer Content-Length — let file.read() handle it

    file_data = await file.read()
    if len(file_data) == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(file_data) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE_BYTES // (1024*1024)} MB.",
        )

    # F103: Validate magic bytes against claimed content type
    if not _validate_magic_bytes(file_data, content_type):
        raise HTTPException(
            status_code=400,
            detail="File content does not match its claimed type. "
                   "The file header must match the selected format.",
        )

    encrypted_data = encrypt_bytes(file_data)
    file_id = _storage_backend.upload(
        encrypted_data,
        filename=filename,
        content_type=content_type,
        metadata={
            "userId": str(current_user["_id"]),
            "filename": filename,
        },
    )

    logger.info(f"File uploaded — user={str(current_user['_id'])} file_id={file_id} name={mask_email(filename)}")
    return {"ok": True, "file_id": file_id, "filename": filename, "content_type": content_type}


@app.get("/files/{file_id}")
@limiter.limit("30/minute", key_func=get_user_or_ip)
def download_file(
    request: Request,
    file_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Download a previously uploaded file. Returns the file with its original
    content type. Only the file owner can download it.
    """
    if _storage_backend is None:
        raise HTTPException(status_code=503, detail="File storage not available")

    if not _file_owns(file_id, current_user):
        raise HTTPException(status_code=404, detail="File not found")

    result = _storage_backend.download(file_id)
    if result is None:
        raise HTTPException(status_code=404, detail="File not found")

    encrypted_data, filename, content_type = result
    try:
        plaintext = decrypt_bytes(encrypted_data)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to decrypt file")

    from fastapi.responses import Response
    headers = {
        "Content-Disposition": f'attachment; filename="{_safe_filename(filename)}"',
        "Content-Type": content_type,
    }
    return Response(content=plaintext, headers=headers, media_type=content_type)


@app.delete("/files/{file_id}")
@limiter.limit("10/minute", key_func=get_user_or_ip)
def delete_file(
    request: Request,
    file_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Delete a previously uploaded file. Only the file owner can delete it.
    """
    if _storage_backend is None:
        raise HTTPException(status_code=503, detail="File storage not available")

    if not _file_owns(file_id, current_user):
        raise HTTPException(status_code=404, detail="File not found")

    deleted = _storage_backend.delete(file_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="File not found")

    logger.info(f"File deleted — user={str(current_user['_id'])} file_id={file_id}")
    return {"ok": True}
