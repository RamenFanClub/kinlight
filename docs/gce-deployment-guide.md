# Kinlight — GCE Free Tier Deployment Guide

> **Goal:** Migrate the FastAPI backend from Railway (`emergency-exit-production.up.railway.app`)
> to Google Compute Engine free tier at `https://api.kinlight.app`.
>
> **Cost:** $0/month permanently (e2-micro VM, 10 GB disk, ephemeral IP).
>
> **Prerequisites:** GCP account, Cloudflare DNS for `kinlight.app`, Railway env vars handy.

---

## Icon key

| Icon | Location |
|------|----------|
| 🌐 | Cloud Console / Web (Cloudflare, GitHub, GCP) |
| 🖥️ | Your local machine (terminal in the kinlight repo) |
| ☁️ | GCE VM via SSH |

---

## Architecture

```
User (AU)
  │
  ▼
https://kinlight.app  ───  GitHub Pages (frontend, unchanged)
  │                           CSP connect-src: api.kinlight.app
  ▼
https://api.kinlight.app ──  GCE e2-micro (us-west1, Oregon)
  │                           ├── nginx (TLS termination, reverse proxy)
  │                           ├── certbot (Let's Encrypt, auto-renew)
  │                           ├── DDNS updater (Cloudflare API, every 60s)
  │                           └── Docker: python:3.11-slim
  │                                 └── uvicorn main:app :8001
  │                                       └── APScheduler (hourly pulse)
  ▼
MongoDB Atlas (asia-southeast2, Jakarta)
  ~180ms latency from Oregon — negligible for a single-user API
```

---

## Step 1 — Create the VM

🌐 **GCP Console** → Compute Engine → VM Instances → Create Instance

| Field | Value |
|-------|-------|
| Name | `kinlight-api` |
| Region | `us-west1 (Oregon)` |
| Zone | Any |
| Series | E2 |
| Machine type | `e2-micro` (0.25 vCPU, 1 GB) |
| Boot disk | Ubuntu 26.04 LTS Minimal, Balanced persistent, **10 GB** |
| Firewall | ✅ Allow HTTP traffic, ✅ Allow HTTPS traffic |
| External IP | Ephemeral (default — do not reserve a static one) |

Click **Create**. After ~60 seconds the VM boots. Note the **External IP** from the VM list — you'll need it for Steps 2 and 3.

---

## Step 2 — DNS

🌐 **Cloudflare Dashboard** → `kinlight.app` → DNS → Records → Add Record

| Field | Value |
|-------|-------|
| Type | A |
| Name | `api` |
| IPv4 | `<ephemeral-IP-from-step-1>` |
| TTL | 120 (2 minutes) |
| Proxy status | **DNS only** (grey cloud) |

---

## Step 3 — SSH into the VM

🖥️ **Your local terminal:**

```bash
ssh <username>@<ephemeral-IP>
```

> **Which username?** If GCE uses OS Login (default on newer VMs), your username is the email prefix before `@gmail.com`. Otherwise it's the username you set during VM creation. If `Permission denied`, go to 🌐 GCP Console → your VM → SSH dropdown → "View gcloud command" for the correct login string.

---

## Step 4 — Install packages

☁️ **On the VM:**

```bash
sudo apt update && sudo apt upgrade -y

sudo apt install -y nginx certbot python3-certbot-nginx git docker.io

sudo usermod -aG docker $USER
```

> **Important:** Now log out and back in for the group change to take effect:
> ```bash
> exit
> ```
> Then:
> ```bash
> ssh <username>@<ephemeral-IP>
> ```
> Docker will work without `sudo` after that.

---

## Step 5 — Clone and build the app

☁️ **On the VM:**

```bash
git clone https://github.com/ramenfanclub/emergency-exit.git ~/kinlight
cd ~/kinlight/identity-service
docker build -t kinlight-api .
```

> This takes ~2 minutes on the e2-micro (0.25 vCPU). Let it finish.

---

## Step 6 — Run the container

☁️ **On the VM:**

