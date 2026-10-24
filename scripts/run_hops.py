#!/usr/bin/env python3
"""Drive a multi-hop upgrade: ONE `ansible-playbook` run per hop.

Called by `rankez-upgrade upgrade --auto-hops`. Loads the plan written by
`scripts/plan_upgrade.py` (state.json `plan`) and, for each hop:

  1. pre-verifies EVERY hop's bundle exists (before any downtime),
  2. writes a hop vars file ({upgrade_version, component_versions,
     local_bundle_dir, current_version}),
  3. confirms the hop (type the target version),
  4. runs `ansible-playbook -i <inventory> upgrade.yml -e @<hop_vars>`,
  5. records the completed hop (resume point) in state.json.

On failure it stops and prints a resume hint. `--allow-inferred` is required
when the plan contains inferred (non-documented) hops; without it, inferred
hops are never auto-run.

Design (from PLAN-upgrade-path.md): Ansible runs exactly ONE hop per playbook
run; this wrapper loops. Path logic stays in manifest.py / plan_upgrade.py.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import rankez_common as rc  # noqa: E402


def _bundle_dir(version: str, os_hint: str | None) -> Path | None:
    """Resolve the packages/ dir for a version, mirroring pre_upgrade_inventory."""
    ws = rc.workspace_dir() / "bundles"
    for os_name in ([os_hint] if os_hint else ["rhel", "ubuntu"]):
        cand = ws / f"onebox-offline-{os_name}-v{version}" / "packages"
        if cand.is_dir():
            return cand
    return None


def _verify_all_bundles(plan: dict, os_hint: str | None) -> list[str]:
    missing = []
    for hop in plan.get("hops", []):
        if _bundle_dir(hop["to"], os_hint) is None:
            missing.append(hop["to"])
    return missing


def _confirm(hop: dict) -> bool:
    if not sys.stdin.isatty():
        return True  # non-interactive: assume yes (caller should use --yes flow)
    ans = input(f"Upgrade {hop['from']} -> {hop['to']} now? type {hop['to']} to continue: ")
    return ans.strip() == hop["to"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--allow-inferred", action="store_true")
    ap.add_argument("--yes", "-y", action="store_true", help="skip per-hop confirmation")
    ap.add_argument("--inventory", default=None, help="inventory path (default: workspace)")
    ap.add_argument("--os", default=None, help="bundle OS hint (rhel|ubuntu)")
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    args = ap.parse_args()

    state = rc.load_state()
    plan = state.get("plan") or {}
    hops = plan.get("hops") or []
    if not hops:
        print("No upgrade plan found. Run './rankez-upgrade plan' first.", file=sys.stderr)
        return 1

    if plan.get("inferred_hops", 0) > 0 and not args.allow_inferred:
        print("Plan contains inferred (non-documented) hops. Re-run with "
              "--allow-inferred to auto-run them.", file=sys.stderr)
        return 3

    os_hint = args.os or state.get("os")
    inv = args.inventory or str(rc.workspace_dir() / "inventory" / "inventory.ini")

    # Pre-verify all bundles before touching anything.
    missing = _verify_all_bundles(plan, os_hint)
    if missing:
        print("Missing bundle(s) for the following hop targets; download first "
              "(./rankez-upgrade download --plan):", file=sys.stderr)
        for v in missing:
            print(f"  - {v}", file=sys.stderr)
        return 1

    completed = set(state.get("completed_hops") or [])
    for i, hop in enumerate(hops):
        key = f"{hop['from']}->{hop['to']}"
        if key in completed:
            print(f"Skipping completed hop {key}")
            continue

        bundle = _bundle_dir(hop["to"], os_hint)
        hop_vars = {
            "upgrade_version": hop["to"],
            "current_version": hop["from"],
            "component_versions": hop["component_versions"],
            "local_bundle_dir": str(bundle),
        }
        vars_path = rc.workspace_dir() / f"hop_vars_{i}.yml"
        lines = ["---"]
        for k, v in hop_vars.items():
            if isinstance(v, dict):
                lines.append(f"{k}: {json.dumps(v)}")
            else:
                lines.append(f"{k}: {v}")
        vars_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        if not args.yes and not _confirm(hop):
            print("Aborted.", file=sys.stderr)
            return 1

        cmd = ["ansible-playbook", "-i", inv, f"{args.repo}/upgrade.yml", "-e", f"@{vars_path}"]
        print(f"==> Hop {i+1}/{len(hops)}: {hop['from']} -> {hop['to']}")
        r = subprocess.run(cmd)
        if r.returncode != 0:
            print(f"Hop {key} failed (rc={r.returncode}). "
                  f"Resume with: ./rankez-upgrade upgrade --auto-hops --resume",
                  file=sys.stderr)
            return r.returncode

        completed.add(key)
        rc.save_state({"completed_hops": sorted(completed)})

    print("All hops completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())