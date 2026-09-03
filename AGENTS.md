# Kinlight — Project Rules

> **Always loaded.** Read this before making any changes.
> **Feature backlog:** `./docs/features.md` — status of all features (Must/Should/Could/Won't).
> **Architecture, API, data model, conventions:** `./docs/reference.md` — full reference doc.

---

## ✅ CHECKPOINT COMPLETE — F134 (Bind encrypted blobs to user via GCM AAD)

> **Completed:** 03 Sep 2026
> **Status:** Code complete (351 pytest tests pass).

### What was built
- **F134 (AAD binding):** `encrypt_content(content, user_id)` and `encrypt_bytes(data, user_id)` bind each blob with `AAD = str(userId)`, so a vault `content` blob or GridFS file body swapped between users fails to decrypt. `decrypt_content`/`decrypt_bytes` try `AAD=userId` and fall back to legacy `None`-AAD on `InvalidTag` (self-healing; no flag needed — GCM authenticates AAD).
- **Threading:** `_decrypt_vault_content(vault_doc)` helper used at every content-decrypt site; `vault_sync`, `upload_file`, `download_file`, and `_download_and_decrypt` (owner via `get_owner`) pass `str(userId)`.
- **One-off migration:** `scripts/migrate_bind_aad.py` (`--dry-run`, `--skip-files`) re-encrypts legacy content + GridFS file bytes with AAD.
- **Key rotation:** `rotate-key.py` is AAD-aware (decrypt with old key + AAD fallback, re-encrypt with new key + AAD).

### Key reminders
- Decrypt's legacy fallback is a deliberate `except InvalidTag:` — a genuinely tampered blob still raises (both AAD and None attempts fail). Do not broaden to `except Exception`.
- `_decrypt_vault_content` uses `str(vault_doc["userId"])`; the AAD string must match exactly what `vault_sync`/`upload_file` used at encrypt time (`str(current_user["_id"])`).
- Filenames (F132) and user PII (F133) use `_encrypt_string`/`_decrypt_string` — intentionally **not** AAD-bound here (out of F134's stated scope of `encrypt_content`/`encrypt_bytes`).

---

## ✅ CHECKPOINT COMPLETE — F133 (Encrypt user PII at rest)

> **Completed:** 03 Sep 2026
> **Status:** Code complete (345 pytest tests pass).

### What was built
- **F133 (user PII encryption):** `users.name`, `users.ageGroup`, and `users.notes` are encrypted at rest (AES-256-GCM via the existing key, reusing `_encrypt_string`/`_decrypt_string`). A `piiEncrypted` flag on the user doc is the source of truth for decrypt-at-read vs legacy passthrough. `_decrypt_user()` decrypts in place and is applied at every fetch/read point: `clean_user` (defensive), `get_current_user`, `login`, `webauthn_login_verify`, `run_pulse_scan`, `request_reset`, `trusted_access`. `PATCH /auth/me` encrypts `name` on write.
- **One-off migration:** `scripts/migrate_encrypt_user_pii.py` (`--dry-run` supported) encrypts existing plaintext `name`/`ageGroup`/`notes`.
- **Key rotation:** `rotate-key.py` re-encrypts user PII fields during rotation.

### Key reminders
- `username` and `email` are **not** touched here — email is the login identifier + delivery target, handled by F139 (blind index). `username` is a legacy duplicate of email.
- `_decrypt_user()` clears the in-memory `piiEncrypted` flag after decrypting (idempotent); it never writes back to Mongo. Writes are explicit `$set` field updates.
- Legacy plaintext users pass through unchanged until the migration script runs.

---

## ✅ CHECKPOINT COMPLETE — F132 (Encrypt GridFS filenames at rest)

> **Completed:** 03 Sep 2026
> **Status:** Code complete (336 pytest tests pass).

### What was built
- **F132 (filename encryption):** `fs.files.filename` + `metadata.filename` no longer store plaintext document names. Upload encrypts the filename with AES-256-GCM (`_encrypt_string`/`_decrypt_string` in `main.py`) and stores ciphertext in `fs.files.filename` with `metadata.filenameEncrypted=True`. `GET /files/{id}` decrypts before `_safe_filename()`. Legacy files pass through unchanged until migrated.
- **Storage interface:** `StorageBackend.download()` now returns a 4-tuple `(data, filename, content_type, filename_is_encrypted)`.
- **One-off migration:** `scripts/migrate_encrypt_filenames.py` (`--dry-run` supported) re-encrypts existing files' plaintext names.
- **Key rotation:** `rotate-key.py` now re-encrypts filenames (and encrypts legacy plaintext names) during rotation.

### Key reminders
- The notification / preview-package / test-notification / PDF paths read filenames from the F04-encrypted vault `content`, **not** from GridFS — no change needed there.
- `metadata.userId` stays plaintext (needed for ownership checks; not a document-name leak).
- `filenameEncrypted` flag on `fs.files.metadata` is the source of truth for decrypt-at-download vs legacy passthrough.

---

## ✅ CHECKPOINT COMPLETE — F102 (File Upload via GridFS)

> **Completed:** 30 July 2026
> **Status:** Code complete (261 tests pass). GridFS stores encrypted files in existing Atlas cluster.

### What was built
- **F102 (File Upload):** Pluggable storage backend (`identity-service/storage/`) with GridFS default — swap to S3 via `STORAGE_BACKEND` env var. AES-256-GCM encryption at rest. Endpoints: `POST /files/upload`, `GET /files/{id}`, `DELETE /files/{id}` with per-user ownership enforcement. Frontend file pickers in Will and suppDoc modals. jsPDF and ReportLab PDFs show attached filenames.
- **Notification attachments:** Overdue notification emails now include uploaded Will and Statement of Wishes files as attachments alongside the generated PDF (20 MB cumulative limit). `send_notification_email` iterates vault content for `will.file_id` and `suppDocs[].file_id`, downloads from storage, decrypts, and attaches. Graceful skip if storage unavailable, file missing, or size limit exceeded.
- **Preview package:** `GET /preview-package/{contact_index}` generates a ZIP containing the full PDF report plus all uploaded files. Replaces client-side jsPDF for "Preview all packages" — contacts get the actual file attachments in a downloaded ZIP.

### Key reminders
- `python-multipart` added to `requirements.txt` (needed for FastAPI `UploadFile`)
- File endpoints rate-limited: upload 10/min/user, download 30/min/user, delete 10/min/user
- Ownership enforced via GridFS metadata `userId` — not vault content
- Orphaned files possible if vault save never happens after upload (acceptable for MVP)
- When editing `index.html`, also sync frontend files:
  ```bash
  cp index.html frontend/index.html
  cp manifest.json sw.js favicon.svg icon-192.png icon-512.png frontend/
  ```
- Storage backend defaults to `gridfs`; no env var needed unless swapping to S3

---

## ✅ CHECKPOINT COMPLETE — F117 + F118 (Security: new-device alerts + passkeys)

> **Completed:** 23 Aug 2026
> **Status:** Code complete (309 pytest tests pass; 13 Playwright settings/login tests).

### What was built
- **F117 (New-device sign-in alert):** Frontend generates a persistent `localStorage['ee_device_id']` (`crypto.randomUUID()`) + device name, sent with every login. Backend `known_devices` collection + `register_device_login()` fires an email (Resend) + push (to already-enrolled devices) the first time a device is seen. Alerts run on a daemon thread. `ee_device_id` is **not** cleared on logout.
- **F118 (Passkey sign-in, opt-in):** Standard WebAuthn passkeys via `webauthn==2.7.1`. `RP_ID="kinlight.app"`, `EXPECTED_ORIGIN="https://kinlight.app"`. "Sign in with passkey" button on the login wall + a "Passkeys" card in Settings (add/list/remove). Password login stays the default; passkey is additive. `clean_user` exposes a denormalized `hasPasskey` flag. **Google SSO explicitly deferred.**
- **F119 (In-app change password):** Settings → Account → "Change password". `POST /auth/change-password` verifies the current password, bumps `tokenVersion` (revokes other sessions), and returns a fresh JWT so the current session stays signed in.

### Key reminders
- New collections: `known_devices` (unique `{userId, deviceId}`) and `webauthn_credentials` (unique `credentialId`). Both indexed at startup.
- `hasPasskey` is denormalized on the user doc — keep it in sync on passkey register/delete (done in `main.py`).
- WebAuthn challenges live in an in-memory dict (5-min TTL), single-worker GCE — fine for now.
- `webauthn` is now in `requirements.txt` (pin `==2.7.1`; the v3 API differs).

---

## Before pushing

```bash
./test.sh   # Runs pytest — must be 351 passed
cp index.html frontend/index.html   # Keep both copies in sync
# Also sync PWA files (F100):
cp manifest.json sw.js favicon.svg icon-192.png icon-512.png frontend/
```

---

## Architecture

| Layer | Where | Notes |
|-------|-------|-------|
| Frontend | `./index.html` + `./frontend/index.html` | Single-file HTML/CSS/JS. GitHub Pages (`ramenfanclub.github.io/emergency-exit` → `kinlight.app`) |
| Backend | `identity-service/main.py` | Python FastAPI. GCE e2-micro (`api.kinlight.app`) |
| Database | MongoDB Atlas | Users + vaults + push_subscriptions + trusted_links + known_devices + webauthn_credentials collections |
| Email | Resend (`resend.com`) | From `hello@kinlight.app` |
| CI/CD | `.github/workflows/ci.yml` | 4 jobs: pytest, sync check, Playwright, pip-audit |

---

## Critical constants (frontend)

- **API base:** `https://api.kinlight.app`
- **localStorage key:** `ee_v3` (vault data, offline cache)
- **Session token:** `sessionStorage` (JWT, clears on tab close)
- **Separate localStorage flags:** `ee_onboarded` (outside `ee_v3`)
- **CSS vars:** `--p` (charcoal), `--ac` (sage), `--s` (linen cream), `--w` (warm white), `--am` (amber), `--er` (red), `--g` (gradient). Full table in `docs/reference.md`.
- **Fonts:** Manrope (headlines, 800) + Public Sans (body)
- **Mobile-first:** max-width 430px, min tap target 48px, border-radius 16px on cards

---

## Critical "What NOT to Do"

These will silently break the app:

- **Never declare `const API` in the main script block** — already declared in `<head>`. Dupe crashes all JS.
- **Never rewrite `<head>` without verifying `</script>` appears before `<style>`** — missing it makes CSS get parsed as JS.
- **Never delete the `save()` function** — core persistence. Body must be: `try{localStorage.setItem('ee_v3',JSON.stringify(S));}catch(e){} syncVault(); render();`
- **Never add `bson` to `requirements.txt`** — pymongo bundles its own; conflict causes ImportError.
- **Never use `doc` as a loop variable in `generate_pdf_for_contact()`** — shadows SimpleDocTemplate object. Use `supp_doc`.
- **Never use `or` for `content.kin` fallback** — empty list `[]` is falsy. Use explicit `None` check.
- **Never use Resend SDK for notification emails with attachments** — use `requests.post` directly.
- **Never use `if db` or `if client` for PyMongo objects** — always `if db is not None`.
- **JWT library is `PyJWT`** (`import jwt`), not `python-jose`.

## Key conventions

- No pure black — use `#2e2b26`. No old navy palette. No old teal accent.
- CSS variables for every colour. Background shifts for sectioning — no 1px borders.
- Monetary values: `Math.round().toLocaleString()`. State merge: `S={...S,...parsed}`.
- JS helper aliases: `$(id)` for `document.getElementById`, `pl()`, `trunc()`, `initials()`, `authHeader()`, `esc()`.
- Vault sync is silent — never show errors to the user.
- All contacts receive the full package — no access level tiers.
- For full conventions and the complete "What NOT to Do" list, see `./docs/reference.md`.
