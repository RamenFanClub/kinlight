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
  -e MONGO_URI='<paste-from-railway>' \
  -e JWT_SECRET='<paste-from-railway>' \
  -e RESEND_API_KEY='<paste-from-railway>' \
  -e VAULT_ENCRYPTION_KEY='<paste-from-railway>' \
  kinlight-api
```

> Copy the 4 env var values from your Railway dashboard before shutting it down. They're the same strings, nothing changes.

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

### 7b. Install the DDNS script

☁️ **On the VM:**

```bash
sudo mkdir -p /opt/ddns
sudo nano /opt/ddns/cloudflare-ddns.sh
```

Paste the following, replacing `<your-token-here>` with the token from step 7a:

```bash
#!/bin/bash
ZONE="kinlight.app"
RECORD="api.$ZONE"
CF_TOKEN="<your-token-here>"
CURRENT_IP=$(curl -sf4 ifconfig.me)

[ -z "$CURRENT_IP" ] && exit 0

ZONE_ID=$(curl -sf -H "Authorization: Bearer $CF_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones?name=$ZONE" | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['result'][0]['id'])")

RECORD_DATA=$(curl -sf -H "Authorization: Bearer $CF_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records?type=A&name=$RECORD")

RECORD_ID=$(echo "$RECORD_DATA" | python3 -c "import sys,json; print(json.load(sys.stdin)['result'][0]['id'])")
DNS_IP=$(echo "$RECORD_DATA" | python3 -c "import sys,json; print(json.load(sys.stdin)['result'][0]['content'])")

if [ "$CURRENT_IP" != "$DNS_IP" ]; then
  curl -sf -X PATCH -H "Authorization: Bearer $CF_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"content\":\"$CURRENT_IP\",\"name\":\"$RECORD\",\"type\":\"A\",\"ttl\":120}" \
    "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/dns_records/$RECORD_ID" \
    && echo "$(date): Updated $RECORD → $CURRENT_IP" >> /var/log/ddns.log
fi
```

```bash
sudo chmod +x /opt/ddns/cloudflare-ddns.sh
```

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

### 11g. `CLAUDE.md` — All Railway URL references (7 occurrences)

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
              -e MONGO_URI='${{ secrets.MONGO_URI }}' \
              -e JWT_SECRET='${{ secrets.JWT_SECRET }}' \
              -e RESEND_API_KEY='${{ secrets.RESEND_API_KEY }}' \
              -e VAULT_ENCRYPTION_KEY='${{ secrets.VAULT_ENCRYPTION_KEY }}' \
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
| `MONGO_URI` | MongoDB connection string (from Railway) |
| `JWT_SECRET` | JWT signing secret (from Railway) |
| `RESEND_API_KEY` | Resend API key (from Railway) |
| `VAULT_ENCRYPTION_KEY` | AES-256 encryption key (from Railway) |

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

## Optional: Uptime monitoring

🌐 **UptimeRobot** (free tier, single monitor, 5-min interval):

- New Monitor → Type: **HTTPS**
- URL: `https://api.kinlight.app/health`
- Monitoring interval: 5 minutes

You'll get an email alert if the VM goes down, Docker crashes, or the pulse scanner hasn't run in 2+ hours (F93 health check returns HTTP 503).

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
  -e MONGO_URI='<value>' \
  -e JWT_SECRET='<value>' \
  -e RESEND_API_KEY='<value>' \
  -e VAULT_ENCRYPTION_KEY='<value>' \
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

Run on the GCE VM (the key lives in the Docker env):

```bash
☁️ GCE VM
docker exec kinlight-app printenv VAULT_ENCRYPTION_KEY | bash ~/kinlight/identity-service/scripts/backup-key.sh --stdin
```

Or, if you're SSH'd in and the key is set in your shell env:

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

Copy the output to `VAULT_ENCRYPTION_KEY` in your `docker run` command and GitHub Secrets.

### Key rotation

Use when you want to change the encryption key (e.g. after a suspected exposure, or periodically). Must run on a machine with MongoDB access (the GCE VM, or locally with network access to Atlas).

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
1. Update `VAULT_ENCRYPTION_KEY` to the new key in Docker env and GitHub Secrets
2. Restart the server: `docker stop kinlight-app && docker rm kinlight-app && docker run ...`
3. Run a fresh backup of the new key: `./scripts/backup-key.sh`
4. Verify: `./scripts/restore-key.sh --asc vault-key-*.asc --verify`

> **Idempotent** — the script sets `encryptionKeyVersion` on each rotated document. Re-runs skip already-migrated data. Safe to run multiple times.
>
> **Atomic per document** — each vault and file is updated individually. No half-rotated state. If the script is interrupted, re-run it.