```bash
docker run -d --restart=unless-stopped --name kinlight-app \
  -p 127.0.0.1:8001:8001 \
  -e PORT=8001 \
  -e GCP_PROJECT_ID='<your-gcp-project-id>' \
  kinlight-api
```

> Secrets are no longer passed via `-e`. The app fetches them from GCP Secret Manager at startup using the VM's service account (see "Secret Manager setup (F121)" below).

Verify:

```bash
docker logs kinlight-app
```

Should show uvicorn startup and APScheduler initializing. Then:

```bash
curl -s http://127.0.0.1:8001/health | python3 -m json.tool
```

Expected:
```json
{"status": "ok", "mongodb": true, "pulseScanner": {"lastRun": null, "healthy": false}}
```

---

## Step 7 — DDNS (Cloudflare auto-updater)

Since the IP is ephemeral, it could change on VM stop/start. This script runs every 60 seconds and updates the DNS record if the IP changed.

### 7a. Create the Cloudflare API token

🌐 **Cloudflare Dashboard** → Profile (top-right icon) → API Tokens → Create Token

- **Template:** "Edit zone DNS"
- **Zone Resources:** `Include` → `Specific zone` → `kinlight.app`
- Click "Continue to summary" → "Create Token"
- **Copy the token immediately** — Cloudflare shows it only once

> **Alternative — use your account's Global API Key.** If you'd rather not create a scoped token, use the account-wide Global API Key (Profile → API Tokens → Global API Key → **View**) with `X-Auth-Email` + `X-Auth-Key` headers. Simpler, but it grants **full account access**, so the scoped token is preferred. The 7b script below supports both.

### 7b. Install the DDNS script

☁️ **On the VM:**

Create the directory and a root-only credentials file:

```bash
sudo mkdir -p /opt/ddns
sudo tee /opt/ddns/cloudflare.env > /dev/null <<'EOF'
CF_EMAIL="<your-cloudflare-login-email>"
CF_KEY="<your-global-api-key>"
EOF
sudo chmod 600 /opt/ddns/cloudflare.env
```

> **Using a scoped token instead of the Global API Key?** In `cloudflare.env`, just set `CF_TOKEN="<your-token>"` and omit `CF_EMAIL`/`CF_KEY`. The script auto-detects the token and uses Bearer auth — no script edit needed.

Now create the script:

```bash
sudo nano /opt/ddns/cloudflare-ddns.sh
```

Paste the following (canonical copy is tracked in the repo at `identity-service/scripts/cloudflare-ddns.sh`):

