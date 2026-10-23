"""Shared path/env resolution for RankEZ upgrade tooling.

Centralises the workspace layout so both the Python inventory generator and
the bash prep script resolve `.env`, bundles, inventories and state the same
way.

Workspace layout (RANKEZ_UPGRADE_HOME, default ~/.rankez-upgrade/):
    .env                       (chmod 600)
    bundles/<os>-v<ver>/packages/*.tar.gz
    inventory/<name>.ini
    state.json

Precedence for locating `.env`:
    CLI flag (--env) > env vars > ./.env in CWD (legacy, warned) > workspace .env
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional


def workspace_dir() -> Path:
    """Resolve the workspace directory, honouring RANKEZ_UPGRADE_HOME."""
    env = os.environ.get("RANKEZ_UPGRADE_HOME")
    if env:
        return Path(env).expanduser()
    return Path("~/.rankez-upgrade").expanduser()


def ensure_workspace() -> Path:
    """Create the workspace directory tree and return its root."""
    root = workspace_dir()
    (root / "bundles").mkdir(parents=True, exist_ok=True)
    (root / "inventory").mkdir(parents=True, exist_ok=True)
    return root


def state_path() -> Path:
    return workspace_dir() / "state.json"


def load_state() -> dict:
    """Load state.json (last used: ssh user, version, os, vault IPs, etc.)."""
    p = state_path()
    if not p.exists():
        return {}
    try:
        with open(p) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict) -> Path:
    """Merge-and-write state.json, returning its path."""
    ensure_workspace()
    merged = load_state()
    merged.update(state)
    p = state_path()
    with open(p, "w") as f:
        json.dump(merged, f, indent=2, sort_keys=True)
    return p


def locate_env(explicit: Optional[str] = None) -> Optional[Path]:
    """Return the path to the `.env` file to load, or None.

    Order: explicit flag > ./.env in CWD (legacy, warned) > workspace .env.
    """
    if explicit:
        p = Path(explicit).expanduser()
        if p.exists():
            return p
        print(f"Warning: --env {p} not found; falling through.", flush=True)

    cwd_env = Path("./.env").resolve()
    if cwd_env.exists():
        print(
            f"Warning: using legacy .env at {cwd_env}. Consider moving it to "
            f"{workspace_dir() / '.env'} (chmod 600).",
            flush=True,
        )
        return cwd_env

    ws_env = workspace_dir() / ".env"
    if ws_env.exists():
        return ws_env
    return None