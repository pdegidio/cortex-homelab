#!/usr/bin/env bash
# =============================================================
#  Cortex — AI-Powered Homelab Monitor
#  Interactive installer
#
#  Usage:
#    bash install.sh
#
#  Or one-liner from GitHub:
#    bash <(curl -sSL https://raw.githubusercontent.com/pdegidio/cortex-homelab/main/install.sh)
#
#  What this script does:
#    1. Checks system requirements
#    2. Creates a dedicated 'cortex' system user
#    3. Installs scripts and config to /opt/cortex
#    4. Installs the Python dependency (requests)
#    5. Walks you through cortex.conf configuration
#    6. Sets up cron jobs
#    7. Optionally installs the systemd service for the exporter
#    8. Runs a test cycle to verify everything works
# =============================================================

set -euo pipefail

# -------------------------------------------------------
#  Colours and formatting
# -------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()      { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()     { error "$*"; exit 1; }
section() { echo -e "\n${BOLD}━━━ $* ━━━${NC}"; }
ask()     { echo -en "${YELLOW}?${NC} $* "; }

# -------------------------------------------------------
#  Paths
# -------------------------------------------------------
INSTALL_DIR="/opt/cortex"
SCRIPTS_DIR="${INSTALL_DIR}/scripts"
CONFIG_DIR="${INSTALL_DIR}/config"
DATA_DIR="/var/lib/cortex"
LOG_DIR="/var/log"
CORTEX_USER="cortex"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# -------------------------------------------------------
#  Banner
# -------------------------------------------------------
clear
echo -e "${BOLD}"
cat <<'EOF'
   ____            _
  / ___|___  _ __| |_ _____  __
 | |   / _ \| '__| __/ _ \ \/ /
 | |__| (_) | |  | ||  __/>  <
  \____\___/|_|   \__\___/_/\_\

  AI-Powered Homelab Monitor — Installer v1.0.0
EOF
echo -e "${NC}"
echo "  This script will install Cortex on your system."
echo "  It requires sudo privileges for a few steps."
echo ""
ask "Ready to begin? [Y/n]"
read -r REPLY
[[ "${REPLY,,}" == "n" ]] && echo "Aborted." && exit 0

# -------------------------------------------------------
#  1. Requirements check
# -------------------------------------------------------
section "Checking requirements"

# Must run on Linux
[[ "$(uname -s)" == "Linux" ]] || die "Cortex requires Linux."

# Python 3.10+
if command -v python3 &>/dev/null; then
    PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
    if [[ "$PY_MAJOR" -ge 3 && "$PY_MINOR" -ge 10 ]]; then
        ok "Python ${PY_VER} found"
    else
        die "Python 3.10+ required, found ${PY_VER}. Please upgrade Python."
    fi
else
    die "Python 3 not found. Install it with: sudo apt install python3"
fi

# Docker
if command -v docker &>/dev/null; then
    DOCKER_VER=$(docker --version | awk '{print $3}' | tr -d ',')
    ok "Docker ${DOCKER_VER} found"
else
    warn "Docker not found — container monitoring will not work."
    warn "Install Docker: https://docs.docker.com/engine/install/"
fi

# pip / requests
if python3 -c "import requests" &>/dev/null; then
    ok "Python 'requests' library found"
    NEED_REQUESTS=false
else
    warn "Python 'requests' library not found — will install it"
    NEED_REQUESTS=true
fi

# curl (for Ollama connectivity test)
if command -v curl &>/dev/null; then
    ok "curl found"
else
    warn "curl not found — skipping Ollama connectivity test"
fi

# sudo
if ! sudo -n true 2>/dev/null; then
    info "This installer needs sudo for a few steps (creating user, writing to /opt, /var)."
    sudo -v || die "sudo authentication failed."
fi
ok "sudo access confirmed"

# -------------------------------------------------------
#  2. Install Python dependency
# -------------------------------------------------------
if [[ "$NEED_REQUESTS" == true ]]; then
    section "Installing Python dependencies"
    info "Installing 'requests'..."
    pip3 install --quiet requests || \
    pip3 install --quiet --break-system-packages requests || \
    die "Failed to install 'requests'. Run: pip3 install requests"
    ok "'requests' installed"