```bash
#!/bin/bash
# Cloudflare DDNS updater — keeps api.kinlight.app pointed at this VM's ephemeral IP.
# Credentials are read from /opt/ddns/cloudflare.env (root-only, 0600).
#
# Auth: prefer a scoped "Edit zone DNS" token (CF_TOKEN). Falls back to the
# account-wide Global API Key (CF_EMAIL + CF_KEY) when no token is set.

set -u

ZONE="kinlight.app"
RECORD="api.$ZONE"
LOG="/var/log/ddns.log"
ENV_FILE="/opt/ddns/cloudflare.env"

if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  . "$ENV_FILE"
fi

# Resolve the public IPv4, trying a fallback chain of providers.
CURRENT_IP=""
for url in https://ifconfig.me https://api.ipify.org https://icanhazip.com; do
  CURRENT_IP=$(curl -sf4 --max-time 10 "$url") && break
done

if [ -z "$CURRENT_IP" ]; then
  echo "$(date): no public IP resolvable — aborting" >&2
  exit 1
fi

if [ -n "${CF_TOKEN:-}" ]; then
  AUTH=(-H "Authorization: Bearer $CF_TOKEN")
elif [ -n "${CF_EMAIL:-}" ] && [ -n "${CF_KEY:-}" ]; then
  AUTH=(-H "X-Auth-Email: $CF_EMAIL" -H "X-Auth-Key: $CF_KEY")
else
  echo "$(date): CF_TOKEN or (CF_EMAIL + CF_KEY) must be set in $ENV_FILE" >&2
  exit 1
fi

ZONE_ID=$(curl -sf "${AUTH[@]}" \
  "https://api.cloudflare.com/client/v4/zones?name=$ZONE" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(d['result'][0]['id'] if d.get('success') and d.get('result') else '')")

if [ -z "$ZONE_ID" ]; then
  echo "$(date): zone $ZONE not found — check your Cloudflare credentials" >&2
  exit 1
fi

RECORD_DATA=$(curl -sf "${AUTH[@]}" \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records?type=A&name=$RECORD")

RECORD_ID=$(echo "$RECORD_DATA" | python3 -c "import sys,json; d=json.load(sys.stdin); r=d.get('result') or []; print(r[0]['id'] if r else '')")
DNS_IP=$(echo "$RECORD_DATA" | python3 -c "import sys,json; d=json.load(sys.stdin); r=d.get('result') or []; print(r[0]['content'] if r else '')")

# Auto-create the A record if it's missing (e.g. lost during a zone restore).
if [ -z "$RECORD_ID" ]; then
  echo "$(date): record $RECORD missing — creating" >> "$LOG"
  curl -sf -X POST "${AUTH[@]}" \
    -H "Content-Type: application/json" \
    -d "{\"type\":\"A\",\"name\":\"$RECORD\",\"content\":\"$CURRENT_IP\",\"ttl\":120,\"proxied\":false}" \
    "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records" \
    && echo "$(date): created $RECORD → $CURRENT_IP" >> "$LOG"
  exit 0
fi

if [ "$CURRENT_IP" != "$DNS_IP" ]; then
  curl -sf -X PATCH "${AUTH[@]}" \
    -H "Content-Type: application/json" \
    -d "{\"content\":\"$CURRENT_IP\",\"name\":\"$RECORD\",\"type\":\"A\",\"ttl\":120}" \
    "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records/$RECORD_ID" \
    && echo "$(date): updated $RECORD → $CURRENT_IP" >> "$LOG"
fi
```

Make it executable and test manually:

```bash
sudo chmod +x /opt/ddns/cloudflare-ddns.sh
sudo /opt/ddns/cloudflare-ddns.sh && cat /var/log/ddns.log
```

> No output means the DNS record already matches the VM's IP (normal). An `updated`/`created` line means it corrected the record. An `IndexError`-style failure means the `api` A record is missing and auto-create didn't run — verify `CF_EMAIL`/`CF_KEY` first.

### 7c. Create the systemd timer

☁️ **On the VM:**

```bash
sudo nano /etc/systemd/system/ddns.service
```

```ini
[Unit]
Description=Cloudflare Dynamic DNS updater

[Service]
Type=oneshot
ExecStart=/opt/ddns/cloudflare-ddns.sh
```

```bash
sudo nano /etc/systemd/system/ddns.timer
```

```ini
[Unit]
Description=Run DDNS updater every minute

[Timer]
OnBootSec=30s
OnUnitActiveSec=60s

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ddns.timer
```

### 7d. Verify it works

```bash
sudo systemctl status ddns.timer
sudo journalctl -u ddns.service -f
# Press Ctrl+C after you see a successful run
```

---

## Step 8 — nginx reverse proxy

☁️ **On the VM:**

```bash
sudo nano /etc/nginx/sites-available/kinlight
```

Paste:

```nginx
server {
    listen 80;
    server_name api.kinlight.app;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
        client_max_body_size 2m;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/kinlight /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

---

## Step 9 — Pre-HTTPS verification

🖥️ **Your local terminal:**

Wait for DNS propagation (1–5 minutes). Then verify:

```bash
dig +short api.kinlight.app
# Must return your VM's ephemeral IP

