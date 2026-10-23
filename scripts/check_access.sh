#!/bin/bash
# check_access.sh - pre-upgrade connectivity / sudo / docker checks (read-only).
# Phase 4 expands this into a full PASS/FAIL table via check.yml.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib/common.sh"

INV="${1:-$(rankez_workspace)/inventory/inventory.ini}"

if [ ! -f "$INV" ]; then
    echo "Error: inventory not found at $INV. Run ./rankez-upgrade inventory first." >&2
    exit 1
fi

echo "==> Checking SSH connectivity (ansible ping)..."
ansible -i "$INV" -m ping all