fi

# -------------------------------------------------------
#  3. Create system user and directories
# -------------------------------------------------------
section "Setting up system user and directories"

if id "$CORTEX_USER" &>/dev/null; then
    ok "User '${CORTEX_USER}' already exists"
else
    info "Creating system user '${CORTEX_USER}'..."
    sudo useradd --system --no-create-home --shell /usr/sbin/nologin "$CORTEX_USER"
    ok "User '${CORTEX_USER}' created"
fi

for DIR in "$SCRIPTS_DIR" "$CONFIG_DIR" "$DATA_DIR"; do
    sudo mkdir -p "$DIR"
    sudo chown -R "${CORTEX_USER}:${CORTEX_USER}" "$DIR"
    ok "Directory: $DIR"
done

# -------------------------------------------------------
#  4. Install scripts
# -------------------------------------------------------
section "Installing Cortex scripts"

for SCRIPT in cortex-monitor.py cortex-digest.py cortex-exporter.py; do
    if [[ -f "${SCRIPT_DIR}/scripts/${SCRIPT}" ]]; then
        sudo cp "${SCRIPT_DIR}/scripts/${SCRIPT}" "${SCRIPTS_DIR}/${SCRIPT}"
    elif [[ -f "${SCRIPT_DIR}/${SCRIPT}" ]]; then
        sudo cp "${SCRIPT_DIR}/${SCRIPT}" "${SCRIPTS_DIR}/${SCRIPT}"
    else
        warn "Script not found: ${SCRIPT} — skipping"
        continue
    fi
    sudo chmod +x "${SCRIPTS_DIR}/${SCRIPT}"
    sudo chown "${CORTEX_USER}:${CORTEX_USER}" "${SCRIPTS_DIR}/${SCRIPT}"
    ok "Installed: ${SCRIPTS_DIR}/${SCRIPT}"
done

# Install Modelfile
if [[ -f "${SCRIPT_DIR}/modelfile/Modelfile" ]]; then
    sudo mkdir -p "${INSTALL_DIR}/modelfile"
    sudo cp "${SCRIPT_DIR}/modelfile/Modelfile" "${INSTALL_DIR}/modelfile/Modelfile"
    ok "Installed: Modelfile"
elif [[ -f "${SCRIPT_DIR}/Modelfile" ]]; then
    sudo mkdir -p "${INSTALL_DIR}/modelfile"
    sudo cp "${SCRIPT_DIR}/Modelfile" "${INSTALL_DIR}/modelfile/Modelfile"
    ok "Installed: Modelfile"
fi

# -------------------------------------------------------
#  5. Configure cortex.conf
# -------------------------------------------------------
section "Configuration"

CONFIG_FILE="${CONFIG_DIR}/cortex.conf"

if [[ -f "$CONFIG_FILE" ]]; then
    warn "Config file already exists at ${CONFIG_FILE}"
    ask "Overwrite it? [y/N]"
    read -r REPLY
    if [[ "${REPLY,,}" != "y" ]]; then
        info "Keeping existing config. Edit it manually: ${CONFIG_FILE}"
        SKIP_CONFIG=true
    else
        SKIP_CONFIG=false
    fi
else
    SKIP_CONFIG=false
fi

