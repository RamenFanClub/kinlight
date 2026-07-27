# Kinlight — Project Specification & Memory

> **IMPORTANT:** `AGENTS.md` is Opencode's primary instruction file (loaded every session).
> This file (`CLAUDE.md`) is the full reference — loaded via `opencode.json`.
> **Feature backlog** lives in `docs/features.md` — update that file when statuses change.
> Both `./index.html` AND `./frontend/index.html` must always be kept in sync.

> **SESSION START INSTRUCTION:** At the beginning of every new chat, always check
> the project-level files synced from GitHub (CLAUDE.md and index.html). These are
> the source of truth. Do not rely on memory summaries alone — read the actual
> documents provided in context before making any assumptions about current state.

---

## 🔴 ACTIVE CHECKPOINT — F100/F101 (PWA + Push Notifications)

> **Last updated:** 27 July 2026
> **Status:** Code complete (214 tests pass). Pushed to GitHub. **VAPID keys NOT yet configured on GCE.**

### What was built
- **F100 (PWA):** `manifest.json`, `sw.js`, app icons, offline caching, offline check-in awareness
- **F101 (Push):** Backend push endpoints (`GET /push/key`, `POST /push/subscribe`, `POST /push/unsubscribe`), `pywebpush` dependency, `send_push_to_user()` called alongside all email triggers, push toggle in Settings UI

### Files changed (all committed + pushed)
- **New:** `manifest.json`, `sw.js`, `favicon.svg`, `icon-192.png`, `icon-512.png` (both root + `frontend/`)
- **Modified:** `index.html`, `frontend/index.html`, `identity-service/main.py`, `identity-service/test_main.py`, `identity-service/requirements.txt`, `.github/workflows/ci.yml`, `.env.example`
- **Docs updated:** `CLAUDE.md`, `AGENTS.md`, `docs/features.md`, `docs/gce-deployment-guide.md`

### What still needs to be done (in order)

**Step 1 — SSH into the GCE VM and get the VAPID keys**
The first deploy ran without `VAPID_PRIVATE_KEY`/`VAPID_PUBLIC_KEY` env vars, so the server generated a new pair at startup and logged them. Retrieve them:
1. SSH into the GCE VM (browser SSH: GCP Console → Compute Engine → VM → SSH button)
2. Run: `docker logs kinlight-app 2>&1 | grep "F101"`
3. You'll see a line like: `WARNING: F101: VAPID keys not found in env — generated new pair. Add VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY to your env vars. Public: BGxl88k2a...`
4. Copy both the private key and public key (both are long URL-safe base64 strings)

**Step 2 — Add the keys as GitHub Secrets**
1. Go to `https://github.com/RamenFanClub/kinlight` → Settings → Secrets and variables → Actions
2. Add **New repository secret:** `VAPID_PRIVATE_KEY` ← the private key string
3. Add **New repository secret:** `VAPID_PUBLIC_KEY` ← the public key string

**Step 3 — Update CI to pass the keys to Docker**
In `.github/workflows/ci.yml`, add these two lines inside the `docker run` command (after line 193):
```
-e VAPID_PRIVATE_KEY='${{ secrets.VAPID_PRIVATE_KEY }}' \
-e VAPID_PUBLIC_KEY='${{ secrets.VAPID_PUBLIC_KEY }}' \
```

**Step 4 — Push to redeploy**
```bash
git add .github/workflows/ci.yml && git commit -m "Add VAPID env vars to GCE deploy" && git push
```
The server now starts with stable VAPID keys. The WARNING disappears.
Run: `./test.sh` — must pass 214 before pushing.

**Step 5 — Test on phone**
1. Open `https://kinlight.app` on your phone
2. Install to home screen (Chrome: Menu → Add to Home Screen / Safari: Share → Add to Home Screen)
3. Open the installed app, log in
4. Go to Settings → toggle "Enable push notifications"
5. Grant permission when prompted

### Key reminders
- VAPID keys must NOT change between deploys (old push subscriptions break)
- On iOS, push only works after installing to home screen (Safari limitation — not a bug)
- The `pass` flag in offline check-in is `ee_pending_checkin` in localStorage
- CI sync check now verifies PWA files too — keep them in sync after index.html edits:
  ```bash
  cp index.html frontend/index.html
  cp manifest.json sw.js favicon.svg icon-192.png icon-512.png frontend/
  ```

---

## Login (Frontend)

The `index.html` includes a login wall:

- **Login screen** appears before anything else
- **Token** stored in `sessionStorage` (clears when browser tab closes)
- **"Sign out"** button in the header
- **"Hi, [Name]"** greeting shown after login
- **API calls** point to `https://api.kinlight.app`
- **Enter key** submits the login form
- **Session persistence** — if token exists in sessionStorage, login wall is skipped
- **Tester accounts:** tester_01 through tester_06, all share password `Benny#07`
- **Admin account:** `anggi` (F77) — separate from the tester pool, `isAdmin: true`, `isTester: false`. This is Anggi's personal/live account, used to build out a real vault and to access `/admin/*` endpoints. Currently uses the same temporary password as testers (`Benny#07`) — needs a unique password before launch.

