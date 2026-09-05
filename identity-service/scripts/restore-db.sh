#!/usr/bin/env bash
# ─── Kinlight — Database Restore (F110) ───────────────────────────────────────
# Pulls a database backup archive from the rclone `crypt` remote (decrypting it
# client-side), then restores it into Atlas with mongorestore.
#
# Usage:
#   restore-db.sh --verify            # decrypt newest archive + mongorestore --dryRun (no writes)
#   restore-db.sh --latest            # restore newest archive (prompts for confirmation)
#   restore-db.sh --latest --yes      # restore newest archive without prompting
#   restore-db.sh /path/to/file.archive.gz   # restore a specific local archive
#
# Env overrides:
#   GCP_PROJECT_ID          optional — pass --project to gcloud
#   KINLIGHT_BACKUP_DIR     local staging dir (default $HOME/kinlight/backups)
#   KINLIGHT_BACKUP_REMOTE  rclone crypt remote (default crypt:)
# ────────────────────────────────────────────────────────────────────────────────

set -euo pipefail

DB_NAME="emergency_exit"
SECRET_NAME="kinlight-mongo-uri"
BACKUP_ROOT="${KINLIGHT_BACKUP_DIR:-$HOME/kinlight/backups}"
REMOTE="${KINLIGHT_BACKUP_REMOTE:-crypt:}"

log() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

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

MODE="restore"
LATEST=0
DO_CONFIRM=1
ARCHIVE_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --verify) MODE="verify" ;;
        --latest) LATEST=1 ;;
        --yes|-y) DO_CONFIRM=0 ;;
        -*) die "unknown option: $1" ;;
        *) ARCHIVE_FILE="$1" ;;
    esac
    shift
done

mkdir -p "$BACKUP_ROOT"

# ── Fetch MONGO_URI from Secret Manager ───────────────────────────────────────
MONGO_URI="$(fetch_secret "$SECRET_NAME" || true)"
[[ -n "$MONGO_URI" ]] || die "could not fetch ${SECRET_NAME} from Secret Manager (is curl+jq installed? does the VM SA have secretAccessor?)"

# ── Resolve the archive ────────────────────────────────────────────────────────
NEED_DOWNLOAD=0
if [[ -z "$ARCHIVE_FILE" ]]; then
    NAME="$(rclone lsf "$REMOTE" --files-only --include 'kinlight-backup-*.archive.gz' 2>/dev/null | sort | tail -1)"
    [[ -n "$NAME" ]] || die "no backup archives found on ${REMOTE}"
    log "latest remote archive: ${NAME}"
    ARCHIVE_FILE="${BACKUP_ROOT}/${NAME}"
    NEED_DOWNLOAD=1
fi

if [[ ! -f "$ARCHIVE_FILE" ]]; then
    if [[ "$NEED_DOWNLOAD" -eq 1 ]]; then
        log "downloading (decrypting) from ${REMOTE} ..."
        rclone copy "${REMOTE}/$(basename "$ARCHIVE_FILE")" "$BACKUP_ROOT/"
    else
        die "archive not found: ${ARCHIVE_FILE}"
    fi
fi
[[ -f "$ARCHIVE_FILE" ]] || die "archive not found: ${ARCHIVE_FILE}"

# ── Integrity check ────────────────────────────────────────────────────────────
log "verifying gzip integrity ..."
gzip -t "$ARCHIVE_FILE" || die "archive failed gzip integrity check"

# ── Verify mode: dry-run, no writes ───────────────────────────────────────────
if [[ "$MODE" == "verify" ]]; then
    log "mongorestore --dryRun (no writes) ..."
    mongorestore --uri="$MONGO_URI" --archive="$ARCHIVE_FILE" --gzip --dryRun
    log "VERIFY OK — archive is readable and would restore into ${DB_NAME}."
    exit 0
fi

# ── Full restore ───────────────────────────────────────────────────────────────
if [[ "$DO_CONFIRM" -eq 1 ]]; then
    read -r -p "Restore ${ARCHIVE_FILE} into ${DB_NAME} (DROPS existing data)? [y/N] " ans < /dev/tty || ans=""
    [[ "$ans" =~ ^[Yy]$ ]] || die "aborted"
fi

log "mongorestore --drop into ${DB_NAME} ..."
mongorestore --uri="$MONGO_URI" --archive="$ARCHIVE_FILE" --gzip --drop
log "restore complete."