if [[ "$SKIP_CONFIG" == false ]]; then
    # Copy example
    if [[ -f "${SCRIPT_DIR}/config/cortex.conf.example" ]]; then
        sudo cp "${SCRIPT_DIR}/config/cortex.conf.example" "$CONFIG_FILE"
    elif [[ -f "${SCRIPT_DIR}/cortex.conf.example" ]]; then
        sudo cp "${SCRIPT_DIR}/cortex.conf.example" "$CONFIG_FILE"
    else
        die "cortex.conf.example not found. Is this the correct directory?"
    fi
    sudo chown "${CORTEX_USER}:${CORTEX_USER}" "$CONFIG_FILE"
    sudo chmod 640 "$CONFIG_FILE"

    echo ""
    info "Let's configure the key settings."
    echo ""

    # Helper: set a value in the config file
    set_config() {
        local KEY="$1"
        local VALUE="$2"
        sudo sed -i "s|^${KEY}=.*|${KEY}=${VALUE}|" "$CONFIG_FILE"
    }

    # Ollama host
    ask "Ollama host URL [http://localhost:11434]:"
    read -r OLLAMA_HOST
    OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
    set_config "OLLAMA_HOST" "$OLLAMA_HOST"
    ok "OLLAMA_HOST=${OLLAMA_HOST}"

    # ntfy
    ask "ntfy base URL (e.g. http://your-ntfy:8090 or https://ntfy.sh):"
    read -r NTFY_URL
    if [[ -n "$NTFY_URL" ]]; then
        set_config "NTFY_URL" "$NTFY_URL"
        ok "NTFY_URL=${NTFY_URL}"

        ask "ntfy topic [homelab-system]:"
        read -r NTFY_TOPIC
        NTFY_TOPIC="${NTFY_TOPIC:-homelab-system}"
        set_config "NTFY_TOPIC" "$NTFY_TOPIC"
        ok "NTFY_TOPIC=${NTFY_TOPIC}"
    else
        warn "ntfy not configured — alert notifications will be skipped."
    fi

    # Containers
    ask "Container names to monitor (space-separated, e.g. sonarr radarr prowlarr plex):"
    read -r CONTAINERS
    if [[ -n "$CONTAINERS" ]]; then
        set_config "MONITORED_CONTAINERS" "\"${CONTAINERS}\""
        ok "MONITORED_CONTAINERS set"
    fi

    echo ""
    info "Config saved to: ${CONFIG_FILE}"
    info "You can edit it at any time: sudo nano ${CONFIG_FILE}"
fi

# -------------------------------------------------------
#  6. Ollama model setup
# -------------------------------------------------------
section "Setting up Ollama model"

if command -v ollama &>/dev/null; then
    MODELFILE="${INSTALL_DIR}/modelfile/Modelfile"
    if [[ -f "$MODELFILE" ]]; then
        info "Building cortex model from Modelfile..."
        ask "Pull base model and build cortex:latest? This may take a few minutes. [Y/n]"
        read -r REPLY
        if [[ "${REPLY,,}" != "n" ]]; then
            ollama pull qwen2.5:14b-instruct && \
            ollama create cortex -f "$MODELFILE" && \
            ok "cortex:latest model created" || \
            warn "Model creation failed — you can run it manually: ollama create cortex -f ${MODELFILE}"
        fi
    else
        warn "Modelfile not found at ${MODELFILE} — skipping model setup"
    fi
else
    warn "ollama not found in PATH — skipping model setup"
    info "Install Ollama: https://ollama.com/download"
    info "Then run: ollama pull qwen2.5:14b-instruct && ollama create cortex -f ${INSTALL_DIR}/modelfile/Modelfile"
fi

# -------------------------------------------------------
#  7. Cron jobs
# -------------------------------------------------------
section "Setting up cron jobs"

PYTHON=$(command -v python3)
CRON_MONITOR="*/30 * * * * ${PYTHON} ${SCRIPTS_DIR}/cortex-monitor.py >> /var/log/cortex.log 2>&1"
CRON_DIGEST="0 9 * * * ${PYTHON} ${SCRIPTS_DIR}/cortex-digest.py >> /var/log/cortex-digest.log 2>&1"

# Check if crons already exist
CURRENT_CRON=$(crontab -l 2>/dev/null || true)

if echo "$CURRENT_CRON" | grep -q "cortex-monitor"; then
    ok "cortex-monitor cron already exists"
else
    ask "Install cron job for cortex-monitor (every 30 min)? [Y/n]"
    read -r REPLY
    if [[ "${REPLY,,}" != "n" ]]; then
        (crontab -l 2>/dev/null; echo "# Cortex — AI monitoring"; echo "$CRON_MONITOR") | crontab -
        ok "cortex-monitor cron installed"
    fi
fi

if echo "$CURRENT_CRON" | grep -q "cortex-digest"; then
    ok "cortex-digest cron already exists"
else
    ask "Install cron job for cortex-digest (daily at 09:00)? [Y/n]"
    read -r REPLY
    if [[ "${REPLY,,}" != "n" ]]; then
        (crontab -l 2>/dev/null; echo "# Cortex — Daily digest"; echo "$CRON_DIGEST") | crontab -
        ok "cortex-digest cron installed"
    fi