curl -s http://api.kinlight.app/health | python3 -m json.tool
# Must return the same healthy response as Step 6
```

If `dig` doesn't return your IP yet, wait and retry. The 120s TTL means propagation is fast.

---

## Step 10 — HTTPS (Let's Encrypt)

☁️ **On the VM:**

```bash
sudo certbot --nginx -d api.kinlight.app
```

- Enter your email for expiry notices
- Agree to Terms of Service (type `Y`)
- "Would you be willing to share your email?" → **N**

Verify auto-renewal:

```bash
sudo certbot renew --dry-run
```

Should say: "Congratulations, all simulated renewals succeeded."

Final HTTPS verification:

```bash
curl -s https://api.kinlight.app/health | python3 -m json.tool
```

The `pulseScanner.healthy` field will be `false` until the first hourly scan runs — that's expected.

---

## Step 11 — Update code references

🖥️ **Your local terminal in the repo root.**

These 8 files reference the old Railway URL and need updating. Make each change now, or say "execute the GCE deployment" in the next session and the assistant will do them all at once.

### 11a. `index.html` — Line 10 (CSP header)

Change:
```
connect-src 'self' https://emergency-exit-production.up.railway.app
```
To:
```
connect-src 'self' https://api.kinlight.app
```

### 11b. `index.html` — Line 12 (API constant)

Change:
```js
const API='https://emergency-exit-production.up.railway.app';
```
To:
```js
const API='https://api.kinlight.app';
```

### 11c. `frontend/index.html` — Same two changes

Repeat 11a and 11b in `frontend/index.html` (lines 10 and 12).

### 11d. `identity-service/main.py` — Line 126 (CORS origins)

Add to the `allow_origins` list:
```python
allow_origins=[
    "https://kinlight.app",
    "https://ramenfanclub.github.io",
    "https://api.kinlight.app",
],
```

### 11e. `tests/frontend/helpers.js` — Line 18 (test API base)

Change:
```js
const API_BASE = 'https://emergency-exit-production.up.railway.app';
```
To:
```js
const API_BASE = 'https://api.kinlight.app';
```

### 11f. `identity-service/ee-test-runner.html` — Line 177

Change the API URL to `https://api.kinlight.app`.

### 11g. `docs/reference.md` — All Railway URL references (7 occurrences)

Search and replace every `emergency-exit-production.up.railway.app` with `api.kinlight.app`. Lines affected: 23, 32, 42, 117, 257, 320, 460.

### 11h. `AGENTS.md` — API base references (2 occurrences)

Search and replace `emergency-exit-production.up.railway.app` with `api.kinlight.app`. Lines affected: 23, 32.

### 11i. `mnt/user-data/outputs/f66/frontend/index.html` — Line 11 (backup file)

Change the API URL to `https://api.kinlight.app`.

---

## Step 12 — Add CI/CD deploy job

🖥️ **Your local terminal.** Edit `.github/workflows/ci.yml`.

Add this as a **5th job** after `dependency-audit`:

```yaml
  deploy:
    name: Deploy to GCE
    needs: [backend-tests, frontend-sync, frontend-tests, dependency-audit]
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'

    steps:
      - name: Deploy to VM
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.GCE_HOST }}
          username: ${{ secrets.GCE_USER }}
          key: ${{ secrets.GCE_SSH_KEY }}
          script: |
            cd ~/kinlight
            git pull
            cd identity-service
            docker build --pull -t kinlight-api .
            docker stop kinlight-app 2>/dev/null || true
            docker rm kinlight-app 2>/dev/null || true
            docker run -d --restart=unless-stopped --name kinlight-app \
              -p 127.0.0.1:8001:8001 \
              -e PORT=8001 \
              -e GCP_PROJECT_ID='${{ secrets.GCP_PROJECT_ID }}' \
              kinlight-api
            docker image prune -af --filter "until=24h"
```

---

## Step 13 — Add GitHub Secrets

🌐 **GitHub** → `ramenfanclub/emergency-exit` → Settings → Secrets and variables → Actions → New repository secret.

| Secret | Value |
|--------|-------|
| `GCE_HOST` | Ephemeral IP from Step 1 |
| `GCE_USER` | SSH username on the VM |
| `GCE_SSH_KEY` | Private key — `cat ~/.ssh/<key-name>`, copy **everything** including the `-----BEGIN` and `-----END` lines |
| `GCP_PROJECT_ID` | Your GCP project ID (not sensitive — the app fetches the real secrets from Secret Manager) |

