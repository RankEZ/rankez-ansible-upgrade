# RankEZ Automated Upgrade Framework

A self-contained automation framework to download, provision, and upgrade a RankEZ
environment using Ansible. Supports Disaster Recovery (DR) Vaults and distributed or
co-located front-end components (PAC, PSM, CPM, CP).

> ⚠️ **Disclaimer: Beta Project**
> This repository is **Beta** and has **not been fully tested** across every
> infrastructure combination. Manual tweaks may be required for your environment.
>
> **Tested & verified:** Active-Standby Vault with one PAC, one PSM, and one CPM.
>
> Use with caution in production. Always back up your data and test in staging first.

---

## Quick Start

```bash
git clone <repo> && cd rankez-ansible-upgrade
pip install -r requirements.txt

./rankez-upgrade all
```

`all` runs a guided wizard: `setup` → `download` → `inventory` → `check` → `upgrade`,
pausing for confirmation before anything is stopped on the targets.

The single entry point (`./rankez-upgrade`) has these subcommands (each also works
standalone — see *Advanced* below):

| Command      | What it does                                                        |
|--------------|---------------------------------------------------------------------|
| `setup`      | Create the workspace and `.env` (hidden password prompt, chmod 600). |
| `download`   | Download the offline upgrade bundle(s). `--plan` fetches every hop.  |
| `inventory`  | Auto-discover components via the RankEZ API and generate inventory.  |
| `plan`       | Compute the upgrade route (direct / multi-hop / inferred).           |
| `check`      | Connectivity / sudo / docker + version-detection checks (read-only). |
| `upgrade`    | Run one hop; `--auto-hops` runs a full multi-hop chain.              |
| `all`        | Guided end-to-end wizard.                                            |
| `doctor`     | Print resolved paths and verify prerequisites.                       |

Run `./rankez-upgrade --help` for usage.

---

## Where do files live

State is **not** stored in the repo. A fresh clone or `git pull` never forces you to
move credentials or tarballs around.

```
~/.rankez-upgrade/                     (RANKEZ_UPGRADE_HOME, default)
  .env                                 credentials (chmod 600)
  bundles/<os>-v<ver>/packages/*.tar.gz   downloaded packages (shared across clones)
  inventory/<name>.ini                 generated inventories
  logs/ansible.log                     playbook logs
  state.json                           last-used version, os, ssh user, bundle dir, ...
```

Override the location with the `RANKEZ_UPGRADE_HOME` environment variable or the
`--workspace` flag. `.env` resolution order: CLI flag → `./.env` in CWD (legacy, warned)
→ workspace `.env`.

Run `./rankez-upgrade doctor` to print the resolved paths and verify python deps,
ansible, docker, and curl. It also reports the release manifest's `generated_at`
and age, warning if it is older than ~90 days.

---

## Release manifest & upgrade paths

Version resolution and safe-upgrade checks are driven by a committed manifest at
`manifest/releases.json` (schema v1), rather than a date-based guess:

```json
{
  "schema_version": 1,
  "generated_at": "2026-10-23T12:00:00Z",
  "releases": {
    "5.10.0": {
      "major": "5",
      "components": {"vault": "5.10.0", "pac": "5.10.0", "psm": "5.10.0",
                     "cpm": "5.10.0"},
      "extra": {"remoteapp": "5.9.0", "utility": "5.9.0"},
      "upgrade_from": ["5.6.0", "5.7.0", "5.8.0", "5.9.0"]
    }
  },
  "latest_by_major": {"5": "5.10.0"}
}
```

What this changes for you:

* **`download`** — `--latest` (and the interactive default) resolve to the
  manifest's latest version, not `(year - 2021).(month).0`. Each component's own
  version is used for its tarball (`pam-vault-…`, `pam-pac-…`, …), so a release
  where components differ (e.g. core 5.10.0 but a bundled utility 5.9.0) is
  handled correctly.
* **`upgrade`** — before stopping anything, the tool compares the installed
  version (discovered by `inventory`) with the target against the manifest's
  `upgrade_from`. An unsupported direct path blocks with a clear message and the
  recommended step chain (e.g. `4.12.0 -> 5.6.0 -> 5.10.0`). Override with
  `./rankez-upgrade upgrade --force`.
* **`doctor`** — reports the manifest path, `generated_at`, age, and latest
  version, warning when the manifest is stale.
* A version not present in the manifest still works: you are warned and may
  enter it manually — it never crashes.

The manifest is updated by `git pull` (it is a tracked file in this repo). The
scraper below is how the *maintainer* regenerates it.

---

## Upgrade paths (multi-hop)

A release's `upgrade_from` may not include your installed version (e.g. 5.10.0
only accepts 5.6.0+). The framework computes the full chain and, by default,
**advises** rather than leaping across hops.

`./rankez-upgrade plan --current 4.11.0` prints the route:

```
4.11.0 -> 4.12.0   (INFERRED - not in release notes)
4.12.0 -> 5.6.0
5.6.0  -> 5.10.0
mode=inferred  hops=3  inferred=1
```

