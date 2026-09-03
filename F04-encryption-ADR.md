# F04 — Application-Level Encryption at Rest

## Architecture Decision Record (ADR)

**Status:** Accepted — implemented June 2026; key-management hardening (Secret Manager, F121–F128) August 2026
**Date:** June 2026 (updated August 2026)
**Feature:** F04 — Data encrypted at rest and in transit

---

## Context

Kinlight stores sensitive personal data — financial assets, Will details, funeral wishes, personal letters — in MongoDB Atlas. Two of the three encryption layers are already in place:

- **In transit:** HTTPS on Railway (done)
- **At rest (infrastructure):** MongoDB Atlas encrypts data files on disk automatically (done)

**The remaining gap:** if someone gains access to the MongoDB database itself (e.g. a leaked `MONGO_URI` connection string, a compromised Railway dashboard, or a rogue database admin), they can read all vault content in plaintext. The Atlas disk-level encryption doesn't help here — it only protects against someone stealing the physical hard drives, not someone who logs into the database.

### The Dead Man's Switch Constraint

Pure end-to-end encryption (where only the user's browser can decrypt) is incompatible with Kinlight's core product loop. When a user stops checking in, the **server** must:

1. Read the vault content (assets, wishes, Will details, contacts, letters)
2. Generate a 6-page PDF per contact using ReportLab
3. Email that PDF to nominated contacts via Resend

If the server can't decrypt the vault, it can't do its job. This rules out true E2EE for Kinlight.

### Chosen approach: Application-Level Encryption (Option 3 — Hybrid)

Encrypt vault content using a secret key held by the application server, **not** stored in the database. This means:

- **Database breach alone** (leaked `MONGO_URI`) → attacker sees encrypted blobs, not plaintext
- **Application server** (Railway) can still decrypt when the pulse scanner needs to generate PDFs

This is the same pattern used by most SaaS products that need to process user data server-side.

---

## What This Protects Against (Threat Model)

| Threat | Protected? | Why |
|--------|-----------|-----|
| Someone steals MongoDB connection string | Yes | Vault content is encrypted; key isn't in the DB |
| MongoDB Atlas employee reads data | Yes | They see encrypted blobs |
| Someone gets into Railway dashboard | Partially | They could find the encryption key in env vars — mitigated by key rotation and access controls |
| Kinlight developer with full server access | No | They have access to both the code and the key |
| Physical theft of Atlas hard drives | Yes (already covered by Atlas) | Double-layered now |

**Honest limitation:** This does not protect against a fully compromised server (where the attacker has both the database AND the application code/environment variables). True protection against that requires E2EE with contact-held keys, which is a much larger product change. MongoDB's Client-Side Field Level Encryption (CSFLE) can be explored later to strengthen this further.

---

## Technical Design

### How It Works

```
USER'S BROWSER                      RAILWAY SERVER                    MONGODB
                                    
Vault data (plaintext JSON)  --->   Receives vault blob
                                    Encrypts content fields
                                    with VAULT_ENCRYPTION_KEY  --->   Stores encrypted blob
                                    
                                    Pulse scanner triggers
                              <---  Reads encrypted blob
                                    Decrypts with same key
                                    Generates PDF
                                    Sends email
```

### Encryption Details

- **Algorithm:** AES-256-GCM (authenticated encryption — industry standard)
  - "AES-256" = the encryption method; "GCM" = adds tamper detection so you know if data was modified
- **Key:** A single `VAULT_ENCRYPTION_KEY` stored as a Railway environment variable (never in code, never in the database)
- **What gets encrypted:** The `content` field in each vault document (assets, wishes, will, suppDocs, kin, notifySeq, saveCount, v)
- **What stays plaintext:** Scheduling fields that the pulse scanner queries on (`lastCheckin`, `checkInFrequency`, `checkInUnit`, `gracePeriodDays`, `notifyProto`, `overdueNotificationSent`, `reminderSent`, `warningSentDays`) — these must remain queryable by MongoDB
- **Each encryption produces a unique random nonce** (a one-time random number), so encrypting the same data twice produces different ciphertext — this prevents pattern analysis

### What Changes in the Code

#### Backend (`identity-service/main.py`)

**New helper functions (2 small functions):**

```
encrypt_content(plaintext_dict) → encrypted_string
decrypt_content(encrypted_string) → plaintext_dict
```

These convert the vault `content` dictionary to/from an encrypted string using the `VAULT_ENCRYPTION_KEY`.

**Modified functions (4 touch points):**

| Function | Current Behaviour | New Behaviour |
|----------|------------------|---------------|
| `vault_sync()` | Stores `content` dict directly in MongoDB | Calls `encrypt_content()` before storing |
| `GET /vault` endpoint | Returns `content` dict from `reconstruct_vault_blob()` | Calls `decrypt_content()` first, then reconstructs |
| `run_pulse_scan()` | Reads `content` directly from vault doc | Calls `decrypt_content()` on the content field before passing to PDF/email functions |
| `reconstruct_vault_blob()` | Reads from `doc["content"]` directly | Receives already-decrypted content (no change to this function itself) |

**No changes to:**
- `generate_pdf_for_contact()` — receives plaintext dict (decryption happens before it's called)
- `send_notification_email()` — same, receives plaintext
- `get_contacts_to_notify()` — same
- Frontend (`index.html`) — no changes at all; encryption/decryption is entirely server-side
- `extract_vault_fields()` — scheduling fields stay plaintext

#### New Environment Variable

```
VAULT_ENCRYPTION_KEY=<64-character hex string>
```

Generated once, set in Railway dashboard, never committed to code.

### Migration Plan (6 Existing Testers)

Migration is handled automatically — no tester action needed:

1. Deploy the updated code (with encrypt/decrypt functions)
2. The `decrypt_content()` function detects whether content is already encrypted or still plaintext (encrypted content is a string; plaintext content is a dict)
3. On first vault sync after deployment, each tester's vault is read as plaintext, then written back encrypted
4. After one sync cycle, all vaults are encrypted

This "read plaintext or encrypted, always write encrypted" pattern means zero downtime and no manual migration scripts.

### Backward Compatibility

The `_get_content_or_legacy()` helper already handles old vs new vault schemas. The decryption layer sits above this — it runs first, and if content is already a plaintext dict (pre-migration), it passes through unchanged.

---

## What This Does NOT Cover (Future Work)

- **MongoDB CSFLE (rejected)** — MongoDB's Client-Side Field Level Encryption was considered and **declined**. Its headline benefit (the database/Atlas admin never sees plaintext) is already delivered by the application-level encryption above; the DB only ever stores ciphertext. Its key-management benefit (KMS, IAM, audit) is now achieved far more simply via **GCP Secret Manager** (see below), without the `mongocrypt` binary, a KMS to operate, or per-operation driver changes. CSFLE is therefore unnecessary complexity for Kinlight's threat model.
- **Per-user keys** — each user having their own encryption key (derived from their password) for cross-user breach isolation. Deferred: not needed at current single-digit-user scale. If adopted later, envelope encryption (per-user DEK wrapped by a master key in Secret Manager) is the recommended middle ground — simpler than CSFLE, stronger than a single key. Note: per-user keys would re-introduce the password-reset complexity (changing password = re-encrypting vault).

## Key Management (Secret Manager, F121–F128)

The `VAULT_ENCRYPTION_KEY` (and all other runtime secrets — `MONGO_URI`, `JWT_SECRET`, `RESEND_API_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`) are stored in **GCP Secret Manager**, not in GitHub Secrets, the CI deploy script, or `docker run` argv. The application fetches them at startup via the Secret Manager REST API, authenticating with the GCE VM's attached service account (instance metadata server token). Existing environment variables always take precedence — preserving a manual-override and rollback path.

**What this buys (key-management hardening, not a new crypto boundary):**

- No plaintext secrets in GitHub Secrets, CI logs, the deploy script, or `docker run` process argv (removing the `ps`/shell-history/`docker inspect` exposure).
- IAM-scoped access (`roles/secretmanager.secretAccessor` on the specific secrets only) plus access audit logs and versioning.
- Centralised rotation story.

**Honest limitation (unchanged from the core F04 threat model):** any process on the VM — including a compromised container — can reach the metadata server and fetch the same service-account token, and thus the same secrets. Secret Manager improves *where the key lives and who is audited*, but does not protect against a fully compromised server. Only contact-held E2EE would, which is incompatible with the dead-man's-switch.

**Rotation cadence (F128):** rotate the key (a) immediately on any suspected exposure, and (b) at least annually as routine hygiene. Use `identity-service/scripts/rotate-key.py` (idempotent; see `docs/gce-deployment-guide.md`). After rotation, update the Secret Manager secret value, restart the server, and run a fresh backup.



---

## Consequences

**Positive:**
- Database breaches no longer expose plaintext sensitive data
- No frontend changes — transparent to users and testers
- No changes to the PDF generation or email delivery pipeline
- Automatic migration — no manual steps for existing testers
- Foundation for stronger encryption later (CSFLE, per-user keys)

**Negative:**
- Losing the `VAULT_ENCRYPTION_KEY` environment variable means all vault data becomes unrecoverable — must document key backup procedure
- Adds a small performance overhead (encrypting/decrypting JSON on every sync) — negligible at current scale
- `content` field is no longer queryable in MongoDB (it's an encrypted blob) — but we never query inside `content` today, only the top-level scheduling fields
- Slightly more complex debugging — can't just look at MongoDB Atlas UI to inspect vault data anymore

---

## Backlog Entry (for docs/reference.md)

```
| F04 | Data encrypted at rest and in transit | Must | in-progress |
Transit: HTTPS (done). Atlas disk encryption (done).
**Application-level encryption (AES-256-GCM):** vault `content` field encrypted
server-side using `VAULT_ENCRYPTION_KEY` env var before MongoDB storage.
Scheduling fields remain plaintext for pulse scanner queries.
Auto-migration: detects plaintext vs encrypted on read; always writes encrypted.
Key management: secrets in GCP Secret Manager (F121–F128). Key rotation (F109).
Future: per-user keys (deferred). CSFLE rejected. |
```

---

## Implementation Checklist

- [ ] Generate `VAULT_ENCRYPTION_KEY` (64-char hex) and set in Railway env vars
- [ ] Add `cryptography` to `requirements.txt` (Python library for AES-256-GCM)
- [ ] Implement `encrypt_content()` and `decrypt_content()` helper functions
- [ ] Modify `vault_sync()` to encrypt before storing
- [ ] Modify `GET /vault` to decrypt before returning
- [ ] Modify `run_pulse_scan()` to decrypt before processing
- [ ] Add backward-compatible plaintext detection in `decrypt_content()`
- [ ] Write pytest tests for encrypt/decrypt round-trip
- [ ] Write pytest test for plaintext-passthrough (migration scenario)
- [ ] Write pytest test for tampered ciphertext detection
- [ ] Update docs/reference.md with F04 status and notes
- [x] Document key backup procedure (F109 — `identity-service/scripts/backup-key.sh`, `restore-key.sh`, `rotate-key.py`)
