#!/usr/bin/env python3
"""`rankez-upgrade doctor` - print resolved workspace paths and check the environment.

Phase 1 prints resolved paths; Phase 5 extends this with dependency checks
(python deps, ansible, docker, curl).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rankez_common as rc  # noqa: E402


def print_paths():
    ws = rc.workspace_dir()
    env = rc.locate_env(os.environ.get("RANKEZ_UPGRADE_ENV_FILE"))

    print("RankEZ upgrade - resolved paths:")
    print(f"  Workspace dir : {ws}")
    print(f"  .env file     : {env if env else '(not found)'}")
    print(f"  Bundles dir   : {ws / 'bundles'}")
    print(f"  Inventory dir : {ws / 'inventory'}")
    print(f"  State file    : {rc.state_path()}")

    state = rc.load_state()
    if state:
        print("\nLast-used state:")
        for k, v in sorted(state.items()):
            print(f"  {k:14}: {v}")
    else:
        print("\nNo saved state yet (run ./rankez-upgrade setup or download).")


def main():
    print_paths()


if __name__ == "__main__":
    main()