fi

# -------------------------------------------------------
#  8. Systemd service for exporter (optional)
# -------------------------------------------------------
section "Prometheus exporter"

SYSTEMD_FILE="/etc/systemd/system/cortex-exporter.service"

if [[ -f "$SYSTEMD_FILE" ]]; then
    ok "cortex-exporter.service already installed"
elif command -v systemctl &>/dev/null; then
    ask "Install cortex-exporter as a systemd service (recommended for production)? [Y/n]"
    read -r REPLY
    if [[ "${REPLY,,}" != "n" ]]; then
        SERVICE_SRC="${SCRIPT_DIR}/docs/systemd-exporter.service"
        if [[ -f "$SERVICE_SRC" ]]; then
            sudo cp "$SERVICE_SRC" "$SYSTEMD_FILE"
            # Patch paths in service file
            sudo sed -i "s|/opt/cortex/scripts/|${SCRIPTS_DIR}/|g" "$SYSTEMD_FILE"
            sudo sed -i "s|/opt/cortex/config/cortex.conf|${CONFIG_DIR}/cortex.conf|g" "$SYSTEMD_FILE"
            sudo systemctl daemon-reload
            sudo systemctl enable cortex-exporter
            sudo systemctl start cortex-exporter
            ok "cortex-exporter.service enabled and started"
            info "Check status: sudo systemctl status cortex-exporter"
        else
            warn "systemd-exporter.service not found — falling back to @reboot cron"
            CRON_EXPORTER="@reboot ${PYTHON} ${SCRIPTS_DIR}/cortex-exporter.py &"
            (crontab -l 2>/dev/null; echo "# Cortex — Prometheus exporter"; echo "$CRON_EXPORTER") | crontab -
            ok "Exporter @reboot cron installed"
        fi
    else
        # Fall back to cron
        CRON_EXPORTER="@reboot ${PYTHON} ${SCRIPTS_DIR}/cortex-exporter.py &"
        (crontab -l 2>/dev/null; echo "# Cortex — Prometheus exporter"; echo "$CRON_EXPORTER") | crontab -
        ok "Exporter @reboot cron installed"
    fi
fi

# -------------------------------------------------------
#  9. Test run
# -------------------------------------------------------
section "Test run"

ask "Run a test cycle now (dry run — no alerts will be sent)? [Y/n]"
read -r REPLY
if [[ "${REPLY,,}" != "n" ]]; then
    info "Running cortex-monitor.py --dry-run..."
    echo ""
    "$PYTHON" "${SCRIPTS_DIR}/cortex-monitor.py" \
        --config "${CONFIG_DIR}/cortex.conf" \
        --dry-run 2>&1 | head -50
    echo ""
    ok "Test run complete — check output above for any errors"
fi

# -------------------------------------------------------
#  Done
# -------------------------------------------------------
section "Installation complete"

echo ""
echo -e "  ${GREEN}${BOLD}Cortex is installed and running.${NC}"
echo ""
echo -e "  ${BOLD}Key paths:${NC}"
echo "    Scripts:  ${SCRIPTS_DIR}/"
echo "    Config:   ${CONFIG_FILE}"
echo "    Data:     ${DATA_DIR}/"
echo "    Logs:     /var/log/cortex.log"
echo ""
echo -e "  ${BOLD}Next steps:${NC}"
echo "    1. Review your config:   sudo nano ${CONFIG_FILE}"
echo "    2. Add *arr API keys to  ${CONFIG_FILE}"
echo "    3. Import Grafana dashboard from: grafana/cortex-monitor.json"
echo "    4. Watch the first real alert arrive at 09:00 tomorrow"
echo ""
echo -e "  ${BOLD}Useful commands:${NC}"
echo "    Manual monitor run:   python3 ${SCRIPTS_DIR}/cortex-monitor.py"
echo "    Manual digest:        python3 ${SCRIPTS_DIR}/cortex-digest.py --dry-run"
echo "    Check metrics:        python3 ${SCRIPTS_DIR}/cortex-exporter.py --once"
echo "    View logs:            tail -f /var/log/cortex.log"
echo ""
echo -e "  Issues? Open a GitHub issue or check the README."
echo ""
