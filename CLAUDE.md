# Emergency Exit — Project Specification & Memory

> **IMPORTANT:** This file is the persistent memory for this Claude Project.
> Update it at the end of every session and push to `main` before closing.
> Both `./index.html` AND `./frontend/index.html` must always be kept in sync.

> **SESSION START INSTRUCTION:** At the beginning of every new chat, always check
> the project-level files synced from GitHub (CLAUDE.md and index.html). These are
> the source of truth. Do not rely on memory summaries alone — read the actual
> documents provided in context before making any assumptions about current state.

---

## Login (Frontend)

The `index.html` includes a login wall:

- **Login screen** appears before anything else
- **Token** stored in `sessionStorage` (clears when browser tab closes)
- **"Sign out"** button in the header
- **"Hi, [Name]"** greeting shown after login
- **API calls** point to `https://emergency-exit-production.up.railway.app`
- **Enter key** submits the login form
- **Session persistence** — if token exists in sessionStorage, login wall is skipped
- **Tester accounts:** tester_01 through tester_06, all share password `Benny#07`

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
- `API` constant — set to `https://emergency-exit-production.up.railway.app`

---

## App Name & Branding

- **App name:** Emergency Exit
- **Design system:** "Aeterna Solid" — Linen Cream + Warm Sage + Warm Charcoal
- **Tone:** Calm, trustworthy, premium — a "Digital Sanctuary"
- **Primary colour:** `#2e2b26` (warm charcoal — replaced navy `#002147`)
- **Primary dark variant:** `#3d3a34`
- **Accent colour:** `#5a7a6e` (warm sage — replaced teal `#2d8a7a`)
- **Amber colour:** `#c47a20` (amber — used for F05 "due soon" reminders, unchanged)
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
--am: #c47a20       /* amber — due soon */
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
- **Backend:** Railway (`emergency-exit-production.up.railway.app`) — auto-deploys on `git push`
- **Database:** MongoDB Atlas on Google Cloud
- **Email provider:** Resend (`resend.com`) — free tier, 100 emails/day
- **CI:** GitHub Actions — `.github/workflows/ci.yml` runs pytest + frontend sync check on every push to `main`
- **Test suite:** `identity-service/test_main.py` — 69 pytest tests covering all backend features

### Planned (production)
- **Frontend:** React Native (iOS + Android) + React (web)
- **Backend:** Python FastAPI microservices
- **Database:** MongoDB (one DB per service)
- **Message broker:** RabbitMQ (domain events between services)
- **Notifications:** Email (Resend/SendGrid), SMS (Twilio), WhatsApp, push
- **Auth:** Biometric (Face ID / Touch ID), PIN, JWT + MFA
- **Login:** Email + password OR Google login (replacing username-only testing auth)

---

## CI/CD

- **GitHub Pages** auto-deploys on every push to `main` — CD is already live
- **GitHub Actions** (`.github/workflows/ci.yml`) — two jobs run on every push:
  1. **Backend Tests** — runs `pytest test_main.py -v` inside `identity-service/`. Deploy does not proceed if any test fails.
  2. **Frontend Sync Check** — confirms `./index.html` and `./frontend/index.html` are identical
- **Railway** auto-deploys backend on every push to `main`
- **Developer workflow:**
  1. Edit `./index.html` in VSCode
  2. Run `cp index.html frontend/index.html` in terminal
  3. `git add -A && git commit -m "..." && git push`
  4. GitHub Actions runs pytest + sync check ✅ → Pages deploys automatically 🚀
  5. Railway picks up backend changes and redeploys automatically 🚀

### Running tests locally (before pushing)
```bash
cd identity-service
python3 -m pytest test_main.py -v
```
Expected output: `69 passed` — if any fail, fix before pushing.

---

## App Structure — 5 Screens

### 1. Home (Dashboard)
- Hero headline: "Everything is in order." (normal) / "Action needed." (overdue — F01)
- Asset + wish count summary with status badge: Active (sage), Due Soon (amber — F05), or Overdue (red — F01)
- **Privacy note** (F31): Small sage lock icon line below summary — "Your information is stored on this device only — it never leaves your phone." Update when backend cloud storage launches.
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
- Title: "Asset Ledger", subtitle: "A record of what you have, so the right people know where to find it."
- Full-width gradient CTA: "Record New Asset"
- Assets grouped by category with category icon and item count
- Per-asset: name, detail snippet, beneficiary, value, edit button, delete button (`.del-btn`)
- Empty state (F23) with encouraging copy and CTA
- Completeness nudge at the bottom
- Screen ID: `s-ledger` | Nav ID: `n-ledger`

