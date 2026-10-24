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
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import rankez_common as rc  # noqa: E402

try:
    import manifest as _manifest  # noqa: E402
    _MANIFEST = _manifest.load()
except Exception:
    _manifest = None
    _MANIFEST = None


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


def print_manifest():
    print("\nRelease manifest:")
    if _manifest is None or _MANIFEST is None:
        print("  [WARN] release manifest (manifest/releases.json) not found or unparsable.")
        print("         Version and upgrade-path checks are disabled. Run the maintainer")
        print("         `python tools/fetch_release_manifest.py` and `git pull` to refresh.")
        return

    print(f"  Path          : {_manifest.local_manifest_path()}")
    ga = _manifest.generated_at(_MANIFEST)
    print(f"  generated_at  : {ga.isoformat() if ga else '(unknown)'}")
    age = _manifest.age_days(_MANIFEST)
    if age is not None:
        stale = "" if age <= _manifest.DEFAULT_STALE_DAYS else \
            f"  [WARN: older than {_manifest.DEFAULT_STALE_DAYS} days - refresh it]"
        print(f"  age           : {age} days{stale}")
    latest = _manifest.latest(_MANIFEST)
    if latest:
        print(f"  latest version: {latest}")


def _which(cmd):
    return shutil.which(cmd) is not None


def print_upgrade_status():
    """Show detected current version and whether a path to latest exists."""
    state = rc.load_state()
    iv = state.get("installed_versions") or {}
    cur = state.get("current_version")

    print("\nUpgrade status:")
    if iv:
        for k, v in sorted(iv.items()):
            print(f"  {k:12}: {v}")
        if len({v for v in iv.values() if v}) > 1:
            print("  [WARN] components are on different versions (half-finished upgrade?)")
    elif cur:
        print(f"  current      : {cur}")
    else:
        print("  current      : (unknown - run ./rankez-upgrade check for detection)")

    if _manifest is not None and cur:
        latest = _manifest.latest(_MANIFEST)
        if latest and cur != latest:
            if _manifest.can_upgrade(_MANIFEST, cur, latest):
                print(f"  to latest    : direct -> {latest}")
            else:
                steps = _manifest.suggest_upgrade_steps(_MANIFEST, cur, latest)
                print(f"  to latest    : {' -> '.join(steps) if steps else '(no path)'}")


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
    print_manifest()
    print_upgrade_status()
    ok = check_environment()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()