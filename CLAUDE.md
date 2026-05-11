# Emergency Exit — Project Context for Claude

## How to start a new chat

This project is connected to the `RamenFanClub/emergency-exit` GitHub repo. `CLAUDE.md` and `index.html` are synced at the project level, so Claude can read them automatically at the start of every chat — no need to upload files manually.

To start a new chat:
1. Open a new chat inside this Claude Project
2. Simply describe what you want to change or build
3. Claude will read the latest `CLAUDE.md` and `index.html` from the GitHub sync

**Important:** Always push your changes to `main` after each session. The GitHub sync reflects whatever is on `main` — if you don't push, the next chat will be working from outdated files.

---

## What is this project?

**Emergency Exit** is a personal digital legacy vault app that helps users prepare for sudden death by recording assets, documenting final wishes, storing Will and legal document details, monitoring liveness via periodic check-ins, and automatically notifying nominated contacts if the user stops responding.

Target market: everyday Australians (non-technical users).

Planned platforms:
- **iOS** (Apple App Store)
- **Android** (Google Play Store)
- **Web** (mobile-optimised browser, currently deployed via GitHub Pages and Google Cloud VM)

---

## Repository & Deployment

- **GitHub:** `https://github.com/RamenFanClub/emergency-exit`
- **GitHub Pages:** `https://ramenfanclub.github.io/emergency-exit` (public prototype, no login)
- **Testing URL:** `http://34.40.251.204` (login-walled, connects to real backend)
- **Deployment:** Push to `main` branch → GitHub Pages serves from `/ (root)` → live in ~60 seconds
- **Key files:**
  - `index.html` — the entire frontend app (root, served by GitHub Pages and Caddy on VM)
  - `frontend/index.html` — duplicate kept in sync with root
  - `CLAUDE.md` — this file
  - `docs/` — Technical Blueprint and Feature Register
  - `identity-service/` — Python FastAPI auth service (live on VM)
  - `*-service/` — other backend microservice skeletons (not yet implemented)

---

## Deploy Workflow

**Making a change and deploying it:**

1. Edit `index.html` locally on your Mac
2. Also update `frontend/index.html` (keep in sync)
3. Push to GitHub:
```bash
cd ~/Github/emergency-exit
git add index.html frontend/index.html CLAUDE.md
git commit -m "describe what changed"
git push
```
4. Pull onto the VM:
```bash
# SSH into VM first
gcloud compute ssh emergency-exit-server --zone=australia-southeast1-a

# Then pull and deploy
cd ~/Github/emergency-exit  # if repo is cloned on VM
# OR copy directly:
curl -o /var/www/html/index.html https://raw.githubusercontent.com/RamenFanClub/emergency-exit/main/index.html
```

**⚠️ IP address warning:** The VM's external IP has changed once already (was `34.40.222.5`, now `34.40.251.204`). If the app stops working, SSH into the VM and run `curl ifconfig.me` to get the current IP, then update: (1) `API` constant in both `index.html` files, (2) MongoDB Atlas Network Access whitelist, (3) this CLAUDE.md file.

**Note:** The repo is private so curl from GitHub won't work without a token. Use `gcloud compute scp` instead:
```bash
# From Cloud Shell (not inside VM):
gcloud compute scp ~/index_updated.html emergency-exit-server:/var/www/html/index.html --zone=australia-southeast1-a
```

---

## Infrastructure — Google Cloud VM

### VM Details
- **Provider:** Google Cloud Compute Engine
- **Project:** My First Project (`moonlit-helper-426004-d2`)
- **Instance name:** `emergency-exit-server`
- **Zone:** `australia-southeast1-a` (Sydney)
- **Machine type:** `e2-micro` (free tier)
- **OS:** Ubuntu 22.04 LTS
- **External IP:** `34.40.251.204` (note: this IP has changed once already — verify with `curl ifconfig.me` on the VM if the service stops responding)
- **Internal IP:** `10.152.0.2`

### Accessing the VM
```bash
# From Google Cloud Console → Cloud Shell:
gcloud compute ssh emergency-exit-server --zone=australia-southeast1-a
```

