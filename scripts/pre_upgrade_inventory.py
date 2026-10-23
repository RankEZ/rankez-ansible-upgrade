import os
import sys
import argparse
import requests
from dotenv import load_dotenv
import urllib3

# Allow running the script directly from the repo dir.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rankez_common  # noqa: E402

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def resolve_bundle_dir(version, os_hint=None, bundle_dir=None):
    """Return an absolute path to the packages/ folder for `version`.

    Resolution order:
      1. --bundle-dir flag (explicit path to the packages/ folder), made absolute.
      2. Search CWD, ./scripts/, and the workspace bundles/ for
         `onebox-offline-<os>-v<version>/packages`.
    Returns (path, searched) or (None, searched) when not found.
    """
    searched = []
    if bundle_dir:
        p = os.path.abspath(bundle_dir)
        searched.append(p)
        return (p if os.path.isdir(p) else None), searched

    oss = [os_hint] if os_hint else ["rhel", "ubuntu"]
    roots = [os.getcwd(), os.path.join(os.getcwd(), "scripts")]
    workspace = os.environ.get(
        "RANKEZ_UPGRADE_HOME", os.path.expanduser("~/.rankez-upgrade"))
    roots.append(os.path.join(workspace, "bundles"))

    candidates = []
    for root in roots:
        for os_name in oss:
            c = os.path.join(root, f"onebox-offline-{os_name}-v{version}", "packages")
            searched.append(c)
            if os.path.isdir(c):
                candidates.append(os.path.abspath(c))
    return (candidates[0] if len(candidates) == 1 else None), searched

