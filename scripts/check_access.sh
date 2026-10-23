#!/bin/bash
# check_access.sh - pre-upgrade connectivity / sudo / docker checks (read-only).
# Prints a PASS/FAIL table per host and records the check time in state.json.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib/common.sh"

INV=""
SSH_KEY=""
SSH_PORT=""
INSECURE=""
while [ $# -gt 0 ]; do
    case "$1" in
        -i|--inventory) INV="$2"; shift 2 ;;
        --ssh-key) SSH_KEY="$2"; shift 2 ;;
        --ssh-port) SSH_PORT="$2"; shift 2 ;;
        --insecure-ssh) INSECURE="-o StrictHostKeyChecking=no" ; shift ;;
        *) INV="$1"; shift ;;
    esac
done

INV="${INV:-$(rankez_workspace)/inventory/inventory.ini}"

if [ ! -f "$INV" ]; then
    echo "Error: inventory not found at $INV. Run ./rankez-upgrade inventory first." >&2
    exit 1
fi

# 1. SSH ping connectivity.
echo "==> Checking SSH connectivity (ansible ping)..."
PING_OUT="$(ansible -i "$INV" -m ping all 2>&1)"
echo "$PING_OUT"

# Identify unreachable hosts and which user failed.
UNREACHABLE=$(echo "$PING_OUT" | awk -F' *\\| *' '/UNREACHABLE/{print $1}')
if [ -n "$UNREACHABLE" ]; then
    echo ""
    echo "The following hosts were unreachable:"
    echo "$UNREACHABLE"
    echo ""
    echo "Likely SSH users to try: cloud-user, ec2-user, root, ubuntu, opc, rocky"
    echo "Re-run with a different user:"
    echo "  ./rankez-upgrade inventory --ssh-user <user>"
    echo "  ./rankez-upgrade check"
    exit 1
fi

# 2. Read-only checks (sudo / disk / docker / buildx / compose) via check.yml.
echo ""
echo "==> Running access + prerequisite checks (no changes to targets)..."
if ! ansible-playbook -i "$INV" "$REPO_ROOT/check.yml"; then
    echo ""
    echo "Checks FAILED. See output above for per-host failures." >&2
    echo "  - sudo failure: re-run the playbook with --ask-become-pass (-K)."
    echo "  - docker/buildx/compose: install on the target before continuing."
    exit 1
fi

# 3. Record check timestamp in state.json.
python3 - "$SCRIPT_DIR" <<'PYEOF'
import sys, os, json, time
script_dir = sys.argv[1]
sys.path.insert(0, os.path.abspath(script_dir))
try:
    import rankez_common as rc
    rc.save_state({"last_check": time.time()})
except Exception:
    ws = os.environ.get("RANKEZ_UPGRADE_HOME", os.path.expanduser("~/.rankez-upgrade"))
    sf = os.path.join(ws, "state.json")
    state = {}
    if os.path.exists(sf):
        try:
            state = json.load(open(sf))
        except Exception:
            state = {}
    state["last_check"] = time.time()
    json.dump(state, open(sf, "w"), indent=2, sort_keys=True)
PYEOF

echo ""
echo "All checks PASSED. Next step: ./rankez-upgrade upgrade"