### Services Running on VM
| Service | Port | Status | Managed by |
|---------|------|--------|------------|
| Caddy (web server) | 80 | Running | systemd |
| identity-service (FastAPI) | 8001 | Running | systemd |

### Firewall Rules Open
| Rule name | Port | Purpose |
|-----------|------|---------|
| allow-http | 80 | Serve frontend |
| allow-https | 443 | Future HTTPS |
| allow-identity-service | 8001 | FastAPI backend |

### Web Server: Caddy
- Serves `index.html` from `/var/www/html/`
- Proxies `/auth/*`, `/admin/*`, `/health` to FastAPI on port 8001
- Config file: `/etc/caddy/Caddyfile`
- Nginx is installed but disabled — Caddy took port 80 first, kept it

### Restarting services after changes:
```bash
sudo systemctl restart caddy
sudo systemctl restart identity-service
sudo systemctl status identity-service
```

---

## Identity Service — Python FastAPI

### ⚠️ Important — two versions of the identity service exist

- **On the VM** (`~/emergency-exit/identity-service/main.py`) — this is the REAL working version. All edits happen here.
- **In the GitHub repo** (`identity-service/app/`) — this is an older skeleton that is NOT what runs on the VM. Ignore it for now.

When editing the backend, always SSH into the VM and edit the file there directly.

### Location on VM
```
/home/anggita_bayu_gmail_com/emergency-exit/identity-service/
├── main.py              ← FastAPI server
├── requirements.txt     ← Python dependencies
├── .env                 ← MongoDB connection string (never commit this)
├── venv/                ← Python virtual environment
└── scripts/
    └── provision.py     ← Creates tester accounts in bulk
```

### Running manually (if systemd service stops):
```bash
cd ~/emergency-exit/identity-service
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8001
```

### API Endpoints
| Method | Endpoint | Auth required | Purpose |
|--------|----------|---------------|---------|
| GET | `/health` | No | Confirm server is running |
| POST | `/auth/login` | No | Login with username + password, returns JWT token |
| GET | `/auth/me` | Yes | Get current user's profile |
| GET | `/admin/testers` | Yes | List all tester accounts |
| POST | `/vault/sync` | Yes | Store full ee_v3 vault blob in MongoDB (F39) |
| POST | `/checkin` | Yes | Record check-in server-side, clear overdue flag (F39) |

### Environment Variables (.env)
```
MONGO_URI=mongodb+srv://dba_emex:PASSWORD@emergency-exit.zygepvl.mongodb.net/emergency_exit?retryWrites=true&w=majority&appName=emergency-exit
JWT_SECRET=emergency_exit_super_secret_key_2024
PORT=8001
```
**Never commit the .env file.** It is in .gitignore.

### Re-provisioning testers:
```bash
cd ~/emergency-exit/identity-service
source venv/bin/activate
python scripts/provision.py
```
Passwords are printed once and cannot be recovered — copy them immediately.

---

## Database — MongoDB Atlas on Google Cloud

- **Provider:** MongoDB Atlas (hosted on Google Cloud)
- **Cluster name:** `emergency-exit`
- **Cluster URL:** `emergency-exit.zygepvl.mongodb.net`
- **Database name:** `emergency_exit`
- **Username:** `dba_emex`
- **Collections:**
  - `users` — tester accounts (username, hashed password, name, ageGroup, hasWill, notes, isTester, createdAt, lastLogin)
  - `vaults` — synced vault blobs per user (userId, vault, syncedAt, lastCheckin, checkInFrequency, checkInUnit, gracePeriodDays, notifyProto, contactCount, overdueNotificationSent)

