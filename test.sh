#!/bin/bash
# Run pytest with the project venv and required env vars.
# Usage: ./test.sh

cd "$(dirname "$0")/identity-service"
source .venv/bin/activate

MONGO_URI=test \
JWT_SECRET=test-jwt-secret-for-kinlight-32chars-min \
RESEND_API_KEY=test \
VAULT_ENCRYPTION_KEY=0000000000000000000000000000000000000000000000000000000000000000 \
python3 -m pytest test_main.py -v
