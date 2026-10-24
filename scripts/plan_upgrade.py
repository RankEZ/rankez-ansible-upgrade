#!/usr/bin/env python3
"""Plan a RankEZ upgrade (possibly multi-hop) from the committed manifest.

Pure path logic lives here (and in scripts/lib/manifest.py), so it is
unit-testable without contacting a target. The Ansible playbook runs exactly
ONE hop per `ansible-playbook` invocation; the `rankez-upgrade` wrapper loops
over the hops produced here.

Usage:
  python scripts/plan_upgrade.py --current 4.11.0 --target 5.10.0
  python scripts/plan_upgrade.py --current 4.11.0            # target = latest
  python scripts/plan_upgrade.py --current 4.11.0 --target 5.10.0 --json

Exit codes:
  0  a plan was computed (direct / multi-hop / inferred)
  1  hard block: downgrade / same version / unknown target / no path at all
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))

import rankez_common as rc  # noqa: E402
import manifest as M  # noqa: E402


def _semver(v: str) -> tuple:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", v or "")
    return tuple(int(x) for x in m.groups()) if m else (-1, -1, -1)


def _hop_versions(m: dict, target: str) -> dict:
    """Per-component versions for a hop target, for tarball planning."""
    out = {}
    for c in M.TARBALL_COMPONENTS:
        out[c] = M.tarball_version(m, target, c, target) or target
    return out


def _tarballs(comp_versions: dict) -> list:
    return [f"pam-{c}-{v}.tar.gz" for c, v in comp_versions.items()]


def plan(current: str, target: str, m: dict) -> dict:
    """Compute the upgrade plan. Pure; does no I/O. `m` is a loaded manifest."""
    plan = {
        "current": current,
        "target": target,
        "status": "ok",
        "mode": None,
        "hops": [],
        "num_hops": 0,
        "inferred_hops": 0,
        "manifest_age_days": M.age_days(m),
        "manifest_stale": (M.age_days(m) or 0) > M.DEFAULT_STALE_DAYS,
        "reachable_targets": M.list_targets(m, current),
    }

    # 1. Unknown target.
    if target not in M.releases(m):
        plan["status"] = "unknown_target"
        return plan

    # 2. Downgrade / no-op.
    c, t = _semver(current), _semver(target)
    if t < c:
        plan["status"] = "downgrade"
        return plan
    if t == c:
        plan["status"] = "same"
        return plan

    # 3. Compute the hop chain (documented -> inferred -> none).
    if M.can_upgrade(m, current, target):
        mode, path = "direct", [current, target]
    else:
        p = M.upgrade_path(m, current, target)
        if p:
            mode, path = "multi-hop", p
        else:
            p = M.suggest_upgrade_steps(m, current, target)
            if p:
                mode, path = "inferred", p
            else:
                plan["status"] = "no_path"
                return plan

    plan["mode"] = mode
    hops = []
    for a, b in zip(path, path[1:]):
        documented = M.can_upgrade(m, a, b)
        hop = {
            "from": a,
            "to": b,
            "inferred": not documented,
            "component_versions": _hop_versions(m, b),
        }
        hop["tarballs"] = _tarballs(hop["component_versions"])
        hops.append(hop)
    plan["hops"] = hops
    plan["num_hops"] = len(hops)
    plan["inferred_hops"] = sum(1 for h in hops if h["inferred"])
    return plan


def _resolve_current(args) -> str:
    if args.current:
        return args.current
    state = rc.load_state()
    cv = state.get("current_version")
    if cv:
        return str(cv)
    iv = state.get("installed_versions") or {}
    if isinstance(iv, dict) and iv:
        vv = {v for v in iv.values() if v}
        if len(vv) == 1:
            return vv.pop()
    return ""


def _print_human(p: dict) -> None:
    if p["status"] != "ok":
        print(f"{p['status'].upper()}: {p['current']} -> {p['target']}")
        if p["status"] == "unknown_target":
            print(f"  target {p['target']} is not a known release in the manifest.")
        elif p["status"] in ("downgrade", "same"):
            print("  downgrade / no-op is not supported; pick a higher version.")
        elif p["status"] == "no_path":
            print(f"  no supported (or inferred) route from {p['current']} to {p['target']}.")
            if p["reachable_targets"]:
                print(f"  reachable: {', '.join(p['reachable_targets'])}")
        return

    src = p["current"]
    for h in p["hops"]:
        tag = " (INFERRED - not in release notes)" if h["inferred"] else ""
        print(f"  {src} -> {h['to']}{tag}")
        src = h["to"]
    print(f"mode={p['mode']}  hops={p['num_hops']}"
          f"  inferred={p['inferred_hops']}  target={p['target']}")
    if p["manifest_stale"]:
        print(f"  WARNING: manifest is {p['manifest_age_days']} days old "
              f"(>{M.DEFAULT_STALE_DAYS}); refresh it.")
    if p["mode"] in ("multi-hop", "inferred") and p["reachable_targets"]:
        print(f"  directly reachable from {p['current']}: {', '.join(p['reachable_targets'])}")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--current", "--current-version", dest="current", default=None,
                   help="installed version (else state.json current_version/installed_versions)")
    p.add_argument("--target", default=None, help="target version (default: latest)")
    p.add_argument("--json", action="store_true", help="print the plan as JSON to stdout")
    p.add_argument("--no-state", action="store_true", help="do not write plan to state.json")
    args = p.parse_args(argv)

    m = M.load()
    current = _resolve_current(args)
    if not current:
        print("Error: cannot determine the installed version. Pass --current X.Y.Z "
              "or run detection first (./rankez-upgrade check).", file=sys.stderr)
        return 2

    target = args.target or M.latest(m)
    if not target:
        print("Error: no target version given and the manifest has no releases.",
              file=sys.stderr)
        return 2

    result = plan(current, target, m)

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Upgrade plan: {result['current']} -> {result['target']}")
        _print_human(result)

    if not args.no_state:
        rc.save_state({"plan": result})

    # 0 = computed ok; 1 = hard block.
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))