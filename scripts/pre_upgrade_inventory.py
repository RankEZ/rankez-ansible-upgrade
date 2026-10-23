import os
import re
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
    workspace = rankez_common.workspace_dir()
    roots.append(str(workspace / "bundles"))

    candidates = []
    for root in roots:
        for os_name in oss:
            c = os.path.join(root, f"onebox-offline-{os_name}-v{version}", "packages")
            searched.append(c)
            if os.path.isdir(c):
                candidates.append(os.path.abspath(c))
    if len(candidates) == 1:
        return candidates[0], searched
    if len(candidates) > 1:
        return "MULTIPLE", searched
    return None, searched


def parse_version(v):
    """Return a tuple of ints (major, minor, patch) from a version string."""
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", v or "")
    if not m:
        return (0, 0, 0)
    return tuple(int(x) for x in m.groups() if x is not None)


def find_vault_ips(components):
    """Try to discover vault IPs from the component status payload.

    Checks common keys for vault / DR nodes. Returns (primary, standby) where
    each may be None if not discoverable.
    """
    def ips_from(items):
        out = set()
        for it in items or []:
            if isinstance(it, dict):
                ip = it.get("sourceIp") or it.get("hostname") or it.get("ip")
                if ip and ip != "127.0.0.1":
                    out.add(ip)
            elif isinstance(it, str):
                if it and it != "127.0.0.1":
                    out.add(it)
        return sorted(out)

    primary = standby = None
    vault_candidates = []
    for key in ("vault", "vaults", "vaultServers", "primaryVault", "standbyVault",
                "dr", "drServers", "drVaults"):
        val = components.get(key)
        if val is None:
            continue
        if isinstance(val, dict):
            for sub in ("primary", "standby"):
                sv = val.get(sub)
                if isinstance(sv, dict):
                    ip = sv.get("sourceIp") or sv.get("hostname") or sv.get("ip")
                    if ip:
                        if sub == "primary":
                            primary = ip
                        elif sub == "standby":
                            standby = ip
            vault_candidates.extend(ips_from(val.get("servers") or []))
        else:
            vault_candidates.extend(ips_from(val))

    if not primary and not standby and vault_candidates:
        primary = vault_candidates[0]
        if len(vault_candidates) > 1:
            standby = vault_candidates[1]
    return primary, standby


def discover_installed_version(components):
    """Try to read the currently installed version from the payload, else None."""
    for key in ("version", "currentVersion", "installedVersion", "buildVersion"):
        v = components.get(key)
        if isinstance(v, str):
            return v
    return None


def prompt(prompt_text, default=None):
    """Prompt only when stdin is a TTY; otherwise return default (or None)."""
    if not sys.stdin.isatty():
        return default
    v = input(prompt_text)
    return v.strip() or default