| Situation                     | What happens                                          |
|-------------------------------|-------------------------------------------------------|
| Direct (`5.8.0 -> 5.10.0`)    | `upgrade` runs the single hop normally.               |
| Documented multi-hop          | `upgrade` stops (exit 3) and prints the chain.        |
| Inferred hop present          | `--allow-inferred` required before any auto-run.      |
| Downgrade / same / unknown    | Blocked with a clear message.                         |

Run a multi-hop upgrade:

```bash
./rankez-upgrade plan --current 4.11.0   # review the route
./rankez-upgrade download --plan         # fetch every hop's bundle (offline)
./rankez-upgrade upgrade --auto-hops     # one playbook run per hop, with confirmation
```

Flags:

* `--auto-hops` — run the full chain (one `ansible-playbook` per hop).
* `--allow-inferred` — permit inferred (non-release-note) hops during `--auto-hops`.
* `--current-version X.Y.Z` — override the detected installed version.
* `--target X.Y.Z` — explicit target (default: latest).
* `--force` — bypass the safe-path gate for a single hop.

Every hop bundle is verified up front; progress is recorded in `state.json`
(`completed_hops`), so a failed run resumes with `upgrade --auto-hops`.

---

## Maintainer: refreshing the manifest

The manifest is generated **locally** (no CI, no GitLab) from the RankEZ Feishu
release notes. There is no automated refresh — a human reviews the diff before
anything is published.

```bash
python3 tools/fetch_release_manifest.py            # full matrix
python3 tools/fetch_release_manifest.py --only 5.11.0   # one release
python3 tools/fetch_release_manifest.py --dry-run       # print diff, write nothing
git diff manifest/releases.json                    # review
git add manifest tools && git commit -m "chore: refresh release manifest" && git push
```

* The scraper drives the `agent-browser` binary (Feishu is a JS SPA; `curl`
  returns an empty shell). Install with `npm i -g agent-browser`, or set `AGENT`
  to an existing binary.
* Feishu node tokens live in `tools/feishu_tokens.json` (an override for when
  index-page discovery fails). These are public wiki page tokens, **not**
  credentials — but keep credentials and internal URLs out of this repo.
* Hand-corrected releases go in `manifest/overrides.json` (set `"manual": true`);
  they survive the next scrape.
* The scraper refuses to write when validation fails: a release missing a
  `vault` component, an `upgrade_from` naming an unknown version, or a release
  count that drops vs. the existing manifest (guards against a silent partial
  scrape).

---

## The upgrade (what happens automatically)

1. **Verify bundle** — assert every required tarball exists locally *before* anything is stopped.
2. **Plan & confirm** — print the planned order/hosts/versions and ask you to type the
   target version to continue.
3. **Preflight** — check docker >=26, buildx, compose, install path, disk space; then
   pause so you can take **VM snapshots**.
4. **Graceful shutdown** — stop frontend components (CPM, PSM, PAC), then Vault.
5. **Transfer packages** — copy only the needed `.tar.gz` files to `/tmp/rankez_upgrade`.
6. **Upgrade vaults** — primary, then standby (validates DR status).
7. **Upgrade frontend** — PAC, PSM (async), CPM.
8. **Upgrade CP** — back up custom configs, upgrade, restore.

---

## Troubleshooting

**Wrong SSH user** — `./rankez-upgrade check` prints which host/user failed and suggests
likely users (`cloud-user`, `ec2-user`, `root`, `ubuntu`, `opc`, `rocky`). Re-generate the
inventory with the correct user:
```bash
./rankez-upgrade inventory --ssh-user <user>
```

**Sudo (become) fails** — re-run the playbook prompted for the become password (`-K`).

**Docker / buildx / compose missing** — `check` reports these per host; install the
Docker plugins on the target before continuing.

**Missing tarball error** — the playbook asserts package existence up front. Run
`./rankez-upgrade download --version <ver> --os rhel|ubuntu` to regenerate the bundle,
or pass `--bundle-dir` to point at the `packages/` folder.

**`check` not recent** — `upgrade` refuses to run unless a check passed in the last 60
minutes (marker in `state.json`). Re-run `./rankez-upgrade check`, or `--skip-check` to bypass.

**Rollback** — there is no in-tool rollback; restore from the VM snapshots taken during
preflight.

---

## Advanced (manual steps)

Each subcommand maps to a standalone script:

```bash
# Download (non-interactive flags available)
./scripts/prep_offline_bundle.sh --os rhel --version 5.9.0 --with-cp --packages-only

# Generate inventory (auto-discovers vault IPs unless overridden)
python scripts/pre_upgrade_inventory.py \
  --primary-vault 192.168.0.11 --standby-vault 192.168.0.12 \
  --ssh-user cloud-user --version 5.9.0

# Check (read-only)
./scripts/check_access.sh <inventory.ini>

# Upgrade
ansible-playbook -i ~/.rankez-upgrade/inventory/inventory.ini upgrade.yml
```

For installations outside `/opt`, pass `--install-path /data`; Ansible applies it to each
component's `INSTALL_PATH` and uses it for shutdown and CP config backup/restore.

### Resuming a failed run
`ansible-playbook` supports `--start-at-task="..."`. Snapshot the VMs at the end of
preflight, so a failed run can be rolled back and resumed from a known-good point.