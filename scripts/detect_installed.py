#!/usr/bin/env python3
"""Parse per-host version-detection clues and write `installed_versions` to state.

Reads a JSON object on stdin mapping hostname -> detection clues, e.g.:

    {"10.0.0.1": {"docker": "vault rankez/pam-vault:5.8.0\\npac rankez/pam-pac:5.8.0",
                  "markers": "5.8.0"},
     "10.0.0.2": {"docker": "", "markers": "5.8.0"}}

For each host, resolves a component (vault/pac/psm/cpm/cp) -> version mapping by
scanning, in order:
  1. Docker container names / image tags (`<name> <image>` lines),
  2. version markers (raw `x.y.z` strings from install.conf / VERSION files).

The merged per-component versions are written to workspace `state.json` under
`installed_versions`. A component that resolves to DIFFERENT versions across
hosts is reported on stderr (half-finished upgrade -> the caller should block).

Open question (verify on a real system): exact Docker container/image naming and
the install.conf / VERSION filename. The token matchers below are best-effort.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rankez_common as rc  # noqa: E402

VER_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")

# Component label tokens (lowercased, matched as substring in name/image).
_COMPONENTS = ("vault", "pac", "psm", "cpm", "cp", "credprov", "credential")
# raw token -> canonical key
_CANON = {
    "vault": "vault", "pac": "pac", "psm": "psm", "cpm": "cpm",
    "cp": "cp", "credprov": "cp", "credential": "cp",
}


def _canonical(token: str) -> str | None:
    t = token.lower()
    # "pac" must not match inside "package"; match longer tokens first.
    for label in ("credprov", "credential", "vault", "psm", "cpm", "pac", "cp"):
        if label in t:
            return _CANON[label]
    return None


def parse_docker(text: str) -> dict:
    """Parse '<name> <image>' lines -> {component: version}."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        name = parts[0] if parts else ""
        image = parts[1] if len(parts) > 1 else ""
        blob = f"{name} {image}"
        comp = _canonical(blob)
        if not comp:
            continue
        m = VER_RE.search(image or blob)
        if m and comp not in out:
            out[comp] = m.group(0)
    return out


def parse_markers(text: str) -> list[str]:
    return sorted(set(VER_RE.findall(text or "")))


def _parse_kv(line: str):
    """Parse a 'component=version' / 'component: version' line -> (comp, ver)."""
    kv = re.split(r"[=:]\s*", line.strip(), 1)
    if len(kv) != 2:
        return None, None
    comp = _canonical(kv[0])
    m = VER_RE.search(kv[1])
    if comp and m:
        return comp, m.group(0)
    return None, None


def parse_host(clues: dict) -> dict:
    """Resolve {component: version} for one host (docker, then component=ver)."""
    result = parse_docker(clues.get("docker", "") or "")
    for line in (clues.get("markers", "") or "").splitlines():
        comp, ver = _parse_kv(line)
        if comp and ver and comp not in result:
            result[comp] = ver
    return result


def merge(hosts: dict):
    """Merge per-host versions; return (installed_versions, conflicts)."""
    merged: dict[str, str] = {}
    by_comp: dict[str, set] = {}
    for host, clues in hosts.items():
        for comp, ver in parse_host(clues).items():
            by_comp.setdefault(comp, set()).add(ver)
    conflicts = {c: sorted(v) for c, v in by_comp.items() if len(v) > 1}
    merged = {c: next(iter(v)) for c, v in by_comp.items() if len(v) == 1}
    return merged, conflicts


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", help="read clues JSON from a file (default: stdin)")
    args = p.parse_args()

    if args.input:
        with open(args.input, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)

    installed, conflicts = merge(data)
    if conflicts:
        print("WARNING: components on different versions across hosts "
              "(half-finished upgrade?):", file=sys.stderr)
        for c, vs in conflicts.items():
            print(f"  {c}: {', '.join(vs)}", file=sys.stderr)
    rc.save_state({"installed_versions": installed})
    print(json.dumps(installed, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())