### 3. My Wishes
- Title: "My Wishes", subtitle: "What matters to you, written down so it's not forgotten."
- Full-width gradient CTA: "New Instruction"
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
- **Grace Period** — stepper in days (1–30 days, default 3)
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
| Ledger | `account_balance` | `s-ledger` | `n-ledger` |
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

---

## Backend (Identity Service on Railway)

The backend is on Railway (`emergency-exit-production.up.railway.app`), NOT the VM. The Google Cloud VM (`e2-micro`) is no longer used and can be shut down.

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
- get_contacts_to_notify() — protocol logic (ping_then_notify / notify_immediately / escalate)
- run_pulse_scan() — hourly scanner, detects overdue vaults, triggers emails with PDF
- All API routes
- startup() — creates MongoDB indexes on boot
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
- **MongoDB indexes** created on startup: `userId` (unique), `lastCheckin`, compound `(overdueNotificationSent, lastCheckin)`. Safe to call `create_index` on every startup — MongoDB skips silently if index already exists.

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

### Environment Variables (set in Railway dashboard, never committed)
```
MONGO_URI=mongodb+srv://...
JWT_SECRET=...
RESEND_API_KEY=re_...
```

### Testing the pulse scanner end-to-end
1. Go to `https://emergency-exit-production.up.railway.app/docs`
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
  gp: number,           // grace period in days
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
  gracePeriodDays: Number,       ← maps to S.gp
  notifyProto: String,
  overdueNotificationSent: Boolean,  ← indexed (compound with lastCheckin)
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
- **Check-in fields at top level** — `lastCheckin`, `gracePeriodDays`, `overdueNotificationSent` are queried by the pulse scanner every hour. Top-level = indexable = fast.
- **Vault content embedded** — assets, wishes, contacts always read and written together → embed, don't reference.
- **Log embedded** — bounded at 20 entries in frontend JS, always read with vault → safe to embed.
- **Revisit log schema when:** building an audit trail, admin dashboard, or unlimited history feature. At that point, move logs to a separate `logs` collection with `userId`, `event`, `detail`, `timestamp` fields.
- **Backward compatibility** — old vault docs (pre-F41) stored content in a `vault` blob field. All fallback lookups use explicit `None` checks (not `or`) to handle empty arrays correctly.

### MongoDB users collection
```
_id, username, password (bcrypt hash), name, ageGroup, hasWill,
notes, isTester, createdAt, lastLogin
```
**Tester accounts:** tester_01 through tester_06. All passwords updated to bcrypt hash of `Benny#07`.

---

## Test Suite

**File:** `identity-service/test_main.py`
**Run:** `python3 -m pytest test_main.py -v`
**Expected:** 69 passed

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

### Frontend test coverage
F44 (explainer card) and other frontend features are not covered by the pytest suite — pytest only covers the Python backend. Frontend test coverage requires a browser automation tool (e.g. Playwright). This is tracked as a future infrastructure task. See F58 in the backlog.

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
- `API` constant in JS points to `https://emergency-exit-production.up.railway.app`
- Login token stored in `sessionStorage` (not localStorage — clears on tab close)
- Vault sync is silent — never show errors to the user if sync fails
- **F41 fallback pattern:** always use explicit `None` check when reading from content sub-document: `kin = doc.get("content", {}).get("kin"); contacts = kin if kin is not None else doc.get("vault", {}).get("kin", [])`

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

---

## Feature Backlog — User Stories

Features are prioritised using MoSCoW: **Must**, **Should**, **Could**, **Won't**

Status key: `idea` → `specified` → `in-progress` → `done`

### Must Have — Core Product Loop

| ID | User Story | Priority | Status | Notes |
|----|-----------|----------|--------|-------|
| F01 | Automatically notify contacts if check-in missed and grace period expires | Must | done | Client-side simulation done. Real email delivery live (F39-2, F39-3). All-clear email on recovery live (F39-8). PDF attachment live (F39-4). Notification queue modal updated to reflect real delivery. |
| F02 | Self-contained PDF package for contacts | Must | done | 6-page A4 PDF, generated client-side via jsPDF. |
| F03 | Personal letter for each contact included in notification | Must | done | Letter stored as `k.letter`. Status pill on contact card. |
| F04 | Data encrypted at rest and in transit | Must | idea | Prototype uses plain localStorage. Production needs AES-256. |
| F05 | Reminders when check-in is due | Must | done | Amber banner + pulse card amber state. Push/email/SMS requires backend. |
| F40 | User authentication for testing | Must | done | Login wall added. Username + password. JWT token in sessionStorage. |

### Should Have

