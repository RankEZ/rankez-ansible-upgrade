#!/usr/bin/env python3
"""`rankez-upgrade doctor` - print resolved workspace paths and check the environment.

Checks python deps (requests, python-dotenv), ansible, docker (for bundle
building), and curl (for package download).
"""

import os
import shutil
import subprocess
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
    print(f"  Logs dir      : {ws / 'logs'}")
    print(f"  State file    : {rc.state_path()}")

    state = rc.load_state()
    if state:
        print("\nLast-used state:")
        for k, v in sorted(state.items()):
            print(f"  {k:14}: {v}")
    else:
        print("\nNo saved state yet (run ./rankez-upgrade setup or download).")


def _which(cmd):
    return shutil.which(cmd) is not None


def check_python_deps():
    deps = {"requests": "requests", "python-dotenv": "dotenv"}
    missing = []
    for label, mod in deps.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append(label)
    return missing


def check_environment():
    print("\nEnvironment checks:")
    results = []

    missing = check_python_deps()
    if missing:
        results.append(("FAIL", "python deps", f"missing: {', '.join(missing)}; run pip install -r requirements.txt"))
    else:
        results.append(("PASS", "python deps", "requests, python-dotenv"))

    if _which("ansible-playbook"):
        ver = subprocess.run(["ansible-playbook", "--version"], capture_output=True, text=True)
        results.append(("PASS", "ansible", ver.stdout.splitlines()[0] if ver.stdout else "installed"))
    else:
        results.append(("FAIL", "ansible", "ansible-playbook not found on PATH"))

    if _which("docker"):
        results.append(("PASS", "docker", "available (for bundle building)"))
    else:
        results.append(("WARN", "docker", "not found; system_deps step in download will be skipped"))

    if _which("curl"):
        results.append(("PASS", "curl", "available (for package download)"))
    else:
        results.append(("FAIL", "curl", "not found; needed to download packages"))

    for status, label, detail in results:
        print(f"  [{status:4}] {label:14} {detail}")

    return all(r[0] != "FAIL" for r in results)


def main():
    print_paths()
    ok = check_environment()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()