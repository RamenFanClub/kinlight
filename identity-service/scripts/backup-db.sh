#!/usr/bin/env bash
# ─── Kinlight — Database Backup (F110) ────────────────────────────────────────
# Dumps the entire Atlas `emergency_exit` DB (incl. GridFS fs.files/fs.chunks)
# with mongodump, encrypts it client-side via rclone's `crypt` remote, and
# uploads it to Google Cloud Storage. Prunes archives older than the retention
# window.
#
# Design:
#   - MONGO_URI is fetched from GCP Secret Manager (`kinlight-mongo-uri`) via the
#     VM service account (gcloud ADC) — never hardcoded.
#   - The archive is uploaded to `crypt:` (client-side AES) so GCS never sees
#     plaintext. This matters because `users.email` + bcrypt hashes are still
#     plaintext in the DB (see F139).
#   - Runs nightly via systemd timer (kinlight-backup.timer), as the deploy user
#     (so rclone finds ~/.config/rclone/rclone.conf).
#
# Env overrides:
#   GCP_PROJECT_ID                optional — pass --project to gcloud
#   KINLIGHT_BACKUP_DIR           local staging dir (default $HOME/kinlight/backups)
#   KINLIGHT_BACKUP_REMOTE        rclone crypt remote (default crypt:)
#   KINLIGHT_BACKUP_RETENTION_DAYS  prune threshold (default 14)
#
# Manual run (as the deploy user):
#   ~/kinlight/identity-service/scripts/backup-db.sh
# ────────────────────────────────────────────────────────────────────────────────

set -euo pipefail

DB_NAME="emergency_exit"
SECRET_NAME="kinlight-mongo-uri"
BACKUP_ROOT="${KINLIGHT_BACKUP_DIR:-$HOME/kinlight/backups}"
REMOTE="${KINLIGHT_BACKUP_REMOTE:-crypt:}"
RETENTION_DAYS="${KINLIGHT_BACKUP_RETENTION_DAYS:-14}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE_NAME="kinlight-backup-${STAMP}.archive.gz"
ARCHIVE="${BACKUP_ROOT}/${ARCHIVE_NAME}"

log() { printf '%s\n' "$*"; }

fetch_secret() {
    # Read a secret from GCP Secret Manager via the VM metadata server (no gcloud).
    local name="$1" proj token
    proj="${GCP_PROJECT_ID:-$(curl -sS -H 'Metadata-Flavor: Google' \
        'http://metadata.google.internal/computeMetadata/v1/project/project-id' 2>/dev/null)}"
    token="$(curl -sS -H 'Metadata-Flavor: Google' \
        'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token' 2>/dev/null \
        | jq -r '.access_token')"
    curl -sS -H "Authorization: Bearer ${token}" \
        "https://secretmanager.googleapis.com/v1/projects/${proj}/secrets/${name}/versions/latest:access" 2>/dev/null \
        | jq -r '.payload.data' | base64 -d | tr -d '[:space:]'
}

log "=== Kinlight DB backup start: ${STAMP} ==="
mkdir -p "$BACKUP_ROOT"

# ── 1. Fetch MONGO_URI from Secret Manager ────────────────────────────────────
MONGO_URI="$(fetch_secret "$SECRET_NAME" || true)"
if [[ -z "$MONGO_URI" ]]; then
    log "ERROR: could not fetch ${SECRET_NAME} from Secret Manager." >&2
    log "       (is curl+jq installed? does the VM service account have secretAccessor?)" >&2
    exit 1
fi
log "fetched MONGO_URI from Secret Manager (${SECRET_NAME})"

# ── 2. Dump ────────────────────────────────────────────────────────────────────
log "mongodump --db ${DB_NAME} ..."
mongodump --uri="$MONGO_URI" --db="$DB_NAME" --archive="$ARCHIVE" --gzip
log "archive written: ${ARCHIVE} ($(du -h "$ARCHIVE" | cut -f1))"

# ── 3. Upload (client-side encrypted by the crypt remote) ─────────────────────
log "rclone copy -> ${REMOTE}"
rclone copy "$ARCHIVE" "$REMOTE"

# ── 4. Prune backups older than the retention window ──────────────────────────
log "pruning backups older than ${RETENTION_DAYS} days ..."
rclone delete "$REMOTE" --include 'kinlight-backup-*.archive.gz' --min-age "${RETENTION_DAYS}d" || true

# ── 5. Remove the local staging archive ───────────────────────────────────────
rm -f "$ARCHIVE"
log "local staging archive removed"

log "=== Kinlight DB backup complete: ${STAMP} ==="