### Key element IDs for login:
- `#login-wall` — the full-screen login overlay
- `#li-user` — username input
- `#li-pass` — password input
- `#login-err` — error message display
- `#logout-btn` — sign out button in header
- `#user-greeting` — "Hi, [Name]" in header

### Key functions:
- `doLogin()` — POSTs to `/auth/login`, stores token, calls `showApp()` (now async)
- `doLogout()` — clears session, resets state, shows login wall
- `showApp(user)` — async; awaits `loadFromServer()` before calling `render()`
- `loadFromServer()` — async; fetches `GET /vault`, falls back to localStorage if server unreachable
- `API` constant — set to `https://api.kinlight.app`

---

## App Name & Branding

- **App name:** **Kinlight** (brand decision made June 2026 — supersedes the "Emergency Exit" working name)
  - **F72a (frontend) ✅ shipped June 2026** — name, Lantern logo, wordmark, status/hero/toast copy, "How Kinlight works" explainer, and client-side jsPDF palette + name updated in both `index.html` copies. Storage keys (`ee_v3` etc.) intentionally unchanged.
  - **F72b (backend) ✅ shipped June 2026** — ReportLab server-side PDF palette updated (charcoal `#2e2b26` + sage `#5a7a6e`), PDF title + filename updated to "Kinlight", all four email functions + password reset email updated (subjects, bodies, sign-off → "The Kinlight team"). `FROM_EMAIL` flipped to `"Kinlight <hello@kinlight.app>"` ✅. F68 absorbed. F63 unblocked.
  - **✅ `kinlight.app` purchased** (Cloudflare Registrar, June 2026) — primary domain confirmed.
  - **Remaining verification (Anggi's action):** IP Australia TM Checker (ipaustralia.gov.au, free, 5 min) + ASIC business-name search (abr.business.gov.au, free). `kinlight.com.au` available once ABN is issued — register via VentraIP.
  - **Social handles:** @kinlight_app on X ✅ · @kinlight.app on Instagram ✅ (both June 2026 — @kinlight was taken on X).
  - **Sender address:** `hello@kinlight.app` ✅ — active (Resend domain verified June 2026, FROM_EMAIL updated).
  - **Meaning:** *kin* (who you protect) + *light* (a light kept on for them). A kinlight is the lamp a keeper tends so their people can find their way home safely.
  - **Tagline (primary):** "A light left on for the people you love." Short form: "The light stays on."
- **Logo:** **The Lantern** — a harbour lantern (the vessel built to *keep* a flame burning through wind), sage frame + amber glow. Night-water tile (`#22302b`) for app icon / email avatar; daylight tile (white) for in-app + documents. Source SVG lives in the brand guide (`kinlight-brand-guide.html`). At ≤16px the handle drops and the favicon leans on the glowing body.
- **Brand world (theme):** lighthouse = *you* (checking in tends the light); safe harbour = *the vault* (things moored somewhere calm); anchor = *your people* (steadiness in the worst week). Theme lives in logo + copy only, never as decoration.
- **Design system:** "Aeterna Solid" — Linen Cream + Warm Sage + Warm Charcoal (unchanged; colours renamed to brand roles: amber = "lamplight," sage = "harbour")
- **Tone:** Calm, plain, certain — "a light left on." Status moments may use the lighthouse metaphor ("Light on", "Your light is on again"); **urgent/overdue states stay literal** ("Action needed.") — clarity outranks brand. The voice may only describe protection that is actually built (false-confidence guard).
- **Primary colour:** `#2e2b26` (warm charcoal — replaced navy `#002147`)
- **Primary dark variant:** `#3d3a34`
- **Accent colour:** `#5a7a6e` (warm sage — replaced teal `#2d8a7a`)
- **Amber colour:** `#c47a20` (amber — used for F05 "due soon" reminders, and F45 "almost there" hero state)
- **Background:** `#faf6f0` (linen cream — replaced `#f8f9fb`)
- **Card background:** `#ede5d8` (warm card — replaced `#f2f4f6`)
- **Card surface:** `#fdf9f4` (warm white — replaced `#ffffff`)
- **Gradient:** `linear-gradient(135deg, #2e2b26 0%, #3d3a34 100%)`
- **Fonts:** Manrope (headlines, 800 weight) + Public Sans (body)
- **Design rules:**
  - No 1px borders for sectioning — use background colour shifts
  - Minimum tap target: 48×48dp
  - Rounded cards (`border-radius: 16px`)
  - Ambient shadows only
  - No pure black — use `#2e2b26` for dark tones
  - Icons on gradient/dark backgrounds use `var(--s)` (linen cream), not `#fff`

### CSS Variable Reference (`:root`)
```css
--p: #2e2b26        /* warm charcoal — primary */
--pc: #3d3a34       /* charcoal variant */
--s: #faf6f0        /* linen cream — page background */
--sl: #ede5d8       /* warm card background */
--sc: #e5ddd0       /* stepper/toggle background */
--sh: #ddd5c8       /* dividers, borders */
--sx: #d5ccbe       /* input backgrounds */
--w: #fdf9f4        /* warm white — card surface */
--os: #2e2b26       /* body text */
--ov: #6b6358       /* secondary text */
--ov2: #b8b0a0      /* tertiary/placeholder */
--ac: #5a7a6e       /* warm sage — accent */
--al: rgba(90,122,110,.10)  /* sage light fill */
--ab: rgba(90,122,110,.22)  /* sage border */
--sec: #8a7d6e      /* section labels, icons */
--scc: #ddd5c8      /* secondary badge background */
--er: #ba1a1a       /* error/overdue red */
--am: #c47a20       /* amber — due soon + F45 "almost there" state */
--am-bg: rgba(196,122,32,.08)
--am-border: rgba(196,122,32,.25)
--g: linear-gradient(135deg, #2e2b26 0%, #3d3a34 100%)
```

---

## Tech Stack

### Current (user testing)
- Single-file HTML/CSS/JavaScript (`index.html`)
- **Login wall** — username + password, JWT token in sessionStorage
- **F41: Server-first vault load** — `loadFromServer()` fetches `GET /vault` on login; localStorage used as offline cache/fallback only. Server is now source of truth.
- **Vault sync** (F39-1): every `save()` call silently POSTs vault to `/vault/sync` — server has a copy
- **Pulse scanner** (F39-2): APScheduler runs `run_pulse_scan()` every hour inside the FastAPI app
- **Email delivery** (F39-3): Resend sends plain-text notification emails to contacts when overdue detected
- **PDF attachment** (F39-4): ReportLab generates server-side PDF; attached to notification email via Resend REST API (direct HTTP, not SDK). PDF mirrors the 6-page jsPDF client-side package.
- jsPDF (via CDN) for client-side PDF generation (unchanged)
- **Frontend:** GitHub Pages (`ramenfanclub.github.io/emergency-exit`) — auto-deploys on `git push`
- **Backend:** GCE e2-micro free tier (`api.kinlight.app`) — auto-deploys on `git push` via GitHub Actions SSH
- **Database:** MongoDB Atlas on Google Cloud
- **Email provider:** Resend (`resend.com`) — free tier, 100 emails/day
- **CI:** GitHub Actions — `.github/workflows/ci.yml` runs 4 jobs on every push to `main`: pytest, frontend sync check, Playwright browser tests, pip-audit dependency scan
- **Test suite:** `identity-service/test_main.py` — 181 pytest tests covering all backend features
- **JWT library:** `PyJWT` (replaces `python-jose` — update `requirements.txt` accordingly)

### Planned (production) — aspirational, not yet started
- **Frontend:** React Native (iOS + Android) + React (web)
- **Backend:** Python FastAPI microservices
- **Database:** MongoDB (one DB per service)
- **Message broker:** RabbitMQ (domain events between services)
- **Notifications:** Email (Resend/SendGrid), SMS (Twilio), WhatsApp, push
- **Auth:** Biometric (Face ID / Touch ID), PIN, JWT + MFA
- **Login:** Email + password OR Google login (replacing username-only testing auth)

> **Repo cleanup, June 2026:** An early, never-finished attempt at this microservices split existed in the repo as dead scaffold code — `identity-service/app/` (domain/application/infrastructure layers, a stub `app/main.py` with only a `/health` endpoint) plus six sibling service folders (`api-gateway`, `vault-service`, `wishes-service`, `guardian-service`, `pulse-service`, `notification-service`) and a `docker-compose.yml` describing how they'd connect via MongoDB + RabbitMQ. None of this was ever wired up — Railway's `Procfile`/`Dockerfile` always pointed at the flat `identity-service/main.py` monolith, confirmed via direct inspection. All of it has been deleted, along with a duplicate `identity-service/tests/` folder that only existed to test the dead scaffold (`test_main.py` is the real, live test suite and was untouched). Two orphaned Railway env vars from this effort, `CSFLE_MASTER_KEY` and `RABBITMQ_URL`, were also removed — neither was referenced anywhere in the live codebase. Root `.env.example` was also rewritten — it previously documented env vars for the dead scaffold (`MONGO_DB_VAULT`, `RABBITMQ_URL`, `CSFLE_MASTER_KEY`, etc., none read by `main.py`) and was the likely source of the orphaned Railway vars in the first place. It now lists exactly what `main.py` reads: `MONGO_URI`, `JWT_SECRET`, `RESEND_API_KEY`, `VAULT_ENCRYPTION_KEY`. If the microservices split above is ever pursued for real, start fresh rather than resurrecting this scaffold.

---

## CI/CD

- **GitHub Pages** auto-deploys on every push to `main` — CD is already live
- **GitHub Actions** (`.github/workflows/ci.yml`) — four jobs run on every push:
  1. **Backend Tests** — runs `pytest test_main.py -v` inside `identity-service/`. Deploy does not proceed if any test fails.
  2. **Frontend Sync Check** — confirms `./index.html` and `./frontend/index.html` are identical
  3. **Frontend Tests (Playwright)** — runs Playwright browser tests against `index.html` in Chromium
  4. **Dependency Audit (pip-audit)** — checks all pinned packages against known CVE database (F90)
- **Branch protection (F92):** `main` requires all 4 CI status checks to pass before merging. PRs not required (solo dev).
- **Railway** auto-deploys backend on every push to `main`
- **Developer workflow:**
  1. Edit `./index.html` in VSCode
  2. Run `cp index.html frontend/index.html` in terminal
  3. `git add -A && git commit -m "..." && git push`
  4. GitHub Actions runs 4 checks (pytest + sync + Playwright + pip-audit) ✅ → Pages deploys automatically 🚀
  5. Railway picks up backend changes and redeploys automatically 🚀

### Running tests locally (before pushing)
```bash
cd identity-service
python3 -m pytest test_main.py -v
```
Expected output: `214 passed` — if any fail, fix before pushing.

---

## App Structure — 5 Screens

### 1. Home (Dashboard)
- **F45: Hero headline** — 5 states (priority order):
  1. `"Action needed."` — red, when overdue + has contacts (F01)
  2. `"Let's get you set up."` — sage accent, completeness < 30%
  3. `"You're making progress."` — sage accent, completeness 30–69%
  4. `"Almost there — now check in."` — **amber**, completeness ≥ 70% but no check-in yet (new state)
  5. `"Everything is in order."` — sage accent, completeness ≥ 70% + checked in
- Asset + wish count summary with status badge: Active (sage), Due Soon (amber — F05), or Overdue (red — F01)
- **Privacy note** (F31): Small sage lock icon line below summary — "Your information is encrypted and stored securely in the cloud."
- **F44: First-run explainer card** — shown once on first login, above the Vitality Pulse card, below the privacy note. Three numbered steps explaining the core mechanic. Dismissed with a tap; sets `ee_onboarded = true` in localStorage permanently.
- **F05: Reminder banner** — amber gradient card shown above Vitality Pulse when check-in approaching but NOT yet overdue
- **F01: Overdue alert banner** — red gradient card shown when grace period has expired and contacts exist
- **Vitality Pulse** — animated pulsing heart, tap to confirm alive. Normal: sage. Due soon: amber. Overdue: red `heart_broken`.
- **F01: Notification queue card** — shown on Home only when overdue
- Next check-in date + days remaining countdown
- Asset Ledger CTA (full-width gradient button)
- Compact pill buttons: **Add Asset** (charcoal) + **My Wishes** (warm card)
- Home completeness % with progress bar (sage) + actionable tips
- Recent activity log
- Screen ID: `s-home` | Nav ID: `n-home`

### 2. Asset Ledger
- Title: "My Assets", subtitle: "A record of what you have, so the right people know where to find it."
- Full-width gradient CTA: "Record New Asset"
- Assets grouped by category with category icon and item count
- Per-asset: name, detail snippet, beneficiary, value, edit button, delete button (`.del-btn`)
- Empty state (F23) with encouraging copy and CTA
- Completeness nudge at the bottom
- Screen ID: `s-ledger` | Nav ID: `n-ledger`

### 3. My Wishes
- Title: "My Wishes", subtitle: "What matters to you, written down so it's not forgotten."
- Full-width gradient CTA: "Add a Wish"
- **Will & Legal Documents** card — status badge, Will details, supplementary docs
- **Statement of Wishes** nudge card (F32) — orange accent, shown until SOW is recorded
- Wishes grouped by category with priority badge (high/medium/low), edit and delete buttons
- Empty state (F23)
- Screen ID: `s-wishes` | Nav ID: `n-wishes`

### 4. Contact
- Title: "Contacts", subtitle: "People to notify and how to reach them"
- Section: "My Contacts" with Add Contact button (`.ob` style in section header)
- Contact cards: initials avatar, full name, relationship, letter status pill, notify method, sequence number with reorder arrows, delete button, write letter button, preview PDF button
- Add Contact modal fields: First name, Last name, Relationship, Email, Phone, Notify via
- **All contacts receive the full package — there are no access level tiers**
- Screen ID: `s-kin` | Nav ID: `n-kin`

### 5. Settings
- **Check-in Frequency** — stepper (1–24 Weeks or Months)
- **Grace Period** — stepper in days (1–30 days, default 7). Helper text recommends at least 7 days. (F56)
- **Notification Protocol** — 3 radio options (ping_then_notify / notify_immediately / escalate)
- **Verification** — FaceID/Biometrics or Secure Passcode
- All changes auto-save immediately with toast feedback (F35)
- Screen ID: `s-config` | Nav ID: `n-config`

---

## Navigation

Bottom tab bar (5 tabs, rounded top corners):

| Tab | Icon | Screen ID | Nav ID |
|-----|------|-----------|--------|
| Home | `shield` | `s-home` | `n-home` |
| Assets | `account_balance` | `s-ledger` | `n-ledger` |
| Wishes | `auto_stories` | `s-wishes` | `n-wishes` |
| Contact | `family_history` | `s-kin` | `n-kin` |
| Settings | `settings_suggest` | `s-config` | `n-config` |

Active tab: linen cream icon/label on charcoal pill. Inactive: charcoal at 35% opacity.

---

## Key UI Patterns

- **Modals:** Bottom-sheet, drag handle at top, tap outside to dismiss
- **Gradient buttons:** Full-width, 18px padding, 3px border-bottom for depth, active scale(0.97)
- **Compact action buttons:** Pill-shaped, side by side, 10px vertical padding
- **Cards:** `border-radius: 16px`, `#ede5d8` or `#fdf9f4` with ambient shadow
- **Card background rule (F27):** `.cardw` (warm white + shadow) = interactive or primary sections. `.card` (warm card bg) = informational or contextual grouping containers.
- **Secondary add-action buttons (F26):** All "add" actions in section headers use `.ob` style
- **Tags/badges:** Uppercase, 10px, pill-shaped
- **Toast notifications:** Fixed, centred, charcoal pill with linen cream text, auto-dismiss 2.4s. All toasts include ✓
- **Delete button (F34):** All delete actions use `.del-btn` CSS class
- **Settings auto-save (F35):** All Settings controls save immediately on change with toast feedback
- **First-run explainer (F44):** `.explainer-card` with `.explainer-step` numbered list. Dismissed via `dismissExplainer()` which sets `ee_onboarded = true` in localStorage. `showExplainerIfNew()` called inside `render()` on every render. Smooth fade-out on dismiss.
- **Hero headline (F45):** 5-state logic in `render()`. Priority: overdue > low pct > mid pct > ready-but-no-checkin (amber) > all clear. The amber "almost there" state catches users who completed their vault but haven't tapped check-in yet.

---

## Backend (Identity Service on GCE)

The backend is on Google Compute Engine free tier (`api.kinlight.app`) — an e2-micro VM in us-west1 running Docker + nginx + certbot.

### identity-service/main.py structure
```
- CORS middleware
- MongoDB connection (users + vaults collections)
- Resend API key loaded from environment variable RESEND_API_KEY
- APScheduler — runs run_pulse_scan() every hour
- JWT auth helpers
- F41 schema helpers — ms_to_dt(), dt_to_ms(), extract_vault_fields(), reconstruct_vault_blob()
- ReportLab PDF generation — generate_pdf_for_contact()
- send_notification_email() — generates PDF, attaches via Resend REST API direct HTTP call
- send_allclear_email() — sends warm recovery email via Resend SDK
- send_nomination_email() — F63: sends nomination email to newly-added contact
- get_contacts_to_notify() — protocol logic (ping_then_notify / notify_immediately / escalate)
- run_pulse_scan() — hourly scanner, detects overdue vaults, triggers emails with PDF, writes F93 heartbeat to system_col on every run
- All API routes
- startup() — creates MongoDB indexes on boot
- F60: is_reminder_due() — checks 25% threshold, guards with reminderSent flag
- F60: send_reminder_email() — warm email to vault holder when check-in approaching
```

### Key implementation notes
- `bson` must NOT be in `requirements.txt` — pymongo bundles its own bson. Adding it separately causes an `ImportError: cannot import name 'SON'` crash.
- `from bson import ObjectId` works because pymongo's bson is available after pymongo is installed.
- Test inbox is hardcoded as `buat.nonton8282@gmail.com` — swap to `contact["email"]` when going live.
- Emails send from `onboarding@resend.dev` (Resend sandbox) — verify a custom domain before going live.
- `overdueNotificationSent` flag in MongoDB prevents re-sending emails every hour for the same overdue event.
- For `escalate` protocol, `overdueNotificationSent` is NOT set to True — scanner keeps running to notify new contacts each day.
- `send_allclear_email()` — sends a warm reassuring email to each contact when vault holder checks in after being overdue. Called inside `POST /checkin` only when `overdueNotificationSent` was True before reset.
- `POST /checkin` response includes `allclear_sent: true/false` and `allclear_count` so caller knows if all-clear emails were triggered.
- **F39-4 PDF generation:** Uses ReportLab (open source, BSD licence, pure Python — no system dependencies). PDF is built in-memory via `io.BytesIO()` — never touches the filesystem. Attached to email as base64 string via direct Resend REST API call (`requests.post` to `https://api.resend.com/emails`). The Resend Python SDK is NOT used for notification emails — the direct HTTP call is more reliable for attachments.
- **Critical variable naming:** In `generate_pdf_for_contact()`, never use `doc` as a loop variable — it shadows the `SimpleDocTemplate` object. Use `supp_doc` for supplementary document loops.
- `requests` library used for direct Resend API calls — confirm it is in `requirements.txt`.
- **F41 or-fallback bug:** Never use `content.get("kin") or fallback` — empty list `[]` is falsy in Python and would incorrectly fall through to the old schema. Always use explicit `None` check: `kin = content.get("kin"); contacts = kin if kin is not None else fallback`.
- **MongoDB indexes** created on startup: `userId` (unique), `lastCheckin`, compound `(overdueNotificationSent, lastCheckin)`, compound `(reminderSent, lastCheckin)`. Safe to call `create_index` on every startup — MongoDB skips silently if index already exists.
- **F60 reminder guard:** `reminderSent` flag on vault document prevents sending the reminder email more than once per check-in cycle. Reset to `False` on every `POST /checkin` alongside `overdueNotificationSent`. New vaults get `reminderSent: False` via `$setOnInsert` in vault sync.
- **F60 threshold logic:** `is_reminder_due()` mirrors the frontend F05 25% rule — `threshold_days = max(7, round(interval_days * 0.25))`. Reminder fires when `0 <= days_remaining <= threshold_days`. Negative days_remaining (overdue) is excluded so the overdue scanner handles that path.
- **JWT library:** Project uses `PyJWT` (imported as `import jwt`). `python-jose` was removed — do not re-add it to `requirements.txt`.

### API Endpoints
| Method | Endpoint | Auth required | Purpose |
|--------|----------|---------------|---------|
| GET | `/health` | No | Confirm server is running |
| POST | `/auth/login` | No | Login with username + password, returns JWT token |
| GET | `/auth/me` | Yes | Get current user's profile |
| GET | `/admin/testers` | Yes | List all tester accounts |
| POST | `/vault/sync` | Yes | Store vault using structured MongoDB schema (F41) |
| GET | `/vault` | Yes | Return vault blob to frontend on login (F41) |
| POST | `/checkin` | Yes | Record check-in server-side, clear overdue flag |
| POST | `/admin/trigger-pulse` | Yes | Manually trigger the pulse scan immediately (testing) |
| POST | `/admin/force-overdue` | Yes | Set vault lastCheckin to 2020 to simulate overdue state (testing) |
| POST | `/admin/force-reminder` | Yes | Set vault lastCheckin to just inside reminder threshold for F60 testing |
| POST | `/admin/force-stale-pulse` | Yes | F93: Backdate the pulse scanner heartbeat to simulate a dead scanner, for testing /health's 503 path |
| GET | `/push/key` | No | F101: Return VAPID public key for Web Push subscription |
| POST | `/push/subscribe` | Yes | F101: Store a push notification subscription for the current user |
| POST | `/push/unsubscribe` | Yes | F101: Remove a push notification subscription |

### Environment Variables (set in Railway dashboard, never committed)
```
MONGO_URI=mongodb+srv://...
JWT_SECRET=...
RESEND_API_KEY=re_...
VAULT_ENCRYPTION_KEY=...  (F04: 64-char hex AES-256-GCM key)
VAPID_PRIVATE_KEY=...     (F101: URL-safe base64 VAPID private key)
VAPID_PUBLIC_KEY=...      (F101: URL-safe base64 VAPID public key)
```

### Testing the pulse scanner end-to-end
1. Go to `https://api.kinlight.app/docs`
2. Login via `POST /auth/login` → copy the token
3. Click Authorize (top right) → paste `Bearer <token>` → Authorize
4. `POST /admin/force-overdue` — sets vault to overdue
5. `POST /admin/trigger-pulse` — runs scanner immediately
6. Check test inbox for email with PDF attachment
7. `POST /checkin` — resets vault back to normal

---

## Data Model

```javascript
// localStorage key: 'ee_v3' (also used as offline cache — server is source of truth from F41)
{
  assets: [{ id, name, category, value, details, beneficiary, notes }],
  wishes: [{ id, category, title, details, priority: 'high'|'medium'|'low' }],
  will: { status: 'signed'|'draft'|'none', date, solicitor, loc1, loc2, notes } | null,
  suppDocs: [{ id, type, name, loc, notes }],
  kin: [{ id, first, last, rel, email, phone, notifyVia, order, letter }],
  lastCheckin: timestamp | null,
  fc: number,           // check-in frequency count
  fu: 'weeks'|'months', // frequency unit
  gp: number,           // grace period in days — default 7 (F56)
  v: 'face'|'pin',
  notifyProto: 'ping_then_notify'|'notify_immediately'|'escalate',
  log: [{ msg, time }],
  saveCount: number
}
```

### localStorage flags (outside ee_v3)
```
ee_onboarded: 'true'   ← set on first dismissal of F44 explainer card. Persists forever.
```

### MongoDB vaults collection — F41 structured schema
```
{
  userId: ObjectId,              ← indexed (unique), links to users collection
  lastCheckin: ISODate,          ← indexed (top-level for pulse scanner queries)
  checkInFrequency: Number,      ← maps to S.fc
  checkInUnit: String,           ← "weeks" | "months"
  gracePeriodDays: Number,       ← maps to S.gp — default 7 (F56)
  notifyProto: String,
  overdueNotificationSent: Boolean,  ← indexed (compound with lastCheckin)
  reminderSent: Boolean,             ← F60: prevents duplicate reminder emails per cycle; reset on check-in
  content: {                     ← vault data — always read/written together → embedded
    assets, wishes, will, suppDocs, kin, v, notifySeq, saveCount
  },
  log: [...],                    ← capped at 20 entries in frontend — safe to embed
  syncedAt: ISODate,
  createdAt: ISODate,
  updatedAt: ISODate
}
```

#### Schema design decisions (MongoDB best practices)
- **Check-in fields at top level** — `lastCheckin`, `gracePeriodDays`, `overdueNotificationSent`, `reminderSent` are queried by the pulse scanner every hour. Top-level = indexable = fast.
- **Vault content embedded** — assets, wishes, contacts always read and written together → embed, don't reference.
- **Log embedded** — bounded at 20 entries in frontend JS, always read with vault → safe to embed.
- **Revisit log schema when:** building an audit trail, admin dashboard, or unlimited history feature. At that point, move logs to a separate `logs` collection with `userId`, `event`, `detail`, `timestamp` fields.
- **Backward compatibility** — old vault docs (pre-F41) stored content in a `vault` blob field. All fallback lookups use explicit `None` checks (not `or`) to handle empty arrays correctly.

### MongoDB users collection
```
_id, username, password (bcrypt hash), name, ageGroup, hasWill,
notes, isTester, isAdmin, createdAt, lastLogin
```
**Tester accounts:** tester_01 through tester_06. All passwords updated to bcrypt hash of `Benny#07`. No `isAdmin` field (defaults to `false`).
**Admin account:** `anggi` — `isTester: false`, `isAdmin: true`. Added F77. ⚠️ Currently shares the same temporary password as testers (`Benny#07`) — change before launch.

---

## Test Suite

**File:** `identity-service/test_main.py`
**Run:** `python3 -m pytest test_main.py -v`
**Expected:** 214 passed

### Coverage by feature

| Test class | Feature covered | Count |
|---|---|---|
| `TestMsToDt` / `TestDtToMs` | F41 timestamp conversion | 7 |
| `TestExtractVaultFields` | F41 schema structuring | 9 |
| `TestReconstructVaultBlob` | F41 round-trip fidelity | 7 |
| `TestGetContactsToNotify` | F39-7 all 3 protocols | 13 |
| `TestPasswordHelpers` | F40 auth — bcrypt | 6 |
| `TestCreateToken` | F40 auth — JWT | 3 |
| `TestCleanUser` | F40 data safety | 5 |
| `TestOverdueCalculationLogic` | F39-2 pulse scanner maths | 5 |
| `TestBackwardCompatibility` | F41 migration safety | 3 |
| `TestAllClearLogic` | F39-8 recovery emails | 4 |
| `TestCompletenessLogic` | Completeness score (7 checks) | 6 |
| `TestReminderLogic` | F60 reminder threshold + email | 11 |
| `TestNominationEmail` | F63 nomination email | 5 |
| `TestNominationValidation` | F79 contact-in-vault check | 5 |
| `TestWarningLogic` | F64-2 escalating warning emails — should_send_warning, should_notify_contacts, email content, guard logic | 13 |
| `TestPasswordReset` | F66 password reset — token hashing, expiry, single-use, email | 14 |
| `TestRequireAdmin` | F77 admin role check — require_admin(), clean_user isAdmin | 6 |
| `TestPdfEscaping` | F83 HTML/XML escape in ReportLab PDF generation | 8 |
| `TestJwtSecretValidation` | F84 JWT_SECRET startup validation — missing, empty, valid | 3 |
| `TestAccountLockout` | F86 account lockout — lock/unlock logic, counter, threshold, clear on login/reset | 10 |
| `TestSecurityHeaders` | F94 security response headers — middleware registered, X-Content-Type-Options, X-Frame-Options, HSTS, Referrer-Policy (async direct call, no TestClient) | 5 |
| `TestVaultSyncLimits` | F96 vault sync input limits — max assets, max contacts, type check, valid payload | 4 |
| `TestStrongerPasswordPolicy` | F97 password policy — common passwords, alpha-only, length, valid, case-insensitive, empty/None | 6 |
| `TestPulseScannerHealth` | F93 pulse scanner heartbeat — healthy/unhealthy thresholds, 503 on stale or missing heartbeat, boundary case, heartbeat write on scan | 5 |
| `TestEncryptContent` / `TestDecryptContent` / `TestReconstructWithEncryption` | F04 AES-256-GCM vault encryption — encrypt, decrypt, round-trip, idempotent, nonce uniqueness, plaintext passthrough | 25 |
| `TestLoginRateLimit` / `TestPasswordResetRateLimit` / `TestNominateRateLimit` | F91 rate limiting — per-IP and per-user buckets, X-Forwarded-For leftmost IP, 429 on threshold | 8 |
| `TestMaskEmail` / `TestJsonFormatter` | F95 structured logging — email masking, JSON formatter output | 9 |
| `TestSecurityLogging` | F95 auth event logging — login failure, lockout, log doesn't leak password | 3 |
| `TestUpdateAccount` | PATCH /auth/me — name, email, validation, normalization | 7 |
| `TestPushNotifications` | F101 push notifications — GET /push/key, subscribe (store/update/validate/auth), unsubscribe (remove/empty/auth) | 9 |

### Frontend test coverage
F44, F45, and other frontend features are not covered by the pytest suite — pytest only covers the Python backend. Frontend test coverage requires a browser automation tool (e.g. Playwright). This is tracked as a future infrastructure task. See F58 in the backlog.

### Adding tests for new features
When building a new feature, add a new `class TestFeatureName` block to `test_main.py` before implementing. Tests run automatically on every push via GitHub Actions.

---

## Completeness Score — 7 Checks (~14.3% each)

1. At least one asset recorded
2. At least one asset has a beneficiary assigned
3. At least one wish recorded
4. Will details recorded
5. Statement of Wishes recorded (via suppDocs)
6. At least one contact added
7. First check-in completed

---

## Coding Conventions

- CSS variables for all colours (`--p`, `--ac`, `--sec`, `--am`, etc.)
- Short class names to keep file size small
- Mobile-first — max-width 430px, touch targets min 48px
- State loading: `S={...S,...parsed}` to safely merge new default fields
- Monetary values: always `Math.round().toLocaleString()`
- localStorage key is `ee_v3`
- Separate localStorage flags (outside `ee_v3`) used for one-time UI state: `ee_onboarded`
- When editing index.html, always update BOTH `./index.html` AND `./frontend/index.html`
- jsPDF loaded via CDN in `<head>`
- `API` constant in JS points to `https://api.kinlight.app`
- Login token stored in `sessionStorage` (not localStorage — clears on tab close)
- Vault sync is silent — never show errors to the user if sync fails
- **F41 fallback pattern:** use `_get_content_or_legacy(doc, key, fallback)` helper — do not inline the None-check pattern again
- **JS helpers:** use `$(id)` not `document.getElementById(id)`, `pl(n, word)` for plurals, `trunc(s)` for truncation, `initials(k)` for contact initials, `authHeader()` for Bearer token header
- **JS constants:** lookup tables (`ASSET_ICONS`, `WISH_CATS`, `WISH_ICONS`, `NOTIFY_LABELS`, etc.) live at the top of the script block — do not redeclare them inside `render()` or other functions
- **JS state reset:** use `S = {...DEFAULT_STATE}` in `doLogout()` — do not hardcode the reset object inline
- **PyMongo objects:** always use `if db is not None` / `if client is not None` — never `if db` or `if client`. Newer PyMongo raises `NotImplementedError` on boolean checks of Database objects
- **Script tag in `<head>`:** the `API` constant lives in its own `<script>` block that must be closed with `</script>` before the `<style>` block opens. Any rewrite of the `<head>` section must preserve this — missing it causes the entire CSS to be parsed as JavaScript and breaks the login screen silently

---

## What NOT to Do

- Do not use Inter, Roboto, or Arial — always Manrope + Public Sans
- Do not use pure black — use `#2e2b26`
- Do not use the old navy palette (`#002147`, `#003366`, `rgba(0,33,71,...)`)
- Do not use the old teal accent (`#2d8a7a`, `rgba(45,138,122,...)`) — use sage `#5a7a6e`
- Do not use the old terminology (Vault, Guardian, Kin, Config, Legacy Wishes)
- Do not render monetary values as raw floats
- Do not forget to update BOTH index.html files (root and `frontend/`)
- Do not use `ee_v2` as localStorage key — it is `ee_v3`
- Do not remove the Statement of Wishes prompt
- Do not reintroduce contact access levels — all contacts receive the full package
- Do not remove login wall element IDs (`#login-wall`, `#li-user`, `#li-pass`, `#login-err`, `#logout-btn`, `#user-greeting`)
- Do not hardcode the MongoDB password anywhere in committed files
- Do not commit the `.env` file
- Do not show vault sync errors to the user — fail silently
- Do not add `bson` to `requirements.txt` — pymongo bundles its own bson and they conflict
- Do not use `doc` as a loop variable inside `generate_pdf_for_contact()` — it shadows the SimpleDocTemplate object. Use `supp_doc` instead.
- Do not use the Resend Python SDK for notification emails with attachments — use `requests.post` to `https://api.resend.com/emails` directly
- Do not use `or` for fallback when reading `content.kin` — empty list `[]` is falsy and would silently fall through. Use explicit `None` check.
- Do not push without running `python3 -m pytest test_main.py -v` first (or rely on GitHub Actions to catch it)
- Do not store one-time UI flags (like `ee_onboarded`) inside the `ee_v3` blob — keep them as separate localStorage keys so they survive vault resets
- Do not use `if db` or `if client` for PyMongo objects — always compare with `is not None`
- Do not rewrite the `<head>` section without verifying `</script>` appears before `<style>` — missing this breaks the login screen with no visible error
- Do not declare `const API` inside the main script block — it is already declared in the `<head>` script. A duplicate `const` declaration causes a SyntaxError that silently prevents all JS from loading, breaking the login screen
- Do not delete the `save()` function — it is the core persistence function called by every CRUD operation. Its body must be: `try{localStorage.setItem('ee_v3',JSON.stringify(S));}catch(e){} syncVault(); render();`


## Feature Backlog

**Status tracked in:** `docs/features.md` — see that file for the full Must/Should/Could/Won't backlog.

---

## End-of-Chat Checklist

- [ ] Download the new `main.py` (if changed)
- [ ] Download the new `test_main.py` (if changed)
- [ ] Download the new `CLAUDE.md`, `AGENTS.md`, `docs/features.md` (if changed)
- [ ] Did anything structural change? Update `AGENTS.md` (session rules) or `CLAUDE.md` (full reference). Feature status changes → `docs/features.md`.
- [ ] Replace `identity-service/main.py` in VS Code
- [ ] Replace `identity-service/test_main.py` in VS Code
- [ ] `cp index.html frontend/index.html && cp manifest.json sw.js favicon.svg icon-192.png icon-512.png frontend/`
- [ ] Run `./test.sh` — confirm 214 passed before pushing
- [ ] `git add -A`
- [ ] `git commit -m "..."`
- [ ] `git push`
- [ ] GitHub Actions runs pytest + sync check ✅
- [ ] Railway redeploys backend automatically ✅
