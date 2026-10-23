#!/bin/bash
# setup_workspace.sh - interactively create the workspace and .env file.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib/common.sh"

WS="$(rankez_workspace_ensure)"
ENV_FILE="$WS/.env"

echo "RankEZ upgrade workspace setup"
echo "Workspace: $WS"
echo ""

# RANKEZ portal credentials (auto-discovery)
read -r -p "RankEZ portal URL [https://192.168.0.21]: " RANKEZ_URL
RANKEZ_URL="${RANKEZ_URL:-https://192.168.0.21}"
read -r -p "RankEZ username [admin]: " RANKEZ_USERNAME
RANKEZ_USERNAME="${RANKEZ_USERNAME:-admin}"
read -r -s -p "RankEZ password: " RANKEZ_PASSWORD; echo
read -r -p "Verify SSL? [false]: " RANKEZ_VERIFY_SSL
RANKEZ_VERIFY_SSL="${RANKEZ_VERIFY_SSL:-false}"

# Zendesk credentials (download)
read -r -p "Zendesk email: " ZENDESK_EMAIL
read -r -s -p "Zendesk password: " ZENDESK_PASSWORD; echo

# Write .env (hidden passwords handled by read -s above)
umask 077
{
    echo "RANKEZ_URL=$RANKEZ_URL"
    echo "RANKEZ_USERNAME=$RANKEZ_USERNAME"
    echo "RANKEZ_PASSWORD=$RANKEZ_PASSWORD"
    echo "RANKEZ_VERIFY_SSL=$RANKEZ_VERIFY_SSL"
    echo ""
    echo "ZENDESK_EMAIL=$ZENDESK_EMAIL"
    echo "ZENDESK_PASSWORD=$ZENDESK_PASSWORD"
} > "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo ""
echo "Workspace created. .env written to $ENV_FILE (chmod 600)."
echo "Next step: ./rankez-upgrade download"