#!/usr/bin/env bash
# ─── Kinlight — DB backup one-shot setup (F110) ────────────────────────────────
# Automates the F110 manual setup as far as possible:
#   1. Installs curl, jq, rclone, and mongodb-database-tools.
#   2. Configures rclone `gcs:` (VM service account) + `crypt:` (client-side AES).
#   3. Installs the nightly systemd timer (kinlight-backup).
#   4. Runs a test backup + a restore --verify.
#
# The crypt passphrase is generated FOR YOU and printed ONCE at the end — save it
# (password manager + optional printed copy). It is never written to the repo or
# any log, and you should NOT share it with anyone (including AI assistants).
#
# The ONE thing this cannot do for you: create the GCS bucket + grant the VM
# service account "Storage Object Admin". If the bucket isn't reachable, this
# script prints the exact Cloud Shell command to run, then exits.
#
# Safe to re-run (idempotent). Requires sudo for apt + systemd.
# ────────────────────────────────────────────────────────────────────────────────

set -euo pipefail

BUCKET="kinlight-backups"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRYPT_SECRET_FILE="$HOME/.config/kinlight/db-backup-crypt.txt"
TOOLS_VERSION="${MONGODB_TOOLS_VERSION:-100.14.0}"

say() { printf '\n\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

command -v sudo >/dev/null || die "sudo is required (apt install + systemd)."

# ── 0. Detect project (metadata server — no gcloud needed) ────────────────────
PROJECT="${GCP_PROJECT_ID:-$(curl -sS -H 'Metadata-Flavor: Google' \
    'http://metadata.google.internal/computeMetadata/v1/project/project-id' 2>/dev/null || true)}"
say "GCP project: ${PROJECT:-<unknown>}   (running as: $USER)"

# ── 1. Install system packages ─────────────────────────────────────────────────
say "Installing curl, jq, rclone ..."
sudo apt-get update -qq
sudo apt-get install -y -qq curl jq rclone

if ! command -v mongodump >/dev/null; then
    say "Installing mongodb-database-tools ${TOOLS_VERSION} ..."
    URL="https://fastdl.mongodb.org/tools/db/mongodb-database-tools-ubuntu2404-x86_64-${TOOLS_VERSION}.tgz"
    TMP="$(mktemp -d)"
    trap 'rm -rf "${TMP:-}"' EXIT
    curl -fsSL "$URL" -o "$TMP/tools.tgz" || die "download failed: $URL — try MONGODB_TOOLS_VERSION=<version> $0"
    tar -xzf "$TMP/tools.tgz" -C "$TMP"
    sudo install -m 0755 "$TMP"/mongodb-database-tools-*/bin/mongodump /usr/local/bin/
    sudo install -m 0755 "$TMP"/mongodb-database-tools-*/bin/mongorestore /usr/local/bin/
fi
say "mongodump $(mongodump --version | head -1)"

# ── 2. Configure rclone gcs: remote (VM service account) ──────────────────────
remote_exists() { rclone listremotes 2>/dev/null | grep -qx "$1"; }

# rclone's `config create` runs an interactive browser OAuth flow for gcs even
# when env_auth is set (it only affects runtime), so write the section directly
# instead. env_auth=true makes rclone use the VM service account via the GCE
# metadata server (no login). gcs: holds no secrets, so delete+recreate is safe.
say "Configuring rclone remote gcs: (VM service account) ..."
rclone config delete gcs >/dev/null 2>&1 || true
RCLONE_CONF="$HOME/.config/rclone/rclone.conf"
mkdir -p "$(dirname "$RCLONE_CONF")"
cat >> "$RCLONE_CONF" <<'EOF'

[gcs]
type = google cloud storage
env_auth = true
bucket_policy_only = true
location = us-west1
EOF

# ── 3. Check the GCS bucket is reachable ──────────────────────────────────────
if ! rclone lsd "gcs:${BUCKET}" >/dev/null 2>&1; then
    cat <<EOF

The bucket 'gcs:${BUCKET}' is not reachable from this VM (it is missing, or the
VM service account lacks access). Run this ONE command in GCP Cloud Shell
(console.cloud.google.com → click the '>_' icon), then re-run this script:

  gcloud storage buckets create gs://${BUCKET} --location=us-west1 --uniform-bucket-level-access --project=${PROJECT} && gcloud storage buckets add-iam-policy-binding gs://${BUCKET} --member="serviceAccount:\$(gcloud projects describe ${PROJECT} --format='value(projectNumber)')-compute@developer.gserviceaccount.com" --role=roles/storage.objectAdmin --project=${PROJECT}

EOF
    die "GCS bucket not reachable yet (see command above)."
fi
say "GCS bucket gs://${BUCKET} is reachable."

# ── 4. Configure rclone crypt: remote (generate passphrase if new) ────────────
if ! remote_exists 'crypt:'; then
    say "Creating rclone remote crypt: (client-side encryption) ..."
    gen_secret() { head -c 24 /dev/urandom | base64 | tr -d '\n'; }
    CRYPT_PASS="$(gen_secret)"
    CRYPT_SALT="$(gen_secret)"
    rclone config create crypt crypt \
        remote="gcs:${BUCKET}" \
        filename_encryption=standard \
        directory_name_encryption=true \
        password="$(rclone obscure "$CRYPT_PASS")" \
        password2="$(rclone obscure "$CRYPT_SALT")"
else
    say "rclone remote crypt: already exists (passphrase NOT regenerated)."
fi
rclone lsd crypt: >/dev/null || die "crypt: remote is broken — re-check rclone config."
chmod 600 "$RCLONE_CONF"

# ── 4b. Save + print the crypt passphrase (must never be lost) ────────────────
# If crypt pre-existed, recover the passphrase from rclone.conf so it can still
# be (re)saved + printed. Done BEFORE the test backup so a later failure can't
# leave you without the passphrase.
if [[ -z "${CRYPT_PASS:-}" ]]; then
    CRYPT_PASS="$(rclone reveal "$(rclone config dump | jq -r '.crypt.password')")"
    CRYPT_SALT="$(rclone reveal "$(rclone config dump | jq -r '.crypt.password2')")"
fi
mkdir -p "$HOME/.config/kinlight"
( umask 077; printf 'Kinlight DB backup crypt passphrase (F110)\npassword : %s\nsalt     : %s\n' \
    "$CRYPT_PASS" "$CRYPT_SALT" > "$CRYPT_SECRET_FILE" )
cat <<EOF

====================================================================
 SAVE THIS NOW — DB BACKUP ENCRYPTION PASSPHRASE
====================================================================
 password : $CRYPT_PASS
 salt     : $CRYPT_SALT

 Without these, every backup in GCS is permanently unrecoverable.

 1. Copy BOTH into your password manager (secure note).
 2. Optionally print them once for your fireproof safe.

 Also saved to: $CRYPT_SECRET_FILE  (chmod 600 — delete it after saving.)

 DO NOT share these values with anyone — including AI assistants.
====================================================================
EOF

# ── 5. Install the systemd timer ──────────────────────────────────────────────
say "Installing kinlight-backup systemd timer ..."
sed -e "s/__BACKUP_USER__/$USER/" -e "s|__BACKUP_SCRIPT__|$SCRIPT_DIR/backup-db.sh|" \
    "$SCRIPT_DIR/kinlight-backup.service" > /tmp/kinlight-backup.service
sudo install -m 0644 /tmp/kinlight-backup.service /etc/systemd/system/kinlight-backup.service
sudo install -m 0644 "$SCRIPT_DIR/kinlight-backup.timer" /etc/systemd/system/kinlight-backup.timer
sudo systemctl daemon-reload
sudo systemctl enable --now kinlight-backup.timer
rm -f /tmp/kinlight-backup.service

# ── 6. Test: backup + verify ──────────────────────────────────────────────────
say "Running a test backup ..."
"$SCRIPT_DIR/backup-db.sh"

say "Backups in GCS (decrypted view via crypt:):"
rclone ls crypt:

say "Dry-run restore verification ..."
"$SCRIPT_DIR/restore-db.sh" --verify

say "Setup complete. Nightly backups run at 03:00 (systemd timer kinlight-backup)."