### Checking tester accounts in Atlas:
Go to [cloud.mongodb.com](https://cloud.mongodb.com) → Browse Collections → `emergency_exit` → `users`

---

## Tester Accounts

- **6 tester accounts** created via `provision.py`
- Usernames: `tester_01` through `tester_06`
- Passwords: auto-generated at provisioning time — stored only with you
- All accounts have `isTester: true` flag for easy identification
- **No email required** — username + password only
- Production will use email + password or Google login

### Tester profile fields:
| Field | Purpose |
|-------|---------|
| username | Login credential |
| password | Bcrypt hashed — never stored plain |
| name | Display name |
| ageGroup | For research analysis |
| hasWill | For research segmentation |
| notes | Researcher context |
| isTester | Flag to identify test accounts |
| lastLogin | Track engagement |

---

## Login Wall (Frontend)

The `index.html` served at `http://34.40.222.5` includes a login wall:

- **Login screen** appears before anything else
- **Token** stored in `sessionStorage` (clears when browser tab closes)
- **"Sign out"** button replaces the avatar circle in the header
- **"Hi, [Name]"** greeting shown after login
- **API calls** point to `http://34.40.222.5:8001`
- **Enter key** submits the login form
- **Session persistence** — if token exists in sessionStorage, login wall is skipped

### Key element IDs added for login:
- `#login-wall` — the full-screen login overlay
- `#li-user` — username input
- `#li-pass` — password input
- `#login-err` — error message display
- `#logout-btn` — sign out button in header
- `#user-greeting` — "Hi, [Name]" in header

### Key functions added:
- `doLogin()` — POSTs to `/auth/login`, stores token, shows app
- `doLogout()` — clears session, resets state, shows login wall
- `showApp(user)` — reveals the app after successful login
- `API` constant — set to `http://34.40.222.5:8001`

---

## App Name & Branding

- **App name:** Emergency Exit
- **Design system:** "The Eternal Archive / Aeterna Solid"
- **Tone:** Calm, trustworthy, premium — a "Digital Sanctuary"
- **Primary colour:** `#002147` (deep navy)
- **Accent colour:** `#2d8a7a` (teal)
- **Amber colour:** `#c47a20` (amber — used for F05 "due soon" reminders)
- **Gradient:** `linear-gradient(135deg, #002147, #003366)`
- **Fonts:** Manrope (headlines, 800 weight) + Public Sans (body)
- **Design rules:**
  - No 1px borders for sectioning — use background colour shifts
  - Minimum tap target: 48×48dp
  - Rounded cards (`border-radius: 16px`)
  - Ambient shadows only
  - No pure black — use `#002147` for dark tones

---

## Tech Stack

### Current (user testing)
- Single-file HTML/CSS/JavaScript (`index.html`)
- **Login wall** added for user testing — username + password, no email required
- **sessionStorage** for auth token (clears on tab close)
- **localStorage** still used for vault data (will migrate to MongoDB in next phase)
- **Vault sync** (F39 Step 1): every `save()` call silently POSTs vault to `/vault/sync` — server now has a copy
- jsPDF (via CDN) for client-side PDF generation
- **Frontend:** Caddy serving from `/var/www/html/` on Google Cloud VM
- **Backend:** Python FastAPI (`identity-service`) on same VM
- **Database:** MongoDB Atlas on Google Cloud

### Planned (production)
- **Frontend:** React Native (iOS + Android) + React (web)
- **Backend:** Python FastAPI microservices
- **Database:** MongoDB (one DB per service)
- **Message broker:** RabbitMQ (domain events between services)
- **Notifications:** Email (SendGrid), SMS (Twilio), WhatsApp, push
- **Auth:** Biometric (Face ID / Touch ID), PIN, JWT + MFA
- **Login:** Email + password OR Google login (replacing username-only testing auth)

---

## App Structure — 5 Screens

### 1. Home (Dashboard)
- Hero headline: "Everything is in order." (normal) / "Action needed." (overdue — F01)
- Asset + wish count summary with status badge: Active (teal), Due Soon (amber — F05), or Overdue (red — F01)
- **Privacy note** (F31): Small teal lock icon line below summary — "Your information is stored on this device only — it never leaves your phone." Update this copy when backend cloud storage launches.
- **F05: Reminder banner** — amber gradient card shown above Vitality Pulse when a check-in is approaching but NOT yet overdue. Contains:
  - Clock icon + "Check-in due soon" title + contextual subtitle (adapts: "due today" / "due tomorrow" / "X days until your next check-in")
  - Detail text explaining what happens if the check-in is missed (grace period begins)
  - Single button: "Check in now" (white, performs check-in)
  - Only shows when: user has checked in before, check-in is within the reminder threshold, and NOT overdue
  - Reminder threshold: the greater of 7 days or 25% of the total interval
- **F01: Overdue alert banner** — red gradient card shown above Vitality Pulse when a check-in is overdue and grace period has expired. Contains:
  - Warning icon + "Check-in overdue" title + grace period expiry info
  - Protocol-specific detail text (adapts to ping_then_notify / notify_immediately / escalate)
  - Two buttons: "I'm okay — check in" (white, dismisses overdue) and "View queue" (opens notification queue modal)
  - Gentle pulse animation on the card shadow to draw attention without being alarming
  - Only shows when contacts exist (no point alerting about notifications with no contacts)
- **Vitality Pulse** — animated pulsing heart, tap to confirm "Alive & well"
  - Normal state: teal icon and text
  - Due soon state (F05): amber icon, title "Check-in due soon", countdown in amber, pulse ring tinted amber via `.pulse-due-soon` CSS class
  - Overdue state (F01): red `heart_broken` icon, "Check-in overdue" title, "X days overdue" in red
  - Fades to 45% opacity and scales down when completeness < 40% (F30) — but NOT when overdue (F01) or due soon (F05) — pulse is critical in those states
  - Completeness card gets teal highlight ring when pulse is dimmed (F30)
  - Contextual hint appears inside pulse card when dimmed (F30)
- **F01: Notification queue card** — shown on Home only when overdue. Compact card with red accent showing first 3 queued contacts, their delivery method, and queue status (Queued/Waiting). "Details" button opens full queue modal. "Generate all packages (dry run)" button downloads PDFs for all contacts.
- Next check-in date + days remaining countdown (combined with Vitality Pulse in one card)
- Last confirmed timestamp shown below pulse
- Asset Ledger CTA (full-width gradient button)
- Compact pill buttons: **Add Asset** (navy) + **My Wishes** (grey-blue)
- Home completeness % with progress bar + actionable tips (7 checks)
- Recent activity log
- Screen ID: `s-home` | Nav ID: `n-home`

### 2. Ledger (Asset Register)
- Record New Asset CTA (gradient button)
- Assets grouped by category with icons
- Categories (11): Bank account, Property, Investment, Superannuation, Life insurance, Vehicle, Cryptocurrency, Business, Personal item, Digital account, Other
- Per asset: name, category, estimated value, details (free-text), beneficiary, notes
- Delete button per asset (`.del-btn` — F34)
- Legacy completeness % shown in info box

### 3. My Wishes
- New Instruction CTA (dark gradient card with START NOW button)
- **Will & Legal Documents** section (always at top):
  - Will status badge: Not recorded / Draft / Signed & witnessed
  - Will details: status, date signed, solicitor, primary location, secondary location, notes
  - Supplementary Documents list with delete per item (`.del-btn` — F34)
  - Attach document button (`.ob` style in section header)
- **Statement of Wishes prompt** (orange accent card — prominent nudge):
  - Shows when Statement of Wishes has NOT been recorded
  - Includes plain-language info box explaining SOW vs Will (F32)
  - Transforms to green confirmation card when recorded, showing storage location
  - Tapping opens Attach Document modal with "Statement of Wishes" pre-selected
- **Wishes grouped by category** (like Ledger)
- Wish categories (9): Funeral & Service, Medical / end of life care, Guardian for children, Pet care, Business succession, Digital accounts, Personal message, Charitable giving, Other
- Wish form fields: Category, Wish, Details, Priority

### 4. Contact
- Title: "Contacts", subtitle: "People to notify and how to reach them"
- Section: "My Contacts" with Add Contact button (`.ob` style in section header)
- Contact cards showing initials avatar, full name, relationship, letter status pill, notify method, sequence number with reorder arrows, delete button, write letter button, preview PDF button
- Add Contact modal fields: First name, Last name, Relationship, Email, Phone, Notify via
- **All contacts receive the full package — there are no access level tiers**

### 5. Settings
- **Check-in Frequency** — stepper (1–24 Weeks or Months)
- **Grace Period** — stepper in days (1–30 days, default 3)
- **Notification Protocol** — 3 radio options
- **Verification** — FaceID/Biometrics or Secure Passcode
- All changes auto-save immediately with toast feedback (F35)

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

Active tab: white icon/label on navy pill. Inactive: navy at 35% opacity.

---

## Key UI Patterns

- **Modals:** Bottom-sheet, drag handle at top, tap outside to dismiss
- **Gradient buttons:** Full-width, 18px padding, 3px border-bottom for depth, active scale(0.97)
- **Compact action buttons:** Pill-shaped, side by side, 10px vertical padding
- **Cards:** `border-radius: 16px`, `#f2f4f6` or white with ambient shadow
- **Card background rule (F27):** `.cardw` (white + shadow) = interactive or primary sections. `.card` (grey) = informational or contextual grouping containers.
- **Secondary add-action buttons (F26):** All "add" actions in section headers use `.ob` style
- **Tags/badges:** Uppercase, 10px, pill-shaped
- **Toast notifications:** Fixed, centred, navy pill, auto-dismiss 2.4s. All toasts include ✓
- **Delete button (F34):** All delete actions use `.del-btn` CSS class
- **Settings auto-save (F35):** All Settings controls save immediately on change with toast feedback

---

## Data Model

```javascript
// localStorage key: 'ee_v3'
// NOTE: vault data still in localStorage for now — migration to MongoDB planned
// F39 Step 1: vault is also synced to MongoDB `vaults` collection on every save()
{
  assets: [{
    id: timestamp, name: string, category: string,
    value: number, details: string, beneficiary: string, notes: string
  }],
  wishes: [{
    id: timestamp, category: string, title: string,
    details: string, priority: 'high' | 'medium' | 'low'
  }],
  will: {
    status: 'signed' | 'draft' | 'none',
    date: string, solicitor: string,
    loc1: string, loc2: string, notes: string
  } | null,
  suppDocs: [{
    id: timestamp,
    type: 'Statement of Wishes' | 'Enduring Power of Attorney' |
          'Advance Health Directive' | 'Guardianship Nomination' |
          'Funeral Pre-arrangement' | 'Business Succession Plan' | 'Other',
    name: string, loc: string, notes: string
  }],
  kin: [{
    id: timestamp, first: string, last: string, rel: string,
    email: string, phone: string,
    notifyVia: 'email' | 'sms' | 'whatsapp' | 'email_and_sms',
    order: number,
    letter: string
  }],
  lastCheckin: timestamp | null,
  fc: number,
  fu: 'weeks' | 'months',
  gp: number,
  v: 'face' | 'pin',
  notifyProto: 'ping_then_notify' | 'notify_immediately' | 'escalate',
  log: [{ msg: string, time: string }],
  saveCount: number
}
```

### MongoDB `vaults` collection schema (F39)
```
{
  userId: ObjectId,         // links to users collection
  vault: object,            // full ee_v3 blob
  syncedAt: Date,           // last sync timestamp
  lastCheckin: number,      // ms timestamp — top-level for pulse service queries
  checkInFrequency: number, // fc value
  checkInUnit: string,      // 'weeks' | 'months'
  gracePeriodDays: number,  // gp value
  notifyProto: string,      // notification protocol
  contactCount: number,     // number of contacts
  overdueNotificationSent: boolean  // set true when notifications fire, false on check-in
}
```

---

## Completeness Score — 7 Checks (~14.3% each)

1. At least one asset recorded
2. At least one asset has a beneficiary assigned
3. At least one wish recorded
4. Will details recorded
5. Statement of Wishes recorded
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
- Home screen uses `id="s-home"` and nav uses `id="n-home"`
- When editing index.html, always update BOTH `./index.html` AND `./frontend/index.html`
- jsPDF loaded via CDN in `<head>`
- `API` constant in JS points to `http://34.40.251.204:8001`
- Login token stored in `sessionStorage` (not localStorage — clears on tab close)
- Vault sync is silent — never show errors to the user if sync fails

---

## What NOT to Do

- Do not use Inter, Roboto, or Arial — always Manrope + Public Sans
- Do not use pure black — use `#002147`
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

---

## Feature Backlog — User Stories

Features are prioritised using MoSCoW: **Must**, **Should**, **Could**, **Won't**

Status key: `idea` → `specified` → `in-progress` → `done`

### Must Have — Core Product Loop

| ID | User Story | Priority | Status | Notes |
|----|-----------|----------|--------|-------|
| F01 | Automatically notify contacts if check-in missed and grace period expires | Must | in-progress | Client-side simulation done. Backend delivery pending F39 sub-tasks. |
| F02 | Self-contained PDF package for contacts | Must | done | 6-page A4 PDF, generated client-side via jsPDF. |
| F03 | Personal letter for each contact included in notification | Must | done | Letter stored as `k.letter`. Status pill on contact card. |
| F04 | Data encrypted at rest and in transit | Must | idea | Prototype uses plain localStorage. Production needs AES-256. |
| F05 | Reminders when check-in is due | Must | done | Amber banner + pulse card amber state. Push/email/SMS requires backend. |
| F40 | User authentication for testing | Must | done | Login wall added. Username + password. JWT token in sessionStorage. 6 tester accounts provisioned. |

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
| F41 | Migrate vault data from localStorage to MongoDB | Should | in-progress | Step 1 done via F39 vault sync. Full migration pending. |

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

### Backend & Infrastructure

| ID | User Story | Priority | Status | Notes |
|----|-----------|----------|--------|-------|
| F39 | Server-side notification system | Must | in-progress | See sub-tasks below. |
| F39-1 | Vault sync endpoint + frontend sync on save() | Must | done | POST /vault/sync and POST /checkin live on VM. save() and checkin() call them silently. IP updated to 34.40.251.204. MongoDB Atlas whitelist updated. |
| F39-2 | Pulse service — scheduled overdue scanner | Must | specified | Runs hourly. Queries vaults collection for overdue users. Publishes user.overdue event. |
| F39-3 | SendGrid email delivery (plain text first) | Must | specified | Notification worker sends email to each contact. No PDF attachment yet. |
| F39-4 | Server-side PDF generation | Must | specified | Python WeasyPrint or ReportLab. Attaches PDF to email. |
| F39-5 | Twilio SMS delivery | Should | specified | SMS with PDF link (not attachment). Requires GCS for PDF hosting. |
| F39-6 | RabbitMQ event bus | Should | specified | Introduces retry resilience. Can skip initially and have pulse call worker directly. |
| F39-7 | Notification protocol logic server-side | Must | specified | Honour ping_then_notify / notify_immediately / escalate on the server. |
| F39-8 | False alarm recovery — cancellation logic | Must | specified | user.checked_in event cancels queued notifications. overdueNotificationSent flag. |
| F39-9 | WhatsApp delivery via Twilio | Could | idea | Requires Meta Business API approval. Defer until core delivery is stable. |
| F40 | Identity service + tester provisioning | Must | done | Python FastAPI on Google Cloud VM. MongoDB Atlas. 6 testers created. |

### Bug Fix

| ID | User Story | Priority | Status | Notes |
|----|-----------|----------|--------|-------|
| F38 | Remove Access Level dropdown from contact form | Must | done | |

### Won't Have

| ID | Feature | Why not |
|----|---------|---------|
| W01 | Legal Will creation | Liability risk. Emergency Exit records where your Will is, not replaces it. |
| W02 | Account closure automation | GoodTrust's territory. |
| W03 | AI-generated legacy stories | Evaheld's territory. |
| W04 | Cryptocurrency wallet/key storage | Extreme security risk. |

---

## End-of-Chat Checklist

- [ ] Download the new `index.html`
- [ ] Download the new `CLAUDE.md`
- [ ] Did anything structural change? Update `CLAUDE.md`
- [ ] Replace files in VS Code (`./index.html` AND `./frontend/index.html`)
- [ ] If the VM IP has changed: update `API` constant in both index.html files, update MongoDB Atlas whitelist, update this CLAUDE.md
- [ ] Apply backend patch: add vault sync routes to `identity-service/main.py` ON THE VM (not the local repo)
- [ ] `git add -A`
- [ ] `git commit -m "describe what changed"`
- [ ] `git push`
- [ ] SSH into VM, pull latest, restart identity-service:
  ```bash
  sudo systemctl restart identity-service
  sudo systemctl status identity-service
  ```