> `MONGO_URI`, `JWT_SECRET`, `RESEND_API_KEY`, `VAULT_ENCRYPTION_KEY`, `VAPID_PRIVATE_KEY`, and `VAPID_PUBLIC_KEY` are **no longer** stored in GitHub Secrets. They live in GCP Secret Manager (see "Secret Manager setup (F121)" below) and are fetched by the app at startup.

---

## Step 13b — Secret Manager setup (F121)

The app fetches all runtime secrets from GCP Secret Manager at startup, authenticating with the VM's attached service account. Do this once before the first deploy.

### 13b.1 Enable the API

🌐 **GCP Console** → APIs & Services → Enable APIs and services → search "Secret Manager API" → **Enable**.

> Also confirm the VM's access scope includes Cloud APIs: Compute Engine → VM instances → `kinlight-api` → Stop → Edit → "Access scopes" → **Allow full access to all Cloud APIs** → Save → Start. Without this, the metadata-server token can't call Secret Manager.

### 13b.2 Create the secrets

🌐 **GCP Console** → Security → Secret Manager → Create Secret, for each row below (or via `gcloud`):

| Secret name | Value (paste current value) |
|-------------|------------------------------|
| `kinlight-mongo-uri` | MongoDB Atlas connection string |
| `kinlight-jwt-secret` | JWT signing secret |
| `kinlight-resend-api-key` | Resend API key |
| `kinlight-vault-encryption-key` | 64-char hex AES key |
| `kinlight-vapid-private-key` | VAPID private key |
| `kinlight-vapid-public-key` | VAPID public key |

### 13b.3 Grant the VM's service account access (least privilege)

🌐 **GCP Console** → Secret Manager → each secret → Permissions → Grant Access:

- **Principal:** the VM's service account. Find it at Compute Engine → VM instances → `kinlight-api` → Service account (usually `<project-number>-compute@developer.gserviceaccount.com`).
- **Role:** `Secret Manager Secret Accessor` (`roles/secretmanager.secretAccessor`).

Grant this on **each of the six secrets** (not project-wide).

### 13b.4 Verify

☁️ **On the VM:**

```bash
gcloud secrets versions access latest --secret=kinlight-vault-encryption-key
```

Must print the 64-char hex key.

> **Note (residual risk):** any process on the VM can reach the instance metadata server and fetch the same service-account token, so this is key-*management* hardening (no plaintext in GitHub/CI/disk, plus IAM + audit), not a new crypto boundary. It does not protect against a fully compromised VM — consistent with the F04 threat model.

---

## Step 14 — Commit, push, and verify

🖥️ **Your local terminal:**

```bash
./test.sh                           # Must pass 214
cp index.html frontend/index.html   # Sync the frontend copies
git add -A
git commit -m "Migrate API from Railway to GCE (api.kinlight.app)"
git push
```

🌐 **GitHub Actions** — Watch the CI run. It now has 5 jobs. When `deploy` goes green:

🖥️ **Your local terminal:**

```bash
curl -s https://api.kinlight.app/health | python3 -m json.tool
```

Then open `https://kinlight.app` in a browser. Log in. Do a test check-in. Confirm the vault loads.

---

## Step 15 — Decommission Railway

🌐 **Railway Dashboard** → `emergency-exit-production` → Settings → **Delete project**.

Also remove any Railway env vars from 🌐 GitHub Secrets if you stored them there.

---

## Uptime monitoring (F112)

The dead man's switch only fires if the VM is alive **and** the hourly pulse scanner is running. `GET /health` already covers both: it returns **HTTP 503** when the scanner hasn't run in `PULSE_SCAN_UNHEALTHY_AFTER_HOURS` (2 hours) or has never run, and a plain `200` when healthy. Point an external monitor at it so a silent failure pages you — this is the difference between "the app is down" and "the app is up but would never notify anyone".

### Setup (UptimeRobot free tier)

> **Free-plan caveat:** the UptimeRobot **free** tier blocks monitor *creation* via the v2 API — `newMonitor` returns `403 "You are not allowed to use some settings with your current plan"`. Reads (`getMonitors`, `getAccountDetails`) work fine. So the monitor is created/edited **manually in the dashboard** (below); the scripted path is kept only to *detect* an existing monitor idempotently.

