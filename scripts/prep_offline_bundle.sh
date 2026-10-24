#!/bin/bash
# prep_offline_bundle.sh
# Offline Package Preparation Tool (Runs on macOS / Linux)
# Last updated: 2026-09-01

set -e

echo "=================================================================="
echo "          RankEZ Onebox Offline Bundle Generator                  "
echo "=================================================================="
echo ""

# 0.2 Load .env File & Check Credentials
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Work from the repository root so bundle output does not depend on the caller's CWD.
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT" || exit 1

# Shared path/env resolution (mirrors scripts/rankez_common.py).
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib/common.sh"

# Release manifest query helper (see scripts/lib/manifest.py). Falls back to the
# legacy date-based guess when the manifest is absent or the lookup fails.
MANIFEST_PY="$SCRIPT_DIR/lib/manifest.py"

if ! rankez_load_env; then
    echo "Note: no .env found (checked workspace, CWD). Zendesk creds must be in env." >&2
fi

# Allow --help before credential checks.
case " $* " in
    *" -h "*|*" --help "*) 
        echo "Usage: $0 [--os rhel|ubuntu] [--version X.Y.Z|latest] [--with-cp]"
        echo "             [--packages-only] [--yes] [--workspace DIR] [--output-dir DIR]"
        exit 0 ;;
esac

if [ -z "$ZENDESK_EMAIL" ] || [ -z "$ZENDESK_PASSWORD" ]; then
    echo "Error: Missing Zendesk credentials." >&2
    echo "Please define ZENDESK_EMAIL and ZENDESK_PASSWORD in a .env file or set them as environment variables." >&2
    exit 1
fi

DOWNLOAD_BASE_URL="https://download.rankez.co/product/pam"

# --- Parse flags -------------------------------------------------------------
TARGET_OS=""
PAM_VERSION=""
INSTALL_CP=false
PACKAGES_ONLY=false
YES=false
WORKSPACE=""
OUTPUT_DIR=""
while [ $# -gt 0 ]; do
    case "$1" in
        --os) TARGET_OS="$2"; shift 2 ;;
        --version) PAM_VERSION="$2"; shift 2 ;;
        --with-cp) INSTALL_CP=true; shift ;;
        --packages-only) PACKAGES_ONLY=true; shift ;;
        --yes|-y) YES=true; shift ;;
        --latest) PAM_VERSION="latest"; shift ;;
        --workspace) WORKSPACE="$2"; shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: $0 [--os rhel|ubuntu] [--version X.Y.Z|latest] [--with-cp]"
            echo "             [--packages-only] [--yes] [--workspace DIR] [--output-dir DIR]"
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [ -n "$WORKSPACE" ]; then
    export RANKEZ_UPGRADE_HOME="$WORKSPACE"
fi
WS="$(rankez_workspace_ensure)"

# 1. Select Target OS
if [ -z "$TARGET_OS" ]; then
    echo "Select Target Operating System:"
    echo "  1) Ubuntu 22.04 / 24.04 / 26.04 (Debian-based)"
    echo "  2) RHEL 9 / Oracle Linux 9 / Rocky 9 (RHEL-based)"
    read -p "Enter choice [1-2]: " OS_CHOICE

    case "$OS_CHOICE" in
        1) TARGET_OS="ubuntu"; DOCKER_IMG="ubuntu:26.04" ;;
        2) TARGET_OS="rhel"; DOCKER_IMG="oraclelinux:9" ;;
        *) echo "Invalid choice. Exiting."; exit 1 ;;
    esac
else
    case "$TARGET_OS" in
        ubuntu) DOCKER_IMG="ubuntu:26.04" ;;
        rhel) DOCKER_IMG="oraclelinux:9" ;;
        *) echo "Invalid --os value: $TARGET_OS (expected rhel|ubuntu). Exiting." >&2; exit 1 ;;
    esac
fi