def main():
    parser = argparse.ArgumentParser(description="Auto-discover RankEZ components and generate Ansible inventory for upgrade.")
    parser.add_argument("--primary-vault", default="192.168.201.131", help="IP address of the primary vault")
    parser.add_argument("--standby-vault", default="192.168.201.132", help="IP address of the standby vault")
    parser.add_argument("--ssh-user", default="cloud-user", help="SSH user for Ansible")
    parser.add_argument("--output", default=None, help="Path to output the inventory file (default: workspace inventory/<name>.ini)")
    parser.add_argument("--name", default="inventory", help="Inventory name when writing to the workspace (default: inventory)")
    parser.add_argument("--version", default="5.9.0", help="Target RankEZ upgrade version (e.g., 5.9.0)")
    parser.add_argument("--os", choices=["rhel", "ubuntu"], help="Target OS of the bundle (rhel or ubuntu)")
    parser.add_argument("--bundle-dir", help="Explicit path to the packages/ folder (contains pam-*.tar.gz)")
    parser.add_argument("--install-path", default="/opt", help="Existing RankEZ installation base path (default: /opt)")
    parser.add_argument("--workspace", default=None, help="Workspace dir (default: $RANKEZ_UPGRADE_HOME or ~/.rankez-upgrade)")
    parser.add_argument("--env", default=None, help="Explicit .env file to load credentials from")
    args = parser.parse_args()

    if args.workspace:
        os.environ["RANKEZ_UPGRADE_HOME"] = args.workspace

    if not args.install_path.startswith("/") or any(char.isspace() for char in args.install_path):
        parser.error("--install-path must be an absolute path without whitespace")
    args.install_path = args.install_path.rstrip("/") or "/"

    env_file = rankez_common.locate_env(args.env)
    if env_file:
        load_dotenv(dotenv_path=str(env_file))
    else:
        load_dotenv()
    url = os.environ.get("RANKEZ_URL")
    username = os.environ.get("RANKEZ_USERNAME")
    password = os.environ.get("RANKEZ_PASSWORD")
    verify_ssl = os.environ.get("RANKEZ_VERIFY_SSL", "false").lower() == "true"

    if not all([url, username, password]):
        print("Error: RANKEZ_URL, RANKEZ_USERNAME, and RANKEZ_PASSWORD must be set in .env or environment variables.")
        exit(1)

    print(f"Connecting to {url} to discover components...")
    
    session = requests.Session()
    session.verify = verify_ssl
    
    login_url = f"{url}/api/auth/logon"
    try:
        response = session.post(login_url, json={"username": username, "password": password}, timeout=10)
        response.raise_for_status()
        token = response.json().get("token")
        if not token:
            print("Failed to parse token from login response.")
            exit(1)
        session.headers.update({"Authorization": token})
    except Exception as e:
        print(f"Login failed: {e}")
        exit(1)

    try:
        comp_url = f"{url}/api/system/component/status"
        comp_resp = session.get(comp_url, timeout=10)
        comp_resp.raise_for_status()
        components = comp_resp.json().get("components", {})
    except Exception as e:
        print(f"Failed to fetch components: {e}")
        exit(1)

    groups = {
        "pac": set(),
        "psm": set(),
        "cpm": set(),
        "cp": set(),
        "frontend": set()
    }
    
    def extract_ips(comp_list, group_name):
        for item in comp_list:
            ip = item.get("sourceIp") or item.get("hostname")
            if ip and ip != "127.0.0.1":
                groups[group_name].add(ip)
                groups["frontend"].add(ip)

    extract_ips(components.get("pacServers", []), "pac")
    extract_ips(components.get("psmServers", []), "psm")
    extract_ips(components.get("cpmServers", []), "cpm")
    extract_ips(components.get("credentialServers", []), "cp")
    
    print(f"\nDiscovered Components:")
    print(f"PAC Nodes: {', '.join(groups['pac']) or 'None'}")
    print(f"PSM Nodes: {', '.join(groups['psm']) or 'None'}")
    print(f"CPM Nodes: {', '.join(groups['cpm']) or 'None'}")
    print(f"CP Nodes:  {', '.join(groups['cp']) or 'None'}")

    local_bundle_dir, searched = resolve_bundle_dir(args.version, os_hint=args.os, bundle_dir=args.bundle_dir)
    if not local_bundle_dir:
        print("\nError: could not locate the packages/ folder for the upgrade bundle.", file=sys.stderr)
        print(f"Searched for onebox-offline-<os>-v{args.version}/packages in:", file=sys.stderr)
        for s in searched:
            print(f"  - {s}", file=sys.stderr)
        print("\nFix: run ./scripts/prep_offline_bundle.sh (or ./rankez-upgrade download) "
              "to generate the bundle, then pass --bundle-dir <path-to-packages> "
              "or set --os <rhel|ubuntu>.", file=sys.stderr)
        exit(1)
    print(f"\nUsing bundle packages dir: {local_bundle_dir}")

    inventory_content = f"""# Automatically generated RankEZ upgrade inventory

[primary_vault]
{args.primary_vault} ansible_user={args.ssh_user}

[standby_vault]
{args.standby_vault} ansible_user={args.ssh_user}

[vaults:children]
primary_vault
standby_vault

[pac]
"""
    for ip in groups["pac"]: inventory_content += f"{ip} ansible_user={args.ssh_user}\n"

    inventory_content += "\n[psm]\n"
    for ip in groups["psm"]: inventory_content += f"{ip} ansible_user={args.ssh_user}\n"

    inventory_content += "\n[cpm]\n"
    for ip in groups["cpm"]: inventory_content += f"{ip} ansible_user={args.ssh_user}\n"

    inventory_content += "\n[cp]\n"
    for ip in groups["cp"]: inventory_content += f"{ip} ansible_user={args.ssh_user}\n"

    inventory_content += "\n[frontend:children]\npac\npsm\ncpm\ncp\n"

    inventory_content += f"""
[all:vars]
ansible_ssh_common_args='-o StrictHostKeyChecking=no'
ansible_become=yes
upgrade_version={args.version}
local_bundle_dir={local_bundle_dir}
remote_tmp_dir=/tmp/rankez_upgrade
install_path={args.install_path}
"""

    if args.output:
        output_path = args.output
    else:
        rankez_common.ensure_workspace()
        output_path = str(rankez_common.workspace_dir() / "inventory" / f"{args.name}.ini")

    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(inventory_content)

    # Remember last-used values for the next run.
    rankez_common.save_state({
        "ssh_user": args.ssh_user,
        "version": args.version,
        "os": args.os,
        "install_path": args.install_path,
        "bundle_dir": local_bundle_dir,
        "inventory": output_path,
    })

    print(f"\nInventory successfully generated at {output_path}")
    print(f"Run: ansible-playbook -i {output_path} upgrade.yml")

if __name__ == "__main__":
    main()