🌐 **UptimeRobot dashboard** → Monitors → create (or edit an existing monitor) with:

| Field | Value |
|-------|-------|
| Monitor Type | HTTP(s) (`type=1`) |
| Friendly Name | `kinlight-api` |
| URL / IP | `https://api.kinlight.app/health` |
| Monitoring Interval | 300s (5 minutes — free-plan minimum) |
| Alert Contact | account default email (`anggita.bayu@gmail.com`) |

An API key is only needed if you want the idempotent detect step (a **read-only** key from *Integrations & API → API*, stored in Secret Manager as `kinlight-uptimerobot-api-key` / env `KINLIGHT_UPTIMEROBOT_API_KEY`, is sufficient):

```bash
cd /app  # or identity-service/ locally
python scripts/create_uptimerobot_monitor.py   # prints "already exists" if the monitor is present
```

UptimeRobot treats any non-`2xx` response as DOWN, so the F93 503 triggers an alert with **zero extra configuration** — the monitor alone covers: VM down, Docker crashed, nginx down, and "scanner silently stopped".

### Verify the monitor actually alerts

Run the verify script — it backdates the scanner heartbeat so `/health` flips to 503, holds it open long enough for a 5-min poll to land, then restores it:

```bash
python scripts/verify_health_alert.py            # 6-minute hold by default
python scripts/verify_health_alert.py --hold-seconds 360
```

Watch your inbox for the DOWN email during the hold. The script restores the heartbeat automatically before exiting (via `POST /admin/trigger-pulse`). Do this once to prove the alert chain, then leave the monitor in place permanently.

Manual equivalent (from the Swagger UI at `https://api.kinlight.app/docs`): `POST /admin/force-stale-pulse` flips `/health` to 503; `POST /admin/trigger-pulse` restores it.

---

## Troubleshooting — quick commands

Run these ☁️ on the VM:

| Task | Command |
|------|---------|
| View app logs | `docker logs kinlight-app` |
| Tail logs | `docker logs --tail 50 -f kinlight-app` |
| Restart app | `docker restart kinlight-app` |
| Check memory | `free -h` |
| Check disk | `df -h` |
| nginx error log | `sudo tail -50 /var/log/nginx/error.log` |
| DDNS logs | `sudo journalctl -u ddns.service --no-pager \| tail -10` |
| Manual certbot renew | `sudo certbot renew` |
| Test DDNS manually | `sudo /opt/ddns/cloudflare-ddns.sh && cat /var/log/ddns.log` |

### Manual redeploy (if CI fails or IP changes)

☁️ **On the VM:**

```bash
cd ~/kinlight
git pull
cd identity-service

# Get current ephemeral IP (if changed, update GitHub secret GCE_HOST)
curl -sf4 ifconfig.me

# Rebuild and redeploy
docker build --pull -t kinlight-api .
docker stop kinlight-app && docker rm kinlight-app
docker run -d --restart=unless-stopped --name kinlight-app \
  -p 127.0.0.1:8001:8001 \
  -e PORT=8001 \
  -e GCP_PROJECT_ID='<your-gcp-project-id>' \
  kinlight-api
```

---

## Free tier limits — know your bounds

| Resource | Free allowance | This app uses | Safe? |
|----------|---------------|---------------|-------|
| e2-micro VM hours | 750/month | ~730 (always on) | ✅ |
| Disk | 30 GB-months | ~10 GB (10 GB × 730 hrs) | ✅ |
| Ephemeral IP | Free when attached | 1 attached 24/7 | ✅ |
| Network egress | 1 GB/month to most destinations | API JSON is tiny; PDFs via Resend are the only significant egress — ~200 KB × N contacts per overdue event | ✅ Comfortable |
| Cloud Monitoring | Limited free metrics | Basic CPU/memory only | ✅ |
| **Regions** | us-west1, us-central1, us-east1 only | us-west1 (Oregon) | ✅ |

---