def main():
    parser = argparse.ArgumentParser(
        description="Auto-discover RankEZ components and generate Ansible inventory for upgrade.")
    parser.add_argument("--primary-vault", default=None,
                        help="IP address of the primary vault (auto-discovered if omitted)")
    parser.add_argument("--standby-vault", default=None,
                        help="IP address of the standby vault (auto-discovered if omitted)")
    parser.add_argument("--ssh-user", default=None, help="SSH user for Ansible")
    parser.add_argument("--ssh-key", default=None, help="Path to SSH private key file")
    parser.add_argument("--ssh-port", default=None, help="SSH port")
    parser.add_argument("--output", default=None,
                        help="Path to output the inventory file (default: workspace inventory/<name>.ini)")
    parser.add_argument("--name", default="inventory",
                        help="Inventory name when writing to the workspace (default: inventory)")
    parser.add_argument("--version", default=None,
                        help="Target RankEZ upgrade version (default: state.json, else newest bundle)")
    parser.add_argument("--os", choices=["rhel", "ubuntu"], help="Target OS of the bundle (rhel or ubuntu)")
    parser.add_argument("--bundle-dir", help="Explicit path to the packages/ folder (contains pam-*.tar.gz)")
    parser.add_argument("--install-path", default=None,
                        help="Existing RankEZ installation base path (default: /opt)")
    parser.add_argument("--workspace", default=None,
                        help="Workspace dir (default: $RANKEZ_UPGRADE_HOME or ~/.rankez-upgrade)")
    parser.add_argument("--env", default=None, help="Explicit .env file to load credentials from")
    args = parser.parse_args()

    if args.workspace:
        os.environ["RANKEZ_UPGRADE_HOME"] = args.workspace

    state = rankez_common.load_state()

    # Resolve ssh user / install path from defaults, then state.
    ssh_user = args.ssh_user or state.get("ssh_user") or "cloud-user"
    install_path = args.install_path or state.get("install_path") or "/opt"
    if not install_path.startswith("/") or any(c.isspace() for c in install_path):
        parser.error("--install-path must be an absolute path without whitespace")
    install_path = install_path.rstrip("/") or "/"

    # Resolve target version: flag > state.json > newest bundle dir.
    version = args.version or state.get("version")
    if not version:
        bundles = rankez_common.workspace_dir() / "bundles"
        if bundles.is_dir():
            dirs = [d.name for d in bundles.iterdir() if d.is_dir()]
            vs = [(d, parse_version(d)) for d in dirs]
            vs = sorted(vs, key=lambda t: t[1], reverse=True)
            if vs:
                version = _ver_from_dirname(vs[0][0]) or vs[0][0]
        if not version:
            print("Error: no target version given and none recorded. "
                  "Run ./rankez-upgrade download first, or pass --version.", file=sys.stderr)
            exit(1)

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
        print("Error: RANKEZ_URL, RANKEZ_USERNAME, and RANKEZ_PASSWORD must be set in .env or environment.")
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

    groups = {"pac": set(), "psm": set(), "cpm": set(), "cp": set(), "frontend": set()}

    def extract_ips(comp_list, group_name):
        for item in comp_list:
            ip = item.get("sourceIp") or item.get("hostname") or item.get("ip")
            if ip and ip != "127.0.0.1":
                groups[group_name].add(ip)
                groups["frontend"].add(ip)

    extract_ips(components.get("pacServers", []), "pac")
    extract_ips(components.get("psmServers", []), "psm")
    extract_ips(components.get("cpmServers", []), "cpm")
    extract_ips(components.get("credentialServers", []), "cp")

    print("\nDiscovered Components:")
    print(f"PAC Nodes: {', '.join(sorted(groups['pac'])) or 'None'}")
    print(f"PSM Nodes: {', '.join(sorted(groups['psm'])) or 'None'}")
    print(f"CPM Nodes: {', '.join(sorted(groups['cpm'])) or 'None'}")
    print(f"CP Nodes:  {', '.join(sorted(groups['cp'])) or 'None'}")

    # Auto-discover vault IPs; fall back to flags / prompt.
    primary_vault = args.primary_vault
    standby_vault = args.standby_vault
    if not primary_vault or not standby_vault:
        disc_primary, disc_standby = find_vault_ips(components)
        primary_vault = primary_vault or disc_primary
        standby_vault = standby_vault or disc_standby
    if not primary_vault:
        primary_vault = prompt("Primary vault IP: ")
    if not primary_vault:
        print("Error: primary vault IP not provided and not discoverable. "
              "Pass --primary-vault <ip>.", file=sys.stderr)
        exit(1)
    if not standby_vault:
        standby_vault = prompt("Standby vault IP (enter to skip): ")
    print(f"\nVaults: primary={primary_vault}, standby={standby_vault or '(none)'}")

    # Version sanity: warn on downgrade / same / major skip.
    installed = discover_installed_version(components)
    if installed:
        cur = parse_version(installed)
        tgt = parse_version(version)
        if tgt < cur:
            print(f"WARNING: target {version} is older than installed {installed} (downgrade).")
        elif tgt == cur:
            print(f"WARNING: target {version} equals installed {installed} (no-op upgrade).")
        elif tgt[0] - cur[0] > 1:
            print(f"WARNING: skipping major version(s) {installed} -> {version}.")

    # Resolve bundle dir; then verify required tarballs against discovered groups.
    local_bundle_dir, searched = resolve_bundle_dir(version, os_hint=args.os, bundle_dir=args.bundle_dir)
    if local_bundle_dir == "MULTIPLE":
        print("\nError: multiple bundle candidates found. Pass --bundle-dir to disambiguate.", file=sys.stderr)
        for s in searched:
            print(f"  - {s}", file=sys.stderr)
        exit(1)
    if not local_bundle_dir:
        print("\nError: could not locate the packages/ folder for the upgrade bundle.", file=sys.stderr)
        print(f"Searched for onebox-offline-<os>-v{version}/packages in:", file=sys.stderr)
        for s in searched:
            print(f"  - {s}", file=sys.stderr)
        print("\nFix: run ./rankez-upgrade download, or pass --bundle-dir <path-to-packages> "
              "and --os <rhel|ubuntu>.", file=sys.stderr)
        exit(1)
    print(f"\nUsing bundle packages dir: {local_bundle_dir}")

    required = ["vault"]
    if groups["pac"]:
        required.append("pac")
    if groups["psm"]:
        required.append("psm")
    if groups["cpm"]:
        required.append("cpm")
    if groups["cp"]:
        required.append("cp")

    missing = []
    for name in required:
        p = os.path.join(local_bundle_dir, f"pam-{name}-{version}.tar.gz")
        if not os.path.isfile(p):
            missing.append(p)
    if missing:
        print("\nError: the following required tarballs are missing from the bundle:", file=sys.stderr)
        for p in missing:
            print(f"  - {p}", file=sys.stderr)
        print(f"\nFix: ./rankez-upgrade download --version {version} "
              f"--os {args.os or 'rhel'} --with-cp" if groups["cp"] else
              f"\nFix: ./rankez-upgrade download --version {version} --os {args.os or 'rhel'}")
        exit(1)

    # Build the SSH connection vars.
    ssh_common = "'-o StrictHostKeyChecking=accept-new'"
    ssh_user_var = f"ansible_user={ssh_user}"
    extra_vars = ""
    if args.ssh_key:
        extra_vars += f"ansible_ssh_private_key_file={args.ssh_key}\n"
    if args.ssh_port:
        extra_vars += f"ansible_port={args.ssh_port}\n"

    inventory_content = f"# Automatically generated RankEZ upgrade inventory\n\n"
    inventory_content += f"[primary_vault]\n{primary_vault} {ssh_user_var}\n\n"
    if standby_vault:
        inventory_content += f"[standby_vault]\n{standby_vault} {ssh_user_var}\n\n"
    inventory_content += "[vaults:children]\nprimary_vault\nstandby_vault\n\n"

    inventory_content += "[pac]\n"
    for ip in sorted(groups["pac"]):
        inventory_content += f"{ip} {ssh_user_var}\n"

    inventory_content += "\n[psm]\n"
    for ip in sorted(groups["psm"]):
        inventory_content += f"{ip} {ssh_user_var}\n"

    inventory_content += "\n[cpm]\n"
    for ip in sorted(groups["cpm"]):
        inventory_content += f"{ip} {ssh_user_var}\n"

    inventory_content += "\n[cp]\n"
    for ip in sorted(groups["cp"]):
        inventory_content += f"{ip} {ssh_user_var}\n"

    inventory_content += "\n[frontend:children]\npac\npsm\ncpm\ncp\n"

    inventory_content += f"""
[all:vars]
ansible_ssh_common_args={ssh_common}
ansible_become=yes
upgrade_version={version}
local_bundle_dir={local_bundle_dir}
remote_tmp_dir=/tmp/rankez_upgrade
install_path={install_path}
{extra_vars}"""

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

    rankez_common.save_state({
        "ssh_user": ssh_user,
        "version": version,
        "os": args.os,
        "install_path": install_path,
        "bundle_dir": local_bundle_dir,
        "inventory": output_path,
    })

    print(f"\nInventory successfully generated at {output_path}")
    print(f"Run: ansible-playbook -i {output_path} upgrade.yml")


def _ver_from_dirname(dirname):
    m = re.search(r"v(\d+\.\d+(?:\.\d+)?)", dirname)
    return m.group(1) if m else None


if __name__ == "__main__":
    main()