# 2. Input PAM Version (resolved against the release manifest)
CURRENT_YEAR=$(date +%Y)
CURRENT_MONTH=$((10#$(date +%m)))
CALC_MAJOR=$((CURRENT_YEAR - 2021))
FALLBACK_VERSION="${CALC_MAJOR}.${CURRENT_MONTH}.0"
MANIFEST_LATEST="$(python3 "$MANIFEST_PY" latest 2>/dev/null || true)"

if [ -z "$PAM_VERSION" ]; then
    DEFAULT_VERSION="${MANIFEST_LATEST:-$FALLBACK_VERSION}"
    read -p "Enter PAM Version to bundle [$DEFAULT_VERSION]: " USER_VERSION
    PAM_VERSION=${USER_VERSION:-$DEFAULT_VERSION}
elif [ "$PAM_VERSION" = "latest" ]; then
    PAM_VERSION="${MANIFEST_LATEST:-$FALLBACK_VERSION}"
    echo "[+] --latest resolved to $PAM_VERSION"
fi

# 3. Include Credential Provider (CP)?
if [ "$INSTALL_CP" = false ] && [ "$YES" = false ]; then
    read -p "Include Credential Provider (CP) package? [y/N]: " CP_CONFIRM
    case "$CP_CONFIRM" in
        [yY][eE][sS]|[yY]) INSTALL_CP=true ;;
        *) INSTALL_CP=false ;;
    esac
fi

# Bundle output location: workspace bundles/ unless --output-dir given.
if [ -n "$OUTPUT_DIR" ]; then
    BUNDLE_ROOT="$OUTPUT_DIR"
    mkdir -p "$BUNDLE_ROOT"
else
    BUNDLE_ROOT="$WS/bundles"
fi
BUNDLE_DIR="$BUNDLE_ROOT/onebox-offline-${TARGET_OS}-v${PAM_VERSION}"
mkdir -p "${BUNDLE_DIR}/packages"
[ "$PACKAGES_ONLY" = true ] || mkdir -p "${BUNDLE_DIR}/system_deps"

echo ""
echo "[+] Target OS      : $TARGET_OS"
echo "[+] PAM Version    : $PAM_VERSION"
echo "[+] Include CP     : $INSTALL_CP"
echo "[+] Packages only  : $PACKAGES_ONLY"
echo "[+] Output Directory: $BUNDLE_DIR"
echo "------------------------------------------------------------------"

# 4. Download PAM Application Packages
echo "[+] Downloading PAM Application Tarballs..."
# Per-component versions come from the manifest; a component may differ from the
# release version (e.g. core 5.10.0 but a bundled utility 5.9.0). Fall back to
# the release version when the manifest has no entry for that component.
comp_ver() {
    local comp="$1" v
    v="$(python3 "$MANIFEST_PY" tarball-version "$PAM_VERSION" "$comp" "" 2>/dev/null || echo "")"
    if [ -n "$v" ]; then
        echo "$v"
    else
        echo "Warning: '$comp' has no version in the manifest for $PAM_VERSION;" >&2
        echo "         falling back to $PAM_VERSION (may not exist on the server)." >&2
        echo "$PAM_VERSION"
    fi
}
VAULT_VERSION="$(comp_ver vault)"
PAC_VERSION="$(comp_ver pac)"
PSM_VERSION="$(comp_ver psm)"
CPM_VERSION="$(comp_ver cpm)"
CP_VERSION="$(comp_ver cp)"

PAM_FILES=("pam-vault-${VAULT_VERSION}.tar.gz" "pam-pac-${PAC_VERSION}.tar.gz" "pam-psm-${PSM_VERSION}.tar.gz" "pam-cpm-${CPM_VERSION}.tar.gz")
if [ "$INSTALL_CP" = "true" ]; then
    PAM_FILES+=("pam-cp-${CP_VERSION}.tar.gz")
fi