## Encryption key backup & restore (F109)

> **Critical.** Lose `VAULT_ENCRYPTION_KEY` and every vault in MongoDB becomes permanently unrecoverable. This section covers backing it up off-machine, testing the restore, and rotating the key.

### Backup procedure

Fetch the key from GCP Secret Manager and pipe it into the backup script (runs on any machine with `gcloud` auth and secret access — e.g. the GCE VM, or your local machine):

```bash
gcloud secrets versions access latest --secret=kinlight-vault-encryption-key \
  | bash ~/kinlight/identity-service/scripts/backup-key.sh --stdin
```

Or, if the key is already in your shell env (e.g. you just retrieved it manually):

```bash
./identity-service/scripts/backup-key.sh
```

The script does two things:

1. **Digital backup** — encrypts the key with `gpg --symmetric --cipher-algo AES256 --armor` using a passphrase you choose. Outputs `vault-key-<date>.asc`. Copy the entire contents of this file into your password manager as a secure note (e.g. "Kinlight — Vault Encryption Key").

2. **Physical backup** — writes the RAW 64-char hex key to `vault-key-<date>.txt`. **Print this page, store it in a fireproof safe, then IMMEDIATELY delete the .txt file from disk.** Never leave the raw key on any internet-connected device. The safe is your encryption layer for this copy.

> **Why two backups?** The password manager copy is day-to-day recovery (needs passphrase + password manager access). The physical copy is the ultimate fallback (bypasses all digital encryption — relies solely on physical security of the safe). If you forget the GPG passphrase and lose all digital access, the printed copy in your safe still recovers everything.

### Restore test (verify your backup works)

Immediately after backing up, verify the backup restores correctly:

```bash
☁️ GCE VM
./identity-service/scripts/restore-key.sh --asc vault-key-<date>.asc --verify
```

This decrypts the `.asc` file (prompts for your GPG passphrase), extracts the key, and compares it against the live `VAULT_ENCRYPTION_KEY`. If it prints `✓ Backup VERIFIED`, you're good.

Also visually inspect the printed physical copy — confirm the hex key on paper is readable and matches.

### Emergency restore (recovering onto a fresh VM)

If you lose the VM and need to restore from backup:

```bash
🖥️ Local machine (or fresh VM)

# From digital backup (needs passphrase + password manager):
./identity-service/scripts/restore-key.sh --asc vault-key-<date>.asc

# From physical backup (no passphrase needed — just type the key):
./identity-service/scripts/restore-key.sh --raw vault-key-<date>.txt
```

Store the restored key in GCP Secret Manager (`kinlight-vault-encryption-key`) so the app can fetch it at startup:

```bash
printf '%s' "<64-char-hex-key>" | gcloud secrets versions add kinlight-vault-encryption-key --data-file=-
```

Then restart the server (`docker stop kinlight-app && docker rm kinlight-app && docker run ...`).

### Key rotation

**When to rotate (F128):** rotate the key (a) immediately on any suspected exposure, and (b) at least annually as routine hygiene. Must run on a machine with MongoDB access (the GCE VM, or locally with network access to Atlas).

```bash
# Dry run first — confirms old key works and lists what will change
python3 identity-service/scripts/rotate-key.py \
  --old-key "$OLD_KEY" \
  --new-key "$NEW_KEY"

# Rotate vault content only (skip GridFS files for speed)
python3 identity-service/scripts/rotate-key.py \
  --old-key "$OLD_KEY" \
  --new-key "$NEW_KEY" \
  --execute --skip-files

# Full rotation (vaults + files)
python3 identity-service/scripts/rotate-key.py \
  --old-key "$OLD_KEY" \
  --new-key "$NEW_KEY" \
  --execute
```

After rotation completes:
1. Update the `kinlight-vault-encryption-key` secret value in GCP Secret Manager with the new key:
   ```bash
   printf '%s' "$NEW_KEY" | gcloud secrets versions add kinlight-vault-encryption-key --data-file=-
   ```
