#!/bin/bash
# Set consumables catalog UI config via admin API.
# Usage: ADMIN_TOKEN=<jwt> DB_NAME=consumables ./scripts/set_consumables_config.sh

set -e

API_BASE="${API_BASE:-https://db-api.nostradamus-api.com}"
DB_NAME="${DB_NAME:-consumables}"
ADMIN_TOKEN="${ADMIN_TOKEN:?Set ADMIN_TOKEN to a valid admin JWT}"

curl -sS -X PUT "${API_BASE}/admin/db-config/${DB_NAME}" \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d @"$(dirname "$0")/../config/consumables_ui_config.example.json" | python3 -m json.tool

echo ""
echo "Config set for database: ${DB_NAME}"
