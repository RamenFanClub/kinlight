# Kinlight — Project Rules

> **Always loaded.** Read this before making any changes.
> **Full reference:** `./CLAUDE.md` — architecture, API endpoints, data model, design system, conventions.
> **Feature backlog:** `./docs/features.md` — status of all features (Must/Should/Could/Won't).

---

## 🔴 ACTIVE CHECKPOINT — F100/F101 (PWA + Push Notifications)

> **Last updated:** 27 July 2026
> **Status:** Code complete (214 tests pass). Pushed to GitHub. **VAPID keys NOT yet configured on GCE.**

### What was built
- **F100 (PWA):** `manifest.json`, `sw.js`, app icons, offline caching, offline check-in awareness
- **F101 (Push):** Backend push endpoints, `pywebpush` dependency, `send_push_to_user()` called alongside all email triggers, push toggle in Settings UI

### What still needs to be done (in order)

**Step 1 — SSH into the GCE VM and get the VAPID keys**
The first deploy ran without VAPID env vars, so the server generated a new pair at startup and logged them:
1. SSH into the GCE VM (browser SSH: GCP Console → Compute Engine → VM → SSH button)
2. Run: `docker logs kinlight-app 2>&1 | grep "F101"`
3. You'll see: `WARNING: F101: VAPID keys not found in env — generated new pair. Public: BGxl88k2a...`
4. Copy both the private key and public key (long URL-safe base64 strings)

**Step 2 — Add the keys as GitHub Secrets**
1. Go to `https://github.com/RamenFanClub/kinlight` → Settings → Secrets and variables → Actions
2. Add **New repository secret:** `VAPID_PRIVATE_KEY` ← the private key string
3. Add **New repository secret:** `VAPID_PUBLIC_KEY` ← the public key string

**Step 3 — Update CI to pass the keys to Docker**
In `.github/workflows/ci.yml`, add these two lines inside the `docker run` command (after the `VAULT_ENCRYPTION_KEY` line):
```
-e VAPID_PRIVATE_KEY='${{ secrets.VAPID_PRIVATE_KEY }}' \
-e VAPID_PUBLIC_KEY='${{ secrets.VAPID_PUBLIC_KEY }}' \
```

**Step 4 — Push to redeploy**
```bash
git add .github/workflows/ci.yml && git commit -m "Add VAPID env vars to GCE deploy" && git push
```
Server now starts with stable VAPID keys. The WARNING disappears.

**Step 5 — Test on phone**
1. Open `https://kinlight.app` → install to home screen (Chrome: Menu → Add to Home Screen / Safari: Share → Add to Home Screen)
2. Log in, go to Settings → toggle "Enable push notifications" → grant permission

### Key reminders
- VAPID keys must NOT change between deploys (old push subscriptions break)
- On iOS, push only works after installing to home screen
- Offline check-in flag: `ee_pending_checkin` in localStorage
- When editing `index.html`, also sync PWA files:
  ```bash
  cp index.html frontend/index.html
  cp manifest.json sw.js favicon.svg icon-192.png icon-512.png frontend/
  ```

---

## Before pushing

```bash
./test.sh   # Runs pytest — must be 214 passed
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
