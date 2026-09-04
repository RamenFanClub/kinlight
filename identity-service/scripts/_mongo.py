#!/usr/bin/env python3
"""Shared MongoDB + encryption helpers for one-off scripts (F141 + migrations).

Consolidates the duplicated `_connect` / `_cipher` / `_encrypt_string` /
`_encrypt_content` helpers that previously lived in every script. The crypto
mirrors `main.py` (AES-256-GCM, base64(nonce + ciphertext + tag)).

Sibling import — run scripts as `python3 scripts/<script>.py` (from
identity-service/) so this module is on `sys.path`; inside the container the
scripts are invoked as `python /app/scripts/<script>.py`.
"""

import base64
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pymongo import MongoClient

DB_NAME = "emergency_exit"


def connect(mongo_uri=None, db_name=DB_NAME):
    """Return the default database handle. Reads MONGO_URI from env when unset."""
    mongo_uri = mongo_uri or os.environ.get("MONGO_URI")
    if not mongo_uri:
        raise SystemExit("ERROR: MONGO_URI not set (use --mongo-uri or MONGO_URI env var).")
    return MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)[db_name]


def get_cipher(key=None):
    """Return an AESGCM cipher from VAULT_ENCRYPTION_KEY (env or explicit key)."""
    key = key or os.environ.get("VAULT_ENCRYPTION_KEY", "")
    if not key:
        raise SystemExit("ERROR: VAULT_ENCRYPTION_KEY not set (use --key or VAULT_ENCRYPTION_KEY env var).")
    return AESGCM(bytes.fromhex(key))


def encrypt_string(cipher, plaintext):
    """Encrypt a short string → base64(nonce + ciphertext + tag). No AAD (F132/F133/F137/F138)."""
    nonce = os.urandom(12)
    ciphertext = cipher.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_string(cipher, encrypted):
    raw = base64.b64decode(encrypted)
    return cipher.decrypt(raw[:12], raw[12:], None).decode("utf-8")


def encrypt_content(cipher, content_dict, user_id=None):
    """Encrypt a vault content dict. Binds AAD=str(user_id) when supplied (F134)."""
    plaintext = json.dumps(content_dict, separators=(",", ":")).encode("utf-8")
    nonce = os.urandom(12)
    aad = user_id.encode("utf-8") if user_id else None
    return base64.b64encode(nonce + cipher.encrypt(nonce, plaintext, aad)).decode("ascii")


def decrypt_content(cipher, stored, user_id=None):
    """Decrypt vault content. Handles None / plaintext-dict / encrypted str.

    F134: tries AAD=user_id first, falling back to legacy None-AAD on InvalidTag.
    """
    if stored is None:
        return {}
    if isinstance(stored, dict):
        return stored
    raw = base64.b64decode(stored)
    nonce, ciphertext = raw[:12], raw[12:]
    if user_id:
        try:
            plaintext = cipher.decrypt(nonce, ciphertext, user_id.encode("utf-8"))
        except InvalidTag:
            plaintext = cipher.decrypt(nonce, ciphertext, None)
    else:
        plaintext = cipher.decrypt(nonce, ciphertext, None)
    return json.loads(plaintext.decode("utf-8"))


def encrypt_bytes(cipher, plaintext, user_id=None):
    """Encrypt raw bytes → nonce + ciphertext + tag (no base64; GridFS is binary)."""
    nonce = os.urandom(12)
    aad = user_id.encode("utf-8") if user_id else None
    return nonce + cipher.encrypt(nonce, plaintext, aad)


def decrypt_bytes(cipher, encrypted, user_id=None):
    nonce, ciphertext = encrypted[:12], encrypted[12:]
    if user_id:
        try:
            return cipher.decrypt(nonce, ciphertext, user_id.encode("utf-8"))
        except InvalidTag:
            return cipher.decrypt(nonce, ciphertext, None)
    return cipher.decrypt(nonce, ciphertext, None)
