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
if [ -f "$SCRIPT_DIR/.env" ]; then
    echo "[+] Loading credentials from $SCRIPT_DIR/.env..."
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

if [ -z "$ZENDESK_EMAIL" ] || [ -z "$ZENDESK_PASSWORD" ]; then
    echo "Error: Missing Zendesk credentials." >&2
    echo "Please define ZENDESK_EMAIL and ZENDESK_PASSWORD in a .env file or set them as environment variables." >&2
    exit 1
fi

DOWNLOAD_BASE_URL="https://download.rankez.co/product/pam"

# 1. Select Target OS
echo "Select Target Operating System:"
echo "  1) Ubuntu 22.04 / 24.04 / 26.04 (Debian-based)"
echo "  2) RHEL 9 / Oracle Linux 9 / Rocky 9 (RHEL-based)"
read -p "Enter choice [1-2]: " OS_CHOICE

case "$OS_CHOICE" in
    1)
        TARGET_OS="ubuntu"
        DOCKER_IMG="ubuntu:26.04"
        ;;
    2)
        TARGET_OS="rhel"
        DOCKER_IMG="oraclelinux:9"
        ;;
    *)
        echo "Invalid choice. Exiting."
        exit 1
        ;;
esac

# 2. Input PAM Version
CURRENT_YEAR=$(date +%Y)
CURRENT_MONTH=$((10#$(date +%m)))
CALC_MAJOR=$((CURRENT_YEAR - 2021))
DEFAULT_VERSION="${CALC_MAJOR}.${CURRENT_MONTH}.0"

read -p "Enter PAM Version to bundle [$DEFAULT_VERSION]: " USER_VERSION
PAM_VERSION=${USER_VERSION:-$DEFAULT_VERSION}

# 3. Include Credential Provider (CP)?
read -p "Include Credential Provider (CP) package? [y/N]: " CP_CONFIRM
case "$CP_CONFIRM" in
    [yY][eE][sS]|[yY]) INSTALL_CP=true ;;
    *) INSTALL_CP=false ;;
esac

BUNDLE_DIR="onebox-offline-${TARGET_OS}-v${PAM_VERSION}"
mkdir -p "${BUNDLE_DIR}/packages"
mkdir -p "${BUNDLE_DIR}/system_deps"

echo ""
echo "[+] Target OS      : $TARGET_OS"
echo "[+] PAM Version    : $PAM_VERSION"
echo "[+] Include CP     : $INSTALL_CP"
echo "[+] Output Directory: $BUNDLE_DIR"
echo "------------------------------------------------------------------"

# 4. Download PAM Application Packages
echo "[+] Downloading PAM Application Tarballs..."
PAM_FILES=("pam-vault-${PAM_VERSION}.tar.gz" "pam-pac-${PAM_VERSION}.tar.gz" "pam-psm-${PAM_VERSION}.tar.gz" "pam-cpm-${PAM_VERSION}.tar.gz")
if [ "$INSTALL_CP" = "true" ]; then
    PAM_FILES+=("pam-cp-${PAM_VERSION}.tar.gz")
fi

for file in "${PAM_FILES[@]}"; do
    TARGET_FILE="${BUNDLE_DIR}/packages/${file}"
    if [ -f "$TARGET_FILE" ]; then
        echo " -> Skipping ${file}, already exists..."
    else
        echo " -> Downloading ${file}..."
        curl -u "${ZENDESK_EMAIL}:${ZENDESK_PASSWORD}" -sSL -f "${DOWNLOAD_BASE_URL}/${PAM_VERSION}/${file}" -o "$TARGET_FILE" || {
            echo "Error downloading ${file}. Please check version number or Zendesk credentials."
            exit 1
        }
    fi
done

# 5. Fetch OS System & Docker Dependencies via Temporary Docker Container
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
    [ -d "$EXTRACT_HOME/pam-cp-$VERSION" ] && sudo firewall-cmd --permanent --add-port=12893/tcp --add-port=12843/tcp --add-port=8200/tcp --add-port=12863/tcp
    sudo firewall-cmd --reload
    
    echo " -> Verified Firewalld open ports:"
    sudo firewall-cmd --list-ports
fi

echo "=================================================="
echo " Offline Installation Completed Successfully!"
echo "=================================================="
EOF

chmod +x "${BUNDLE_DIR}/install_onebox_offline.sh"

# 7. Create Final Archive
TAR_NAME="onebox-offline-${TARGET_OS}-v${PAM_VERSION}.tar.gz"
echo ""
echo "[+] Creating final tarball: ${TAR_NAME}..."
tar -czvf "${TAR_NAME}" "${BUNDLE_DIR}"

echo ""
echo "=================================================================="
echo " Offline bundle successfully generated!"
echo " Archive File: $(pwd)/${TAR_NAME}"
echo "=================================================================="