#!/usr/bin/env bash
# ─── Kinlight — Encryption Key Backup (F109) ──────────────────────────────────
# Encrypts VAULT_ENCRYPTION_KEY with GPG and outputs both an encrypted .asc file
# (for your password manager) and a raw .txt file (for printing → fireproof safe).
#
# Usage:
#   ./backup-key.sh              # reads VAULT_ENCRYPTION_KEY from env
#   echo "<key>" | ./backup-key.sh --stdin
#
# Output:
#   vault-key-<date>.asc  →  GPG-encrypted text → store in password manager
#   vault-key-<date>.txt  →  RAW key → PRINT then DELETE THIS FILE
# ────────────────────────────────────────────────────────────────────────────────

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m' # No Colour

# ── dependency check ──────────────────────────────────────────────────────────

if ! command -v gpg &>/dev/null; then
    echo -e "${RED}ERROR: gpg is not installed. Install it first:${NC}"
    echo "  Debian/Ubuntu:  sudo apt install gnupg"
    echo "  macOS:          brew install gnupg"
    exit 1
fi

# ── read key ──────────────────────────────────────────────────────────────────

KEY=""
if [[ "${1:-}" == "--stdin" ]]; then
    KEY="$(cat)"
elif [[ -n "${VAULT_ENCRYPTION_KEY:-}" ]]; then
    KEY="$VAULT_ENCRYPTION_KEY"
else
    echo -e "${YELLOW}VAULT_ENCRYPTION_KEY is not set in the environment.${NC}"
    echo "Either set it as an env var or pipe it via stdin:"
    echo "  echo \"<key>\" | $0 --stdin"
    exit 1
fi

KEY="$(echo "$KEY" | tr -d '[:space:]')"

# ── validate format ───────────────────────────────────────────────────────────

if [[ ! "$KEY" =~ ^[0-9a-fA-F]{64}$ ]]; then
    echo -e "${RED}ERROR: Invalid key format.${NC}"
    echo "Expected: 64 hex characters (e.g. a1b2c3d4...)"
    echo "Got: ${#KEY} chars"
    exit 1
fi

# ── date stamp ────────────────────────────────────────────────────────────────

DATE="$(date +%Y-%m-%d)"
ASC_FILE="vault-key-${DATE}.asc"
TXT_FILE="vault-key-${DATE}.txt"

# ── GPG encrypt ───────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  Kinlight — Encryption Key Backup${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "You will now set a ${BOLD}passphrase${NC} to encrypt the key."
echo "Choose something memorable — you will need it to restore."
echo ""

# Collect passphrase in bash (avoids GPG pinentry issue on headless VMs)
read -rsp "Enter passphrase: " PASSPHRASE
echo ""
read -rsp "Confirm passphrase: " PASSPHRASE_CONFIRM
echo ""

if [[ "$PASSPHRASE" != "$PASSPHRASE_CONFIRM" ]]; then
    echo -e "${RED}ERROR: Passphrases do not match.${NC}"
    exit 1
fi

if [[ -z "$PASSPHRASE" ]]; then
    echo -e "${RED}ERROR: Passphrase cannot be empty.${NC}"
    exit 1
fi

# Feed key on stdin (fd 0), passphrase on fd 3 (no trailing newline)
gpg --symmetric --armor --cipher-algo AES256 --output "$ASC_FILE" --batch \
    --passphrase-fd 3 3< <(printf '%s' "$PASSPHRASE") <<<"$KEY"

echo -e "${GREEN}✓${NC} Encrypted key saved to: ${BOLD}$ASC_FILE${NC}"
echo ""

# ── raw file for physical backup ──────────────────────────────────────────────

cat > "$TXT_FILE" <<PHYSICAL
========================================
KINLIGHT — VAULT ENCRYPTION KEY
========================================

Backup date:   $DATE
Purpose:       Decrypts ALL vault data in MongoDB (content + uploaded files).
               Without this key, every user's vault is permanently unrecoverable.

Store this page in a fireproof safe.
DO NOT keep this file on any internet-connected device — print it and delete the file.

Key:
$KEY

========================================
PHYSICAL

echo -e "${GREEN}✓${NC} Raw key saved to:         ${BOLD}${RED}$TXT_FILE${NC}"
echo ""

# ── storage instructions ──────────────────────────────────────────────────────

echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  Storage Instructions${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${BOLD}1. Digital backup (primary)${NC}"
echo -e "     └─ Copy the contents of ${BOLD}$ASC_FILE${NC} into your password manager"
echo -e "        as a secure note (e.g. \"Kinlight — Vault Encryption Key\")."
echo -e "     └─ The passphrase you just chose encrypts this file."
echo -e "        ${YELLOW}Memorize it or store it separately${NC} from the .asc content."
echo ""
echo -e "  ${BOLD}2. Physical backup (ultimate fallback)${NC}"
echo -e "     └─ Print ${BOLD}${RED}$TXT_FILE${NC} and store it in a fireproof safe."
echo -e "     └─ ${RED}THEN DELETE $TXT_FILE FROM DISK.${NC}"
echo -e "        Never leave the raw key on any internet-connected device."
echo ""
echo -e "  ${BOLD}3. Test your backup${NC}"
echo -e "     └─ Run: $(dirname "$0")/restore-key.sh --asc $ASC_FILE --verify"
echo -e "        This decrypts the backup and confirms it matches the live key."
echo ""
echo -e "  ${BOLD}4. Secure the printed copy${NC}"
echo -e "     └─ Confirm the printout is readable before deleting $TXT_FILE"
echo ""
echo -e "${GREEN}Backup complete.${NC}"
