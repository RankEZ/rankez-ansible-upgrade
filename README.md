# RankEZ Automated Upgrade Framework

This directory contains a fully self-contained automation framework to download, provision, and upgrade a RankEZ environment using Ansible. It supports Disaster Recovery (DR) Vaults and distributed or co-located front-end components (PAC, PSM, CPM, CP).

> ⚠️ **Disclaimer: Beta Project**
> This repository is currently a **Beta** project and has **not been fully tested** across all possible infrastructure combinations. Manual tweaks and configurations may be required to fit your specific environment.
> 
> **Currently Tested & Verified Environment:**
> * **Active-Standby Vault** with one **PAC**, one **PSM**, and one **CPM**.
> 
> Use with caution in production environments. Always back up your data and test thoroughly in a staging environment first.


## Directory Structure
- `upgrade.yml`: The main Ansible playbook orchestrating the upgrade.
- `roles/`: Ansible roles for preflight checks, graceful shutdown, package transfer, and component upgrades.
- `scripts/prep_offline_bundle.sh`: Downloads the RankEZ tarballs securely using Zendesk authentication.
- `scripts/pre_upgrade_inventory.py`: Queries the RankEZ API to auto-discover component IPs and generate the `inventory.ini` file.

## Prerequisites

1. **Python Environment**: Ensure you have a Python environment with the required dependencies:
   ```bash
   pip install requests python-dotenv ansible
   ```
2. **Target Host Tools**: Every upgrade target must have Docker Engine 26 or newer, the Docker Buildx plugin, and the Docker Compose plugin available to the Ansible SSH user (with privilege escalation). Preflight checks these before stopping any services.
3. **Configuration (.env)**: Create a `.env` file in the `ansible/scripts/` directory with your RankEZ portal credentials (for auto-discovery) and Zendesk credentials (for downloading packages).
   ```env
   RANKEZ_URL=https://192.168.0.21
   RANKEZ_USERNAME=admin
   RANKEZ_PASSWORD=YourPasswordHere!
   RANKEZ_VERIFY_SSL=false
   
   ZENDESK_EMAIL=your_zendesk_email@example.com
   ZENDESK_PASSWORD=your_zendesk_password
   ```

---

## Step 1: Download the Offline Bundle

Before running Ansible, download the upgrade packages securely using the included script. This will download the massive tarballs locally before Ansible pushes them to the target VMs.

Navigate to the `ansible/` directory and run:
```bash
./scripts/prep_offline_bundle.sh
```
*Follow the prompts to select your OS and version. The packages will be saved to `ansible/scripts/onebox-offline-rhel-v<version>/packages/`.*

---

## Step 2: Auto-Discover Components & Generate Inventory

Instead of manually crafting the Ansible inventory, use the pre-upgrade Python script to dynamically query the RankEZ API. 

From the `ansible/` directory, run:
```bash
python scripts/pre_upgrade_inventory.py \
  --primary-vault 192.168.0.11 \
  --standby-vault 192.168.0.12 \
  --ssh-user cloud-user \
  --version 5.9.0
```

For installations outside `/opt`, pass the existing installation base directory with `--install-path` (the default is `/opt`):
```bash
python scripts/pre_upgrade_inventory.py --install-path /data --primary-vault 192.168.0.11 --standby-vault 192.168.0.12 --ssh-user cloud-user --version 5.9.0
```
The generated inventory includes `install_path=/data`; Ansible applies it to each component's `INSTALL_PATH` in `install.conf` before running the upgrade, and uses it for component shutdown and CP custom-configuration backup/restore. For a manually maintained inventory, set `install_path` under `[all:vars]`.

**What this does:**
1. Logs into the RankEZ API.
2. Identifies the IP addresses of all active PAC, PSM, CPM, and CP nodes.
3. Automatically generates the `inventory.ini` file, categorizing every node into the correct Ansible groups and pointing the `local_bundle_dir` to the packages downloaded in Step 1.

---

## Step 3: Execute the Upgrade

From the `ansible/` directory, verify the generated inventory:
```bash
cat inventory.ini
```

If it looks correct, execute the master upgrade playbook:
```bash
ansible-playbook -i inventory.ini upgrade.yml
```

### 🛑 IMPORTANT: VM Snapshot Pause
During the `Preflight` phase, the automation will stop the `dr-manager` service on both vaults. **The playbook will pause.** At this moment, take VM snapshots of all nodes via your Hypervisor, then press **Enter** to resume the upgrade.

---

## Playbook Workflow (What Happens Automatically)

1. **Preflight Checks**: Verifies Docker version (>=26), Docker Buildx (`docker buildx version`), and Docker Compose (`docker compose version`) are available on every target before pausing for snapshots. Install the Docker Buildx and Compose plugins on hosts where either check fails; the upgrade is stopped before service shutdown if a prerequisite is missing.
2. **Graceful Shutdown**: Stops all frontend components (CPM, PSM, PAC) in the safe sequence, followed by stopping the Vault services.
3. **Package Transfer**: Intelligently copies *only* the required `.tar.gz` packages to `/tmp/rankez_upgrade` on the specific target VMs.
4. **Vault Upgrades**: Upgrades Primary Vault, then Standby Vault. Validates DR status via `docker exec dr-manager dr-control show-status`.
5. **Frontend Upgrades**: Sequentially upgrades PAC, PSM, and CPM. (Note: PSM uses `async` to prevent SSH timeouts during heavy Docker image loading).
6. **Credential Provider (CP) Upgrade**: Backs up custom configs, upgrades CP, and restores configs seamlessly.