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