2. Restart the server: `docker stop kinlight-app && docker rm kinlight-app && docker run ...`
3. Run a fresh backup of the new key: `./scripts/backup-key.sh`
4. Verify: `./scripts/restore-key.sh --asc vault-key-*.asc --verify`

> **Idempotent** — the script sets `encryptionKeyVersion` on each rotated document. Re-runs skip already-migrated data. Safe to run multiple times.
>
> **Atomic per document** — each vault and file is updated individually. No half-rotated state. If the script is interrupted, re-run it.

---

## Database backup & restore (F110)

Atlas M0 (free tier) has **no automated backups**, so a nightly `mongodump` job on the GCE VM copies the whole `emergency_exit` DB to a private GCS bucket. The archive is **client-side encrypted** with rclone `crypt`, so GCS never sees plaintext (the DB still contains plaintext `users.email` + bcrypt hashes — F139 is deferred).

```
nightly 03:00 (systemd timer)
  └─ backup-db.sh (deploy user)
       ├─ MONGO_URI ← Secret Manager (curl + metadata server, VM SA)
       ├─ mongodump --archive --gzip  (whole DB, incl. GridFS)
       ├─ rclone copy → crypt:        (client-side AES → GCS)
       └─ rclone delete --min-age 14d
```

### One-time setup (automated)

A single `setup-db-backup.sh` script installs tools, configures rclone (`gcs:` + `crypt:`), generates the crypt passphrase, installs the timer, and runs a test backup + verify. Two things only you can do:

**1. Create the GCS bucket + grant access** — paste this ONE command in GCP **Cloud Shell** (console.cloud.google.com → `>_` icon):

```bash
gcloud storage buckets create gs://kinlight-backups --location=us-west1 --uniform-bucket-level-access --project=moonlit-helper-426004-d2 && gcloud storage buckets add-iam-policy-binding gs://kinlight-backups --member="serviceAccount:$(gcloud projects describe moonlit-helper-426004-d2 --format='value(projectNumber)')-compute@developer.gserviceaccount.com" --role=roles/storage.objectAdmin --project=moonlit-helper-426004-d2
```

**2. Run the setup script** ☁️ on the VM:

```bash
bash ~/kinlight/identity-service/scripts/setup-db-backup.sh
```

The script installs `curl`/`jq`/`rclone` + `mongodb-database-tools`, configures both rclone remotes, installs the `kinlight-backup` timer, runs a test backup, and a `restore --verify`. At the end it **prints the crypt passphrase once** — copy it into your password manager (and optionally print it for the fireproof safe).

> ⚠️ **The crypt passphrase is a second "must-not-lose" secret** — lose it and every GCS backup is unrecoverable. It is not re-generated on re-run (it lives obscured in `~/.config/rclone/rclone.conf`). Record it via the F109 procedure, alongside `VAULT_ENCRYPTION_KEY`.
>
> The script only depends on `curl`/`jq`/`rclone` (no `gcloud`); `MONGO_URI` is fetched straight from Secret Manager via the metadata server, so no secret ever touches the command line or shell history.

### Verify

The setup script already runs a backup + verify. To re-check any time:

```bash
~/kinlight/identity-service/scripts/backup-db.sh
rclone ls crypt:                                        # should show kinlight-backup-<stamp>.archive.gz
~/kinlight/identity-service/scripts/restore-db.sh --verify   # decrypt + mongorestore --dryRun
```

### Restore

```bash
# Restore the newest archive (prompts for confirmation; --yes skips):
~/kinlight/identity-service/scripts/restore-db.sh --latest
# Restore a specific local archive:
~/kinlight/identity-service/scripts/restore-db.sh ~/kinlight/backups/kinlight-backup-<stamp>.archive.gz
```

The restore **drops and rewrites** every collection in `emergency_exit`. To recover onto a fresh cluster, also restore `VAULT_ENCRYPTION_KEY` from its F109 backup (the DB dump is useless without it — vault content/files are encrypted with it).

Manual equivalent: `gcloud storage cp gs://kinlight-backups/...` pulls the raw (still-crypt-encrypted) object; you must `rclone copy crypt:<name> .` to decrypt, never `gcloud storage cp`.
