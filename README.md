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
| `download`   | Download the offline upgrade bundle (picks OS/version interactively). |
| `inventory`  | Auto-discover components via the RankEZ API and generate inventory.  |
| `check`      | Connectivity / sudo / docker checks (read-only, no target changes).   |
| `upgrade`    | Run `check`, then `ansible-playbook` with the generated inventory.    |
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
ansible, docker, and curl.

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