| ID | User Story | Priority | Status | Notes |
|----|-----------|----------|--------|-------|
| F06 | Dry run notification test | Should | idea | |
| F07 | Guided onboarding flow | Should | idea | |
| F08 | Export/backup vault data | Should | idea | |
| F09 | Auto-generated action checklist in PDF | Should | done | Page 2 of PDF. |
| F10 | Digital accounts section | Should | idea | |
| F11 | Clear explanation for contacts receiving notification | Should | idea | |
| F19 | Passive liveness detection via phone activity | Should | idea | Requires native mobile APIs. |
| F20 | Minimum information capture design | Should | idea | |
| F21 | Document location recording (not upload) | Should | idea | Partially done via suppDocs location field. |
| F41 | Migrate vault data from localStorage to MongoDB | Should | done | Server-first load implemented. GET /vault returns vault on login. localStorage kept as offline cache. Structured MongoDB schema with indexes. 69 automated tests. |

### Could Have

| ID | User Story | Priority | Status | Notes |
|----|-----------|----------|--------|-------|
| F12 | Physical Emergency Access Card (QR code) | Could | idea | |
| F13 | Advance Care Directive | Could | idea | |
| F14 | Periodic vault review reminders | Could | idea | |
| F15 | Backup check-in method | Could | idea | |
| F16 | Video/audio messages for contacts | Could | idea | |
| F17 | "What my contacts will receive" preview | Could | done | Delivered as part of F02. |
| F18 | Vault editor delegate access | Could | idea | |

### UX Improvements

| ID | User Story | Priority | Status | Notes |
|----|-----------|----------|--------|-------|
| F22 | Consistent primary CTA pattern | Must | done | |
| F23 | Encouraging empty states | Must | done | |
| F24 | Calm, grounded language | Must | done | |
| F25 | Edit assets and contacts | Must | done | |
| F26 | Consistent add-action button pattern | Should | done | |
| F27 | Card background visual rule | Should | done | |
| F28 | Consistent section header layout | Should | done | |
| F29 | Contact email/phone visible on card | Should | done | |
| F30 | Pulse de-emphasis before vault is set up | Should | done | |
| F31 | Privacy note on Home screen | Should | done | |
| F32 | Statement of Wishes plain-language explanation | Should | done | |
| F33 | Check-in label "Tap to check in" | Should | done | |
| F34 | Standardised delete button | Could | done | |
| F35 | Settings auto-save | Could | done | |
| F36 | Warm toast messages | Could | done | |
| F37 | Screen subtitle visual hierarchy | Could | done | |
| F38 | Remove Access Level dropdown from contact form | Must | done | |
| F42 | Palette redesign — linen cream + warm sage | Should | done | Navy/teal replaced. See CSS variable reference above. |
| F43 | CI/CD — automated pytest on every push | Should | done | GitHub Actions runs 69 tests + frontend sync check. Blocks deploy on failure. |
| F44 | First-run explainer card — core mechanic | Must | done | F44-1 (card + HTML), F44-2 (ee_onboarded persistence), F44-3 (copy refinement) all done. Card shows once on first login between privacy note and pulse card. Smooth fade-out on dismiss. ee_onboarded stored as separate localStorage key (not inside ee_v3). F44 frontend test coverage deferred — see F58. |
| F45 | Fix Home hero text for empty/incomplete state | Must | done | "Everything is in order" is misleading when completeness is 0%. Show "Let's get you set up" below ~30% completeness. Only show "Everything is in order" above 70%. Middle state (30–70%): "You're making progress." |
| F46 | Contacts screen — explain what contacts receive | Must | done | Add one-line explainer above the contact list: "These people will receive a full package of your information — assets, wishes, and any personal letters — if your check-in is overdue." |
| F47 | Fix privacy note — no longer device-only | Must | done | Current copy says "stored on this device only" which is false since F41 (data now syncs to Railway/MongoDB). Update to: "Your information is encrypted and stored securely in the cloud." |
| F48 | Pulse card first-visit explainer | Should | backlog | On first visit (before first check-in), show subtitle under the pulse card: "Check in regularly to confirm you're okay. If you stop, your contacts will be notified." Hide after first check-in. |
| F49 | Rewrite Notification Protocol labels in plain English | Should | backlog | Replace: "Ping me first, then notify contacts" → "Warn me first (3 reminders, then notify contacts)". "Escalate gradually" → "Notify contacts one at a time, 24 hours apart". Add note clarifying how the user receives the warning. |
| F50 | Overdue banner — add cancellation reassurance | Should | backlog | Add one calm line to overdue banner: "Don't worry — checking in now will immediately cancel any notifications." Reduces false-alarm anxiety. |
| F51 | First check-in milestone confirmation | Should | backlog | On the very first check-in only, show richer confirmation: "You're all set. Emergency Exit is now active. We'll remind you to check in again in [X weeks/months]." Use localStorage flag `ee_first_checkin_done` to detect. |
| F52 | Promote personal letter feature on contact card | Should | backlog | Move "Write personal letter" button higher on contact card — directly below the contact name. Reframe: "Write [Name] a personal message — it'll be the first thing they read." |
| F53 | Rename "Asset Ledger" to "My Assets" | Could | backlog | "Asset Ledger" is jargon. Update screen title, nav label, and all references. Nav label changes from "Ledger" to "Assets". |
| F54 | Rename "New Instruction" CTA to "Add a Wish" | Could | backlog | The Wishes screen CTA says "New Instruction" which is cold and clinical. Change to "Add a Wish". |
| F55 | Label or hide unbuilt WhatsApp notify option | Could | backlog | WhatsApp delivery (F39-9) is not built. Either hide from the dropdown or add "(coming soon)" label so testers don't expect it to work. |
| F56 | Change grace period default from 3 to 7 days | Could | backlog | 3 days is very short — a user could be in hospital or travelling. Default to 7. Add helper text recommending at least 7 days. |
| F57 | Remove "tester" language from login screen | Could | backlog | "Sign in with your tester credentials" is internal language. Change to "Sign in to your account" so testers experience realistic copy. |
| F58 | Frontend test coverage infrastructure | Should | backlog | F44 and other frontend features have no automated test coverage. pytest only covers the Python backend. Set up Playwright or similar browser automation tool to test: ee_onboarded flag behaviour, explainer card show/hide, dismissal persistence, and other client-side logic. Required before production launch. |

