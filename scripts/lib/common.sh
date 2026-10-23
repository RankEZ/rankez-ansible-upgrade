# common.sh - shared path/env resolution for RankEZ upgrade bash tooling.
# Source this file, then use the functions. Mirrors scripts/rankez_common.py.

# Resolve the workspace dir (RANKEZ_UPGRADE_HOME or ~/.rankez-upgrade).
rankez_workspace() {
    echo "${RANKEZ_UPGRADE_HOME:-$HOME/.rankez-upgrade}"
}

# Create the workspace tree if missing, echo the root.
rankez_workspace_ensure() {
    local ws
    ws="$(rankez_workspace)"
    mkdir -p "$ws/bundles" "$ws/inventory"
    echo "$ws"
}

# Load .env with precedence: --env flag > ./.env in CWD (legacy, warned) > workspace .env.
# Usage: rankez_load_env [--env path]
rankez_load_env() {
    local explicit=""
    if [ "$1" = "--env" ]; then
        explicit="$2"
    fi

    if [ -n "$explicit" ]; then
        if [ -f "$explicit" ]; then
            set -a; source "$explicit"; set +a
            return 0
        fi
        echo "Warning: --env $explicit not found; falling through." >&2
    fi

    local cwd_env ws_env ws
    cwd_env="$(pwd)/.env"
    ws="$(rankez_workspace)"
    ws_env="$ws/.env"

    if [ -f "$cwd_env" ]; then
        echo "Warning: using legacy .env at $cwd_env. Consider moving it to $ws_env (chmod 600)." >&2
        set -a; source "$cwd_env"; set +a
        return 0
    fi
    if [ -f "$ws_env" ]; then
        set -a; source "$ws_env"; set +a
        return 0
    fi
    return 1
}