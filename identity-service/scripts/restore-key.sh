#!/usr/bin/env bash
# ─── Kinlight — Encryption Key Restore (F109) ───────────────────────────────
# Decrypts a backed-up key and prints it. Use --verify to confirm the backup
# matches the currently deployed key.
#
# Usage:
#   ./restore-key.sh --asc vault-key-2026-08-06.asc          # decrypt GPG backup
#   ./restore-key.sh --raw vault-key-2026-08-06.txt          # read raw backup
#   ./restore-key.sh --asc vault-key-2026-08-06.asc --verify # compare with live
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m' # No Colour

VERIFY=false
MODE=""
FILE=""

# ── parse args ────────────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --asc) MODE=asc; FILE="$2"; shift 2 ;;
        --raw) MODE=raw; FILE="$2"; shift 2 ;;
        --verify) VERIFY=true; shift ;;
        *) echo -e "${RED}Unknown option: $1${NC}"; exit 1 ;;
    esac
done

if [[ -z "$MODE" ]]; then
    echo "Usage: $0 --asc <file> | --raw <file> [--verify]"
    echo ""
    echo "  --asc FILE    Decrypt a GPG-encrypted .asc backup"
    echo "  --raw FILE    Read a raw plaintext .txt backup"
    echo "  --verify      Compare decrypted key against current VAULT_ENCRYPTION_KEY"
    exit 1
fi

if [[ ! -f "$FILE" ]]; then
    echo -e "${RED}ERROR: File not found: $FILE${NC}"
    exit 1
fi

# ── dependency check (GPG mode only) ──────────────────────────────────────────

if [[ "$MODE" == "asc" ]] && ! command -v gpg &>/dev/null; then
    echo -e "${RED}ERROR: gpg is not installed. Install it first:${NC}"
    echo "  Debian/Ubuntu:  sudo apt install gnupg"
    exit 1
fi

# ── decrypt / read ────────────────────────────────────────────────────────────

if [[ "$MODE" == "asc" ]]; then
    if [[ -t 0 ]]; then
        echo -e "${BOLD}Decrypting backup…${NC}"
        read -rsp "Enter your GPG passphrase: " PASSPHRASE
        echo ""
    else
        # Non-interactive (pipe/CI): let GPG fail if passphrase is required
        PASSPHRASE=""
    fi

    if [[ -n "$PASSPHRASE" ]]; then
        KEY="$(gpg --decrypt --batch --passphrase-fd 3 "$FILE" 2>/dev/null \
            3< <(printf '%s' "$PASSPHRASE"))"
    else
        KEY="$(gpg --decrypt --batch "$FILE" 2>/dev/null)"
    fi
else
    echo -e "Reading raw backup from $FILE"
    # Extract the hex line from the formatted .txt (skip the header/separator lines)
    KEY="$(grep -E '^[0-9a-fA-F]{64}$' "$FILE" | head -1)"
    if [[ -z "$KEY" ]]; then
        # If no 64-char hex line found, try reading the whole file (bare key, no formatting)
        KEY="$(tr -d '[:space:]' < "$FILE")"
    fi
fi

KEY="$(echo "$KEY" | tr -d '[:space:]')"

# ── validate format ───────────────────────────────────────────────────────────

if [[ ! "$KEY" =~ ^[0-9a-fA-F]{64}$ ]]; then
    echo -e "${RED}ERROR: Decrypted content is not a valid 64-char hex key.${NC}"
    echo "Got: ${#KEY} chars"
    echo "This could mean: wrong passphrase, corrupted file, or wrong file format."
    exit 1
fi

# ── verify against live key ───────────────────────────────────────────────────

if [[ "$VERIFY" == true ]]; then
    LIVE_KEY="${VAULT_ENCRYPTION_KEY:-}"
    LIVE_KEY="$(echo "$LIVE_KEY" | tr -d '[:space:]')"

    if [[ -z "$LIVE_KEY" ]]; then
        echo -e "${YELLOW}VAULT_ENCRYPTION_KEY not set in environment — skipping verify.${NC}"
    elif [[ "$KEY" == "$LIVE_KEY" ]]; then
        echo -e "${GREEN}✓ Backup VERIFIED — matches the currently deployed key.${NC}"
    else
        echo -e "${RED}✗ MISMATCH — backup key does NOT match VAULT_ENCRYPTION_KEY.${NC}"
        echo "This backup may be from an older key (after rotation)."
        exit 1
    fi
fi

# ── output ────────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}Restored key:${NC}"
echo ""
echo "$KEY"
echo ""
echo -e "${YELLOW}Copy this value to your VAULT_ENCRYPTION_KEY env var and restart the server.${NC}"