### Backend & Infrastructure

| ID | User Story | Priority | Status | Notes |
|----|-----------|----------|--------|-------|
| F39 | Server-side notification system | Must | in-progress | See sub-tasks below. |
| F39-1 | Vault sync endpoint + frontend sync on save() | Must | done | POST /vault/sync and POST /checkin live on Railway. |
| F39-2 | Pulse service — scheduled overdue scanner | Must | done | APScheduler runs hourly inside FastAPI. Detects overdue vaults, honours all 3 protocols. |
| F39-3 | Resend email delivery (plain text) | Must | done | Plain-text email sent via Resend to test inbox. From: onboarding@resend.dev. Swap to contact["email"] + verified domain for production. |
| F39-4 | Server-side PDF generation + email attachment | Must | done | ReportLab (open source, BSD, pure Python). PDF built in-memory via io.BytesIO. Attached as base64 via direct Resend REST API HTTP call. Bug fixed: loop variable `doc` renamed to `supp_doc` to avoid shadowing SimpleDocTemplate object. |
| F39-5 | Twilio SMS delivery | Should | specified | SMS with PDF link (not attachment). Requires cloud storage for PDF hosting. |
| F39-6 | RabbitMQ event bus | Should | specified | Introduces retry resilience. Can skip initially — scanner calls worker directly. |
| F39-7 | Notification protocol logic server-side | Must | done | Delivered as part of F39-2. ping_then_notify / notify_immediately / escalate all implemented. |
| F39-8 | False alarm recovery — cancellation logic | Must | done | POST /checkin detects if overdueNotificationSent was True before resetting. If so, sends warm all-clear email to all contacts via send_allclear_email(). Returns allclear_sent: true in response. |
| F39-9 | WhatsApp delivery via Twilio | Could | idea | Requires Meta Business API approval. Defer until core delivery stable. |

### Won't Have

| ID | Feature | Why not |
|----|---------|---------|
| W01 | Legal Will creation | Liability risk. Emergency Exit records where your Will is, not replaces it. |
| W02 | Account closure automation | GoodTrust's territory. |
| W03 | AI-generated legacy stories | Evaheld's territory. |
| W04 | Cryptocurrency wallet/key storage | Extreme security risk. |

---

## End-of-Chat Checklist

- [ ] Download the new `main.py` (if changed)
- [ ] Download the new `test_main.py` (if changed)
- [ ] Download the new `CLAUDE.md`
- [ ] Did anything structural change? Update `CLAUDE.md`
- [ ] Replace `identity-service/main.py` in VS Code
- [ ] Replace `identity-service/test_main.py` in VS Code
- [ ] Add `reportlab` and `requests` to `identity-service/requirements.txt` if not already present
- [ ] Run `python3 -m pytest test_main.py -v` — confirm 69 passed before pushing
- [ ] `cp index.html frontend/index.html`
- [ ] `git add -A`
- [ ] `git commit -m "describe what changed"`
- [ ] `git push`
- [ ] GitHub Actions runs pytest + sync check ✅
- [ ] Railway redeploys backend automatically ✅
