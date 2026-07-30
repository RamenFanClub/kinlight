# Kinlight — Project Rules

> **Always loaded.** Read this before making any changes.
> **Feature backlog:** `./docs/features.md` — status of all features (Must/Should/Could/Won't).
> **Architecture, API, data model, conventions:** `./docs/reference.md` — full reference doc (WIP: migrating from CLAUDE.md).

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

## Before pushing

```bash
./test.sh   # Runs pytest — must be 261 passed
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
| Database | MongoDB Atlas | Users + vaults + push_subscriptions collections |
| Email | Resend (`resend.com`) | From `hello@kinlight.app` |
| CI/CD | `.github/workflows/ci.yml` | 4 jobs: pytest, sync check, Playwright, pip-audit |

---

## Critical constants (frontend)

- **API base:** `https://api.kinlight.app`
- **localStorage key:** `ee_v3` (vault data, offline cache)
- **Session token:** `sessionStorage` (JWT, clears on tab close)
- **Separate localStorage flags:** `ee_onboarded` (outside `ee_v3`)
- **CSS vars:** `--p` (charcoal), `--ac` (sage), `--s` (linen cream), `--w` (warm white), `--am` (amber), `--er` (red), `--g` (gradient). Full table in CLAUDE.md.
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
- For full conventions and the complete "What NOT to Do" list, see `./CLAUDE.md`.