for file in "${PAM_FILES[@]}"; do
    TARGET_FILE="${BUNDLE_DIR}/packages/${file}"
    if [ -f "$TARGET_FILE" ] && gzip -t "$TARGET_FILE" 2>/dev/null; then
        echo " -> Skipping ${file}, already exists and is valid..."
    else
        [ -f "$TARGET_FILE" ] && rm -f "$TARGET_FILE"  # remove corrupt/partial
        PART_FILE="${TARGET_FILE}.part"
        echo " -> Downloading ${file}..."
        if curl -u "${ZENDESK_EMAIL}:${ZENDESK_PASSWORD}" -sSL -f "${DOWNLOAD_BASE_URL}/${PAM_VERSION}/${file}" -o "$PART_FILE"; then
            if gzip -t "$PART_FILE" 2>/dev/null; then
                mv "$PART_FILE" "$TARGET_FILE"
                echo "    -> verified OK"
            else
                rm -f "$PART_FILE"
                echo "Error: ${file} failed gzip integrity check; removed partial file." >&2
                exit 1
            fi
        else
            rm -f "$PART_FILE"
            echo "Error downloading ${file}. Please check version number or Zendesk credentials." >&2
            exit 1
        fi
    fi
done

# 5. Fetch OS System & Docker Dependencies via Temporary Docker Container
if [ "$PACKAGES_ONLY" = true ]; then
    echo ""
    echo "[+] --packages-only: skipping system_deps (Ansible upgrade only needs packages/)."
else
echo ""
echo "[+] Downloading System & Docker packages for ${TARGET_OS} (using Docker container)..."

