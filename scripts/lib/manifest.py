"""Read the committed RankEZ release manifest (`manifest/releases.json`).

The manifest is generated LOCALLY by the maintainer via `tools/fetch_release_manifest.py`
and committed to the repo. It records, per release version:

  * the per-component tarball versions (vault / pac / psm / cpm / cp, plus any
    informational `extra` components such as remoteapp / utility), and
  * the *supported upgrade paths* (`upgrade_from`), i.e. which installed versions
    may be upgraded directly to this one.

This module is the single source of truth at upgrade time. Neither the download
step nor the inventory/upgrade step guesses versions from the current date any
more: they look them up here.

Manifest location (first match wins):
  1. `RANKEZ_MANIFEST_PATH`        -> explicit local file path
  2. `RANKEZ_MANIFEST_URL`         -> fetch a remote manifest (JSON) over HTTPS
  3. `<repo>/manifest/releases.json` (shipped in the repo, updated by `git pull`)

Component keys: the five tarball prefixes used by `prep_offline_bundle.sh` are
`vault`, `pac`, `psm`, `cpm`, `cp`. Any OTHER Feishu component (remoteapp,
utility, ...) is stored under the release's `extra` dict and is informational.

The manifest schema is version 1. See `manifest/releases.json` for the shape.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover - optional URL fetch
    requests = None

SCHEMA_VERSION = 1

# The five tarball prefixes produced by the offline bundle generator.
TARBALL_COMPONENTS = ("vault", "pac", "psm", "cpm", "cp")

# Staleness threshold used by `age()` / the CLI (`--stale-days`).
DEFAULT_STALE_DAYS = 90


# --------------------------------------------------------------------------- paths
def default_manifest_path() -> Path:
    """Return the shipped manifest inside this repository."""
    # scripts/lib/manifest.py -> parents[0]=lib, [1]=scripts, [2]=repo root.
    return Path(__file__).resolve().parents[2] / "manifest" / "releases.json"


def local_manifest_path() -> Path:
    """Resolve an explicit local manifest path, honouring RANKEZ_MANIFEST_PATH."""
    override = os.environ.get("RANKEZ_MANIFEST_PATH")
    if override:
        p = Path(override).expanduser()
        if p.exists():
            return p
    return default_manifest_path()


def _fetch_url(url: str) -> dict:
    if requests is None:
        raise RuntimeError("requests is not installed; cannot fetch a remote manifest")
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()


# --------------------------------------------------------------------------- loading
def load(path: Optional[str] = None) -> dict:
    """Load the manifest as a dict.

    `path` (str/path-like) wins; otherwise RANKEZ_MANIFEST_URL (remote), else
    RANKEZ_MANIFEST_PATH / the shipped repo file.
    """
    if path:
        return _read(Path(path))
    url = os.environ.get("RANKEZ_MANIFEST_URL")
    if url:
        return _fetch_url(url)
    return _read(local_manifest_path())


def _read(p: Path) -> dict:
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def schema_ok(m: dict) -> bool:
    return isinstance(m, dict) and m.get("schema_version") == SCHEMA_VERSION


# --------------------------------------------------------------------------- helpers
def _semver(v: str) -> tuple:
    """Parse '5.10.0' -> (5, 10, 0); unparsable -> (-1,)."""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", v or "")
    if not m:
        return (-1,)
    return tuple(int(x) for x in m.groups())


def versions(m: dict) -> List[str]:
    """All known release versions, newest first."""

    def key(v):
        return _semver(v)

    return sorted(m.get("releases", {}).keys(), key=key, reverse=True)


def releases(m: dict) -> Dict[str, dict]:
    return m.get("releases", {})


def latest(m: dict, major: Optional[str] = None) -> Optional[str]:
    """Latest known version, optionally restricted to one major ('4' or '5')."""
    vs = versions(m)
    if major is None:
        return vs[0] if vs else None
    for v in vs:
        if v.split(".")[0] == str(major):
            return v
    return None


# --------------------------------------------------------------------------- lookups
def components(m: dict, version: str) -> Dict[str, str]:
    """Per-component versions for `version` (tarball prefixes + extras merged)."""
    rel = releases(m).get(version, {})
    comps = dict(rel.get("components", {}) or {})
    comps.update(rel.get("extra", {}) or {})
    return comps


def tarball_version(m: dict, version: str, component: str,
                    fallback: Optional[str] = None) -> Optional[str]:
    """Version of one tarball prefix (vault/pac/psm/cpm/cp) for release `version`.

    Falls back to `fallback` (e.g. the release version) when the component has no
    explicit entry.
    """
    comps = components(m, version)
    v = comps.get(component)
    if v is None or v == "n/a":
        return fallback if fallback is not None else None
    return v


def upgrade_from(m: dict, version: str) -> List[str]:
    rel = releases(m).get(version, {})
    return list(rel.get("upgrade_from", []) or [])


def can_upgrade(m: dict, frm: str, to: str) -> bool:
    """True when an installed version `frm` may be upgraded DIRECTLY to `to`."""
    if frm == to:
        return True
    return frm in upgrade_from(m, to)


def list_targets(m: dict, current: str) -> List[str]:
    """Release versions that accept `current` as a direct upgrade source."""
    out = []
    for v in versions(m):
        if current in upgrade_from(m, v):
            out.append(v)
    return out


def upgrade_path(m: dict, frm: str, to: str) -> Optional[List[str]]:
    """Shortest hop chain frm -> ... -> to via `upgrade_from` edges (BFS).

    Returns None when no path exists. A direct upgrade returns [frm, to].
    """
    if frm == to:
        return [frm]
    if can_upgrade(m, frm, to):
        return [frm, to]

    # Build the reverse adjacency: version -> versions it can be upgraded FROM.
    from collections import deque
    rev = {}
    for v in versions(m):
        for src in upgrade_from(m, v):
            rev.setdefault(src, []).append(v)

    queue = deque([[frm]])
    seen = {frm}
    while queue:
        path = queue.popleft()
        last = path[-1]
        for nxt in rev.get(last, []):
            if nxt in seen:
                continue
            new_path = path + [nxt]
            if nxt == to:
                return new_path
            seen.add(nxt)
            queue.append(new_path)
    return None


def suggest_upgrade_steps(m: dict, frm: str, to: str) -> Optional[List[str]]:
    """Best-effort step list from `frm` to `to`.

    Preference order:
      1. A documented chain (`upgrade_path`): direct or multi-hop over real
         `upgrade_from` edges.
      2. Same major, no documented edge: a minor/patch bump within one release
         line is treated as an allowed step (release notes often omit it).
      3. Cross-major, no documented edge: step to the latest node of the current
         major, then follow the documented cross-major bridge (the release whose
         `upgrade_from` lists that latest).

    Steps from #2/#3 are inferred, not scraped; callers should say so. Returns
    None only when no chain (even inferred) can be produced.
    """
    p = upgrade_path(m, frm, to)
    if p:
        return p

    fa, ta = frm.split(".")[0], to.split(".")[0]
    if fa == ta:
        return [frm, to] if frm != to else None

    pivot = latest(m, fa)
    if not pivot:
        return None
    if frm == pivot:
        # Already at the latest of the current major and no documented path; no
        # cross-major bridge can be inferred from here.
        return None
    tail = upgrade_path(m, pivot, to)
    if not tail:
        return None  # the latest of the current major cannot bridge to `to`.
    return [frm, pivot] + tail[1:]


# --------------------------------------------------------------------------- staleness
def generated_at(m: dict) -> Optional[datetime]:
    raw = m.get("generated_at")
    if not raw:
        return None
    try:
        # Accept both naive and offset-aware ISO-8601.
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def age_days(m: dict, now: Optional[datetime] = None) -> Optional[int]:
    """Age of the manifest in whole days, or None when `generated_at` is absent."""
    g = generated_at(m)
    if g is None:
        return None
    now = now or datetime.now(timezone.utc)
    return (now - g).days


# --------------------------------------------------------------------------- CLI
def _cli(argv: List[str]) -> int:
    """Small CLI so bash scripts can query the manifest without duplicating logic.

    Usage:
      manifest.py latest [--major N]
      manifest.py components <version>
      manifest.py tarball-version <version> <component> [fallback]
      manifest.py can-upgrade <from> <to>
      manifest.py list-targets <current>
      manifest.py upgrade-path <from> <to>
      manifest.py age [--stale-days N]
      manifest.py path
    """
    args = argv[1:]
    if not args:
        return 2

    cmd = args[0]
    rest = args[1:]

    if cmd == "path":
        print(str(local_manifest_path()))
        return 0

    if cmd == "latest":
        major = None
        if "--major" in rest:
            major = rest[rest.index("--major") + 1]
        v = latest(load(), major)
        print(v if v else "")
        return 0 if v else 1

    if cmd == "components":
        if not rest:
            return 2
        print(json.dumps(components(load(), rest[0]), sort_keys=True))
        return 0

    if cmd == "tarball-version":
        if len(rest) < 2:
            return 2
        ver, comp = rest[0], rest[1]
        fallback = rest[2] if len(rest) > 2 else None
        v = tarball_version(load(), ver, comp, fallback)
        print(v if v else "")
        return 0 if v else 1

    if cmd == "can-upgrade":
        if len(rest) < 2:
            return 2
        ok = can_upgrade(load(), rest[0], rest[1])
        print("yes" if ok else "no")
        return 0 if ok else 1

    if cmd == "list-targets":
        if not rest:
            return 2
        print("\n".join(list_targets(load(), rest[0])))
        return 0

    if cmd == "upgrade-path":
        if len(rest) < 2:
            return 2
        p = upgrade_path(load(), rest[0], rest[1])
        if p is None:
            print("(none)")
            return 1
        print(" -> ".join(p))
        return 0

    if cmd == "suggest-steps":
        if len(rest) < 2:
            return 2
        p = suggest_upgrade_steps(load(), rest[0], rest[1])
        if p is None:
            print("(none)")
            return 1
        print(" -> ".join(p))
        return 0

    if cmd == "age":
        stale = DEFAULT_STALE_DAYS
        if "--stale-days" in rest:
            stale = int(rest[rest.index("--stale-days") + 1])
        days = age_days(load())
        if days is None:
            print("(unknown)")
            return 0
        print(days)
        return 1 if days > stale else 0

    return 2


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(_cli(sys.argv))