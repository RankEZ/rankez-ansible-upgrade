import os
import argparse
import requests
from dotenv import load_dotenv
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def main():
    parser = argparse.ArgumentParser(description="Auto-discover RankEZ components and generate Ansible inventory for upgrade.")
    parser.add_argument("--primary-vault", default="192.168.201.131", help="IP address of the primary vault")
    parser.add_argument("--standby-vault", default="192.168.201.132", help="IP address of the standby vault")
    parser.add_argument("--ssh-user", default="cloud-user", help="SSH user for Ansible")
    parser.add_argument("--output", default="inventory.ini", help="Path to output the inventory file")
    parser.add_argument("--version", default="5.9.0", help="Target RankEZ upgrade version (e.g., 5.9.0)")
    args = parser.parse_args()

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
    
    print("\nDiscovered Components:")
    print(f"PAC Nodes: {', '.join(groups['pac']) or 'None'}")
    print(f"PSM Nodes: {', '.join(groups['psm']) or 'None'}")
    print(f"CPM Nodes: {', '.join(groups['cpm']) or 'None'}")
    print(f"CP Nodes:  {', '.join(groups['cp']) or 'None'}")

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
local_bundle_dir=scripts/onebox-offline-rhel-v{args.version}/packages
remote_tmp_dir=/tmp/rankez_upgrade
"""

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        f.write(inventory_content)
    
    print(f"\nInventory successfully generated at {args.output}")

if __name__ == "__main__":
    main()