if [ "$TARGET_OS" = "ubuntu" ]; then
    if [ -f /etc/os-release ] && grep -q "ubuntu" /etc/os-release; then
        echo " -> Running directly on Ubuntu host. Fetching packages locally..."
        sudo apt-get update
        sudo apt-get install -y ca-certificates curl gnupg
        sudo install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
        sudo chmod a+r /etc/apt/keyrings/docker.gpg
        . /etc/os-release
        echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
        sudo apt-get update
        sudo apt-get install --download-only -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin telnet ufw wget tar curl ca-certificates gnupg
        cp -r /var/cache/apt/archives/*.deb "$(pwd)/${BUNDLE_DIR}/system_deps/"
        sudo chown -R $USER:$USER "$(pwd)/${BUNDLE_DIR}/system_deps/"
    elif command -v docker &> /dev/null; then
        echo " -> Running via Docker container..."
        docker run --rm -v "$(pwd)/${BUNDLE_DIR}/system_deps:/dist" $DOCKER_IMG bash -c "
            apt-get update && \
            apt-get install -y ca-certificates curl gnupg && \
            install -m 0755 -d /etc/apt/keyrings && \
            curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
            . /etc/os-release && \
            echo \"deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \${VERSION_CODENAME} stable\" > /etc/apt/sources.list.d/docker.list && \
            apt-get update && \
            cd /dist && \
            apt-get install --download-only -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin telnet ufw wget tar curl ca-certificates gnupg && \
            cp -r /var/cache/apt/archives/*.deb /dist/
        "
    else
        echo "Warning: Docker is not installed and host is not Ubuntu. System packages (.deb) will be skipped."
    fi
elif [ "$TARGET_OS" = "rhel" ]; then
    if command -v docker &> /dev/null; then
        docker run --rm -v "$(pwd)/${BUNDLE_DIR}/system_deps:/dist" $DOCKER_IMG bash -c "
            dnf install -y dnf-plugins-core && \
            dnf config-manager --add-repo=https://download.docker.com/linux/centos/docker-ce.repo && \
            sed -i 's/\$releasever/9/g' /etc/yum.repos.d/docker-ce.repo && \
            cd /dist && \
            dnf download --resolve --destdir=/dist docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin docker-ce-rootless-extras telnet firewalld wget tar curl
        "
    else
        echo "Warning: Docker is not installed. System packages (.rpm) will be skipped."
    fi
fi
fi  # end of PACKAGES_ONLY guard

# 6. Embed Offline Installer Script into Bundle
cat << 'EOF' > "${BUNDLE_DIR}/install_onebox_offline.sh"
#!/bin/bash
# install_onebox_offline.sh
# Multi-OS Offline Installer (Target Machine)

set -e

echo "=================================================================="
echo "          RankEZ Onebox Offline Target Installer                  "
echo "=================================================================="

# 1. OS Detection
if [ -f /etc/os-release ]; then
    . /etc/os-release
    case "$ID" in
        ubuntu|debian) OS_FAMILY="debian"; FIREWALL_TOOL="ufw" ;;
        rhel|ol|centos|rocky|almalinux) OS_FAMILY="rhel"; FIREWALL_TOOL="firewall-cmd" ;;
        *) echo "Unsupported OS: $ID" && exit 1 ;;
    esac
else
    echo "Error: /etc/os-release not found." && exit 1
fi

# 2. Check & Disable SELinux
echo "[+] Checking SELinux Status..."
if command -v getenforce &>/dev/null; then
    SELINUX_STATUS=$(getenforce)
    echo " -> Current SELinux Status: $SELINUX_STATUS"
    if [ "$SELINUX_STATUS" = "Enforcing" ]; then
        echo " -> Disabling SELinux (setting to Permissive)..."
        sudo setenforce 0 2>/dev/null || true
        if [ -f /etc/selinux/config ]; then
            sudo sed -i 's/^SELINUX=enforcing$/SELINUX=permissive/' /etc/selinux/config 2>/dev/null || true
        fi
    fi
else
    echo " -> SELinux is not installed/active."
fi

# 3. Check and Install Local System Packages & Docker
echo "[+] Checking for required system packages..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REQUIRED_TOOLS=("curl" "tar" "telnet" "docker" "$FIREWALL_TOOL")
MISSING_PKGS=()

for tool in "${REQUIRED_TOOLS[@]}"; do
    if ! command -v "$tool" &> /dev/null; then
        MISSING_PKGS+=("$tool")
    fi
done

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    echo " -> Missing tools: ${MISSING_PKGS[*]}. Installing from offline bundle..."
    if [ "$OS_FAMILY" = "debian" ]; then
        sudo dpkg -i --force-depends "${SCRIPT_DIR}"/system_deps/*.deb || true
        sudo apt-get install -f -y 2>/dev/null || true
    elif [ "$OS_FAMILY" = "rhel" ]; then
        sudo dnf localinstall -y "${SCRIPT_DIR}"/system_deps/*.rpm || sudo rpm -Uvh --replacepkgs "${SCRIPT_DIR}"/system_deps/*.rpm
    fi
else
    echo " -> All required tools are already installed. Skipping package installation."
fi

systemctl start docker
systemctl enable docker

# 4. Detect PAM Version from Local Tarballs
PAM_VAULT_FILE=$(ls "${SCRIPT_DIR}"/packages/pam-vault-*.tar.gz 2>/dev/null | head -n 1)
if [ -z "$PAM_VAULT_FILE" ]; then
    echo "Error: No pam-vault tarball found in ./packages/"
    exit 1
fi
PAM_VERSION=$(basename "$PAM_VAULT_FILE" | sed -E 's/pam-vault-(.*)\.tar\.gz/\1/')

# 5. Prompts for Network / Admin Credentials
DEFAULT_IP=$(ip -4 addr show eth0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -n 1)
[ -z "$DEFAULT_IP" ] && DEFAULT_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -z "$DEFAULT_IP" ] && DEFAULT_IP="192.168.0.100"

if [ -t 0 ]; then
    read -p "Enter IP Address [$DEFAULT_IP]: " USER_IP
    export address=${USER_IP:-$DEFAULT_IP}
else
    export address="$DEFAULT_IP"
fi

if [ -z "$PAM_REGISTER_PASSWORD" ]; then
    while true; do
        read -s -p "Enter password for PAM admin: " PAM_REGISTER_PASSWORD && echo
        read -s -p "Confirm password: " PAM_REGISTER_PASSWORD_CONFIRM && echo
        [ "$PAM_REGISTER_PASSWORD" = "$PAM_REGISTER_PASSWORD_CONFIRM" ] && [ -n "$PAM_REGISTER_PASSWORD" ] && break
        echo "Passwords do not match or empty. Try again."
    done
fi
export PAM_REGISTER_PASSWORD
export VAULT_IP=$address; export PAC_IP=$address; export PSM_IP=$address; export PAM_REGISTER_USERNAME=admin

# 6. Extract Packages
echo "[+] Extracting PAM packages..."
EXTRACT_HOME="/root/packages/$PAM_VERSION"
mkdir -p "$EXTRACT_HOME"
for file in "${SCRIPT_DIR}"/packages/pam-*.tar.gz; do
    tar -xvf "$file" -C "$EXTRACT_HOME"
done

# 7. Execute PAM Service Installers
echo "[+] Installing Vault..."
cd "$EXTRACT_HOME/pam-vault-$PAM_VERSION" && bash ./install.sh && sleep 180

echo "[+] Installing PAC..."
cd "$EXTRACT_HOME/pam-pac-$PAM_VERSION"
sed -i "s/^VAULT_LISTEN_IP=.*$/VAULT_LISTEN_IP=${VAULT_IP}/g" register.conf
bash ./install.sh && sleep 30

echo "[+] Installing PSM..."
cd "$EXTRACT_HOME/pam-psm-$PAM_VERSION"
sed -i "s/^CONNECT_PSM_ADDRESS=.*$/CONNECT_PSM_ADDRESS=${PSM_IP}/g" install.conf
sed -i "s/^VAULT_LISTEN_IP=.*$/VAULT_LISTEN_IP=${VAULT_IP}/g" register.conf
bash ./install.sh && sleep 30

echo "[+] Installing CPM..."
cd "$EXTRACT_HOME/pam-cpm-$PAM_VERSION"
sed -i "s/^VAULT_LISTEN_IP=.*$/VAULT_LISTEN_IP=${VAULT_IP}/g" register.conf
bash ./install.sh && sleep 30

if [ -d "$EXTRACT_HOME/pam-cp-$PAM_VERSION" ]; then
    echo "[+] Installing Credential Provider (CP)..."
    cd "$EXTRACT_HOME/pam-cp-$PAM_VERSION"
    [ -f "register.conf" ] && sed -i "s/^VAULT_LISTEN_IP=.*$/VAULT_LISTEN_IP=${VAULT_IP}/g" register.conf
    bash ./install.sh && sleep 30
fi

# 8. Configure & Verify Firewall
echo "[+] Configuring Firewall ($FIREWALL_TOOL)..."
if [ "$OS_FAMILY" = "debian" ]; then
    echo " -> Current UFW status:"
    sudo ufw status verbose || true
    
    echo " -> Updating UFW rules..."
    sudo ufw allow 22/tcp; sudo ufw allow 443/tcp; sudo ufw allow 1222/tcp; sudo ufw allow 3389/tcp; sudo ufw allow 8443/tcp
    [ -d "$EXTRACT_HOME/pam-cp-$PAM_VERSION" ] && sudo ufw allow 12893/tcp && sudo ufw allow 12843/tcp && sudo ufw allow 8200/tcp && sudo ufw allow 12863/tcp
    sudo ufw --force enable && sudo ufw reload
    
    echo " -> Verified UFW status:"
    sudo ufw status verbose
elif [ "$OS_FAMILY" = "rhel" ]; then
    sudo systemctl start firewalld && sudo systemctl enable firewalld
    echo " -> Current Firewalld status:"
    sudo firewall-cmd --state || true
    
    echo " -> Updating Firewalld rules..."
    sudo firewall-cmd --permanent --add-port=443/tcp --add-port=1222/tcp --add-port=3389/tcp --add-port=8443/tcp
    [ -d "$EXTRACT_HOME/pam-cp-$PAM_VERSION" ] && sudo firewall-cmd --permanent --add-port=12893/tcp --add-port=12843/tcp --add-port=8200/tcp --add-port=12863/tcp
    sudo firewall-cmd --reload
    
    echo " -> Verified Firewalld open ports:"
    sudo firewall-cmd --list-ports
fi

echo "=================================================="
echo " Offline Installation Completed Successfully!"
echo "=================================================="
EOF

chmod +x "${BUNDLE_DIR}/install_onebox_offline.sh"

# 7. Write manifest + state, and optionally create the final archive
PACKAGES_ABS="$(cd "${BUNDLE_DIR}/packages" && pwd)"
# Component -> version map recorded so the inventory step knows each tarball's
# own version (components can diverge from the release version).
COMPONENT_VERSIONS="{\"vault\":\"${VAULT_VERSION}\",\"pac\":\"${PAC_VERSION}\",\"psm\":\"${PSM_VERSION}\",\"cpm\":\"${CPM_VERSION}\""
[ "$INSTALL_CP" = "true" ] && COMPONENT_VERSIONS="${COMPONENT_VERSIONS},\"cp\":\"${CP_VERSION}\""
COMPONENT_VERSIONS="${COMPONENT_VERSIONS}}"

BUNDLE_MANIFEST="${BUNDLE_DIR}/MANIFEST.json"
cat > "$BUNDLE_MANIFEST" <<JSONEOF
{
  "version": "${PAM_VERSION}",
  "os": "${TARGET_OS}",
  "with_cp": ${INSTALL_CP},
  "packages_only": ${PACKAGES_ONLY},
  "bundle_dir": "${BUNDLE_DIR}",
  "packages_dir": "${PACKAGES_ABS}",
  "component_versions": ${COMPONENT_VERSIONS},
  "packages": [
JSONEOF
for i in "${!PAM_FILES[@]}"; do
    comma=","
    [ "$i" -eq $((${#PAM_FILES[@]} - 1)) ] && comma=""
    echo "    \"${PAM_FILES[$i]}\"${comma}" >> "$BUNDLE_MANIFEST"
done
cat >> "$BUNDLE_MANIFEST" <<'JSONEOF'
  ]
}
JSONEOF

# Record last-used download state for the inventory step (via rankez_common).
if command -v python3 >/dev/null 2>&1; then
    python3 - "$PAM_VERSION" "$TARGET_OS" "$INSTALL_CP" "$PACKAGES_ABS" "$SCRIPT_DIR" "$VAULT_VERSION" "$PAC_VERSION" "$PSM_VERSION" "$CPM_VERSION" "$CP_VERSION" <<'PYEOF'
import sys, os, json
version, os_name, with_cp, packages_abs, script_dir = sys.argv[1:6]
v_vault, v_pac, v_psm, v_cpm, v_cp = sys.argv[6:11]
component_versions = {"vault": v_vault, "pac": v_pac, "psm": v_psm, "cpm": v_cpm}
if with_cp.lower() == "true":
    component_versions["cp"] = v_cp
sys.path.insert(0, os.path.abspath(script_dir))
try:
    import rankez_common as rc
    rc.save_state({
        "version": version,
        "os": os_name,
        "with_cp": with_cp.lower() == "true",
        "bundle_dir": packages_abs,
        "packages_dir": packages_abs,
        "component_versions": component_versions,
    })
except Exception as e:
    # Fallback: write state.json directly.
    ws = os.environ.get("RANKEZ_UPGRADE_HOME", os.path.expanduser("~/.rankez-upgrade"))
    os.makedirs(os.path.join(ws, "inventory"), exist_ok=True)
    os.makedirs(os.path.join(ws, "bundles"), exist_ok=True)
    state_file = os.path.join(ws, "state.json")
    state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                state = json.load(f)
        except Exception:
            state = {}
    state.update({
        "version": version,
        "os": os_name,
        "with_cp": with_cp.lower() == "true",
        "bundle_dir": packages_abs,
        "packages_dir": packages_abs,
        "component_versions": component_versions,
    })
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
PYEOF
fi

if [ "$PACKAGES_ONLY" = true ]; then
    echo ""
    echo "[+] --packages-only: skipping final archive. Only packages/ written."
else
    TAR_NAME="onebox-offline-${TARGET_OS}-v${PAM_VERSION}.tar.gz"
    echo ""
    echo "[+] Creating final tarball: ${TAR_NAME}..."
    tar -czf "${BUNDLE_ROOT}/${TAR_NAME}" -C "$BUNDLE_ROOT" "onebox-offline-${TARGET_OS}-v${PAM_VERSION}"
fi

echo ""
echo "=================================================================="
echo " Offline bundle successfully generated!"
echo " Packages dir : ${PACKAGES_ABS}"
echo "------------------------------------------------------------------"
echo " Next step - generate the inventory:"
echo "   ./rankez-upgrade inventory --version ${PAM_VERSION} --os ${TARGET_OS} --bundle-dir \"${PACKAGES_ABS}\""
echo "=================================================================="