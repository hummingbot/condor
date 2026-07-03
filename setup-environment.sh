#!/bin/bash

# Configuration
ENV_FILE=".env"
CONFIG_FILE="config.yml"
DATA_DIR="data"
HB_API_DIR="../hummingbot-api"
HB_API_REPO="https://github.com/hummingbot/hummingbot-api.git"

# ── Colors & Output Helpers ──────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

msg_info()  { echo -e "  ${CYAN}→${RESET} $1"; }
msg_ok()    { echo -e "  ${GREEN}✓${RESET} $1"; }
msg_warn()  { echo -e "  ${YELLOW}!${RESET} $1"; }
msg_error() { echo -e "  ${RED}✗${RESET} $1"; }

# Prompt with default value display
prompt_visible() {
    local prompt="$1"
    local default="$2"
    local var_name="$3"
    if [ -n "$default" ]; then
        echo -ne "  ${prompt} ${DIM}[${default}]${RESET}: " >&2
    else
        echo -ne "  ${prompt}: " >&2
    fi
    read -r value < /dev/tty || value=""
    value=$(echo "$value" | tr -d '[:space:]')
    if [ -z "$value" ] && [ -n "$default" ]; then
        value="$default"
    fi
    eval "$var_name=\"$value\""
}

# Prompt for passwords (no echo)
prompt_secret() {
    local prompt="$1"
    local default="$2"
    local var_name="$3"
    if [ -n "$default" ]; then
        echo -ne "  ${prompt} ${DIM}[${default}]${RESET}: " >&2
    else
        echo -ne "  ${prompt}: " >&2
    fi
    read -rs value < /dev/tty || value=""
    echo "" >&2
    value=$(echo "$value" | tr -d '[:space:]')
    if [ -z "$value" ] && [ -n "$default" ]; then
        value="$default"
    fi
    eval "$var_name=\"$value\""
}

# Escape special characters for .env file
escape_env_value() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//\$/\\\$}"
    echo "$value"
}

# OSC 8 clickable hyperlink (falls back to plain URL)
make_link() {
    local url="$1"
    local text="${2:-$url}"
    # Check if terminal supports hyperlinks (most modern terminals do)
    if [ -n "${TERM:-}" ] && [ "${TERM:-}" != "dumb" ]; then
        echo -e "\033]8;;${url}\033\\${text}\033]8;;\033\\"
    else
        echo "$text ($url)"
    fi
}

# Refresh PATH to include common installation locations
refresh_path() {
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin:$PATH"
    
    # Load nvm if available
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    [ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"
    
    # Add nvm node to PATH if nvm is available
    if command_exists nvm; then
        export PATH="$NVM_DIR/versions/node/$(nvm version 2>/dev/null)/bin:$PATH"
    fi
    
    # Also source profile files if they exist
    [ -f "$HOME/.bashrc" ] && source "$HOME/.bashrc" 2>/dev/null || true
    [ -f "$HOME/.profile" ] && source "$HOME/.profile" 2>/dev/null || true
    [ -f "$HOME/.bash_profile" ] && source "$HOME/.bash_profile" 2>/dev/null || true
}

# Check if a command exists and is executable
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Quick localhost API probe — avoid hanging Step 2 when port 8000 is slow/unreachable
api_health_check() {
    curl -sf --connect-timeout 3 --max-time 5 http://localhost:8000/docs >/dev/null 2>&1
}

# Restore Tailscale wizard choice from .env (survives re-runs after Step 1 is skipped)
load_tailscale_choice() {
    case "${USE_TAILSCALE:-}" in
        [Tt][Rr][Uu][Ee]|[Yy][Ee][Ss]|[Yy]|1) use_tailscale_early="y" ;;
    esac
}

# True when config.yml already has a server host entry under servers:
config_has_api_server() {
    [ -f "$CONFIG_FILE" ] && grep -A8 '^servers:' "$CONFIG_FILE" 2>/dev/null | grep -q 'host:'
}

# Merge API connection details into config.yml (handles both template and servers: {} layouts).
# PyYAML is NOT part of the Python standard library and is not guaranteed to be
# preinstalled on a fresh Linux/Mac/WSL box, so this tries several ways to get a
# working python3+yaml before falling back to a plain text edit. Returns 0 on a
# full YAML-aware update, 1 if it had to fall back to the degraded sed edit.
update_config_api_server() {
    local host="$1" port="$2" username="$3" password="$4"
    local yaml_script
    yaml_script=$(cat << 'PYEOF'
import sys
from pathlib import Path
import yaml

path = Path(sys.argv[1])
host, port, username, password, admin_id = sys.argv[2:7]
data = yaml.safe_load(path.read_text()) if path.exists() else {}
if not isinstance(data, dict):
    data = {}

servers = data.setdefault("servers", {})
servers["local"] = {
    "host": host,
    "port": int(port),
    "username": username,
    "password": password,
}
data["default_server"] = data.get("default_server") or "local"

server_access = data.setdefault("server_access", {})
if "local" not in server_access:
    server_access["local"] = {
        "owner_id": int(admin_id) if admin_id.isdigit() else admin_id,
        "created_at": None,
        "shared_with": {},
    }

# Compare as strings since the YAML template stores this key as an int but
# admin_id arrives here as a string -- avoids writing a duplicate entry.
chat_defaults = data.setdefault("chat_defaults", {})
existing_keys = {str(k) for k in chat_defaults.keys()}
if admin_id and admin_id not in existing_keys:
    chat_defaults[int(admin_id) if admin_id.isdigit() else admin_id] = "local"

data.setdefault("version", 1)
path.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False))
PYEOF
)

    # 1. PyYAML already importable by the system python3 -- use it directly.
    if python3 -c "import yaml" >/dev/null 2>&1; then
        echo "$yaml_script" | python3 - "$CONFIG_FILE" "$host" "$port" "$username" "$password" "$ADMIN_USER_ID" && return 0
    fi

    # 2. uv is already installed by Step 0 of this script -- use it to run the
    #    editor in an ephemeral environment with pyyaml. No system/user
    #    site-packages changes, no PEP 668 "externally managed" errors.
    if command_exists uv; then
        if echo "$yaml_script" | uv run --with pyyaml python3 - "$CONFIG_FILE" "$host" "$port" "$username" "$password" "$ADMIN_USER_ID" 2>/dev/null; then
            return 0
        fi
    fi

    # 3. Last resort: try installing pyyaml directly for python3 (covers both
    #    PEP 668 "externally managed" distros and plain ones, root or non-root).
    msg_warn "PyYAML unavailable via uv -- attempting a direct pip install..."
    if python3 -m pip install --user --quiet pyyaml >/dev/null 2>&1 \
       || python3 -m pip install --user --break-system-packages --quiet pyyaml >/dev/null 2>&1 \
       || python3 -m pip install --break-system-packages --quiet pyyaml >/dev/null 2>&1; then
        if python3 -c "import yaml" >/dev/null 2>&1; then
            echo "$yaml_script" | python3 - "$CONFIG_FILE" "$host" "$port" "$username" "$password" "$ADMIN_USER_ID" && return 0
        fi
    fi

    # 4. All YAML-based paths failed -- fall back to the old text-based edit so
    #    config.yml still gets updated, just without the richer server_access /
    #    chat_defaults handling.
    msg_warn "Could not load PyYAML -- falling back to a simpler text-based edit of $CONFIG_FILE"
    sed -i.bak "/servers:/,/^[^ ]/ s/host: .*/host: $host/" "$CONFIG_FILE" && rm -f "$CONFIG_FILE.bak"
    sed -i.bak "/servers:/,/^[^ ]/ s/port: .*/port: $port/" "$CONFIG_FILE" && rm -f "$CONFIG_FILE.bak"
    if [ -n "$username" ]; then
        sed -i.bak "/servers:/,/^[^ ]/ s/username: .*/username: $username/" "$CONFIG_FILE" && rm -f "$CONFIG_FILE.bak"
        sed -i.bak "/servers:/,/^[^ ]/ s/password: .*/password: $password/" "$CONFIG_FILE" && rm -f "$CONFIG_FILE.bak"
    fi
    return 1
}

# On WSL2, systemd doesn't manage tailscaled — we must start the daemon manually
# before calling `tailscale up`, otherwise the call silently fails.
tailscale_up() {
    if grep -qi microsoft /proc/version 2>/dev/null; then
        if ! pgrep -x tailscaled >/dev/null 2>&1; then
            msg_info "Starting Tailscale daemon (WSL2)..."
            sudo mkdir -p /var/run/tailscale /var/lib/tailscale
            sudo tailscaled --state=/var/lib/tailscale/tailscaled.state \
                            --socket=/var/run/tailscale/tailscaled.sock \
                            >/dev/null 2>&1 &
            sleep 2
        fi
    fi
    sudo tailscale up "$@"
}

# ── Banner ───────────────────────────────────────────

echo ""
echo -e "${BOLD}╔═══════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║            Condor Setup                   ║${RESET}"
echo -e "${BOLD}╚═══════════════════════════════════════════╝${RESET}"
echo ""

# ── Step 0: Ensure dependencies are installed ───────

echo -e "${BOLD}Step 0: Installing Dependencies${RESET}"
echo ""

SUDO_CMD=""
if [ "${EUID:-0}" -ne 0 ] && command_exists sudo; then
    SUDO_CMD="sudo"
fi

# Track if we need to restart the script
NEEDS_RESTART=false

# ── Install uv ──────────────────────────────────────

if ! command_exists uv; then
    msg_info "Installing uv (https://docs.astral.sh/uv/)..."
    if curl -LsSf https://astral.sh/uv/install.sh | sh; then
        msg_ok "uv installed successfully"
        NEEDS_RESTART=true
        refresh_path
        
        # Verify installation
        if ! command_exists uv; then
            msg_warn "uv installed but not immediately available. Will retry after PATH refresh."
        else
            msg_ok "uv is now available"
        fi
    else
        msg_error "Failed to install uv automatically."
        msg_info "Please install manually: curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi
else
    msg_ok "uv is already installed ($(uv --version 2>/dev/null || echo 'version unknown'))"
fi

# ── Install tmux ────────────────────────────────────

if ! command_exists tmux; then
    msg_info "Installing tmux..."

    if command_exists apt-get; then
        $SUDO_CMD apt-get update -qq && $SUDO_CMD apt-get install -y tmux || {
            msg_error "Failed to install tmux via apt-get."
            exit 1
        }
    elif command_exists yum; then
        $SUDO_CMD yum install -y tmux || {
            msg_error "Failed to install tmux via yum."
            exit 1
        }
    elif command_exists dnf; then
        $SUDO_CMD dnf install -y tmux || {
            msg_error "Failed to install tmux via dnf."
            exit 1
        }
    elif command_exists brew; then
        brew install tmux || {
            msg_error "Failed to install tmux via Homebrew."
            exit 1
        }
    else
        msg_error "No supported package manager found. Please install tmux manually."
        exit 1
    fi
    msg_ok "tmux installed successfully"
else
    msg_ok "tmux is already installed ($(tmux -V 2>/dev/null || echo 'version unknown'))"
fi

# ── Install Node.js and npm via nvm ────────────────

if ! command_exists node || ! command_exists npm; then
    # If node exists but npm doesn't, we still need to install via nvm
    if command_exists node && ! command_exists npm; then
        msg_warn "Node.js found but npm is missing. Installing via nvm..."
    else
        msg_info "Installing Node.js and npm via nvm..."
    fi
    
    # Install nvm (Node Version Manager)
    if [ ! -d "$HOME/.nvm" ]; then
        msg_info "Installing nvm..."
        if curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.4/install.sh | bash; then
            msg_ok "nvm installed successfully"
        else
            msg_error "Failed to install nvm."
            msg_info "Install manually: https://github.com/nvm-sh/nvm"
            exit 1
        fi
    else
        msg_ok "nvm is already installed"
    fi
    
    # Load nvm into current shell (in lieu of restarting)
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    [ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"
    
    # Verify nvm is loaded
    if ! command_exists nvm; then
        msg_warn "nvm not immediately available, sourcing profile..."
        # Try to source nvm from common locations
        for profile in "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.zshrc" "$HOME/.profile"; do
            if [ -f "$profile" ] && grep -q "NVM_DIR" "$profile"; then
                source "$profile" 2>/dev/null || true
                [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
                break
            fi
        done
    fi
    
    # Install Node.js LTS (version 24)
    msg_info "Installing Node.js v24 (LTS)..."
    if nvm install 24; then
        nvm use 24
        nvm alias default 24
        msg_ok "Node.js v24 installed and set as default"
        NEEDS_RESTART=true
    else
        msg_error "Failed to install Node.js via nvm."
        exit 1
    fi
    
    # Load node into current shell
    export PATH="$NVM_DIR/versions/node/$(nvm version)/bin:$PATH"
    
else
    msg_ok "Node.js is already installed ($(node --version 2>/dev/null))"
    msg_ok "npm is available ($(npm --version 2>/dev/null))"
fi

# Verify npm is available - final check with better error handling
if ! command_exists npm; then
    msg_warn "npm not found, attempting to load nvm..."
    
    # Try to load nvm and node
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    
    if command_exists nvm; then
        nvm use default 2>/dev/null || nvm use node 2>/dev/null || nvm use 24 2>/dev/null || true
        export PATH="$NVM_DIR/versions/node/$(nvm version)/bin:$PATH"
    fi
    
    # Final check
    if ! command_exists npm; then
        msg_error "npm still not available."
        msg_info "Your system has Node.js $(node --version 2>/dev/null) installed without npm."
        msg_info ""
        msg_info "Options:"
        msg_info "  1. Install npm: sudo apt-get install npm (or your package manager)"
        msg_info "  2. Or let this script install via nvm (uninstall system node first)"
        msg_info ""
        msg_info "To use nvm: sudo apt-get remove nodejs && bash $0"
        exit 1
    fi
fi

# ── Install Claude ACP globally ─────────────────────

# Ensure shell environment is refreshed so npm is available after node install.
refresh_path
hash -r

if ! npm list -g @agentclientprotocol/claude-agent-acp >/dev/null 2>&1; then
    msg_info "Installing @agentclientprotocol/claude-agent-acp globally..."
    if npm install -g @agentclientprotocol/claude-agent-acp; then
        msg_ok "@agentclientprotocol/claude-agent-acp installed successfully"
        NEEDS_RESTART=true
        refresh_path
    else
        msg_error "Failed to install @agentclientprotocol/claude-agent-acp globally."
        msg_info "You can install it later with: npm install -g @agentclientprotocol/claude-agent-acp"
        # Don't exit - this dependency is optional for core setup flow
    fi
else
    msg_ok "@agentclientprotocol/claude-agent-acp is already installed"
fi

# ── Install TypeScript globally ─────────────────────

if ! command_exists tsc && ! npm list -g typescript >/dev/null 2>&1; then
    msg_info "Installing TypeScript globally..."
    if npm install -g typescript; then
        msg_ok "TypeScript installed successfully"
        NEEDS_RESTART=true
        refresh_path
    else
        msg_error "Failed to install TypeScript globally."
        msg_info "You can install it later with: npm install -g typescript"
        # Don't exit - TypeScript might not be critical for all setups
    fi
else
    msg_ok "TypeScript is already installed ($(tsc --version 2>/dev/null || echo 'installed'))"
fi

# ── Handle script restart if needed ─────────────────

if [ "$NEEDS_RESTART" = true ]; then
    msg_info "Dependencies were installed. Refreshing environment..."
    refresh_path
    
    # Verify critical commands are now available
    missing_commands=()
    command_exists uv || missing_commands+=("uv")
    command_exists node || missing_commands+=("node")
    command_exists npm || missing_commands+=("npm")
    
    if [ ${#missing_commands[@]} -gt 0 ]; then
        msg_warn "Some commands still not available: ${missing_commands[*]}"
        msg_info "Restarting script with refreshed environment..."
        echo ""
        
        # Re-execute this script in a new shell with proper environment
        exec bash "$0" "$@"
    else
        msg_ok "All dependencies are now available!"
    fi
fi

echo ""

# ── Step 1: Telegram Configuration ──────────────────

echo -e "${BOLD}Step 1: Telegram Configuration${RESET}"
echo ""

telegram_configured=false

# Source existing .env if present
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE" 2>/dev/null
    set +a
fi

if [ -n "${TELEGRAM_TOKEN:-}" ] && [ -n "${ADMIN_USER_ID:-}" ]; then
    msg_ok "Telegram already configured"
    telegram_configured=true
    load_tailscale_choice
else
    msg_info "Create a bot: $(make_link 'https://t.me/BotFather')"
    msg_info "Get your ID: $(make_link 'https://t.me/userinfobot')"
    echo ""

    # Prompt for Telegram Bot Token
    while true; do
        prompt_visible "Telegram Bot Token" "" "telegram_token"
        if [ -z "$telegram_token" ]; then
            msg_warn "Token cannot be empty"
            continue
        fi
        if ! [[ "$telegram_token" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
            msg_warn "Invalid format. Expected: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
            continue
        fi
        break
    done
    TELEGRAM_TOKEN="$telegram_token"

    # Prompt for Admin User ID
    while true; do
        prompt_visible "Admin User ID" "" "admin_id"
        if [ -z "$admin_id" ]; then
            msg_warn "User ID cannot be empty"
            continue
        fi
        if ! [[ "$admin_id" =~ ^[0-9]+$ ]]; then
            msg_warn "User ID must be numeric (e.g., 123456789)"
            continue
        fi
        break
    done
    ADMIN_USER_ID="$admin_id"

    # Determine web dashboard access
    echo ""
    prompt_visible "Will you use Tailscale to secure the connection to hummingbot-api? [y/N]" "N" "use_tailscale_early"
    SERVER_IP=""
    if ! [[ "${use_tailscale_early:-}" =~ ^[Yy]$ ]]; then
        echo ""
        echo -e "  The /web Telegram command sends you a login link for the web dashboard."
        echo -e "  So we need to know where Condor is running:"
        echo -e "    ${BOLD}Local machine${RESET} (Mac / Linux / WSL2) — press Enter"
        echo -e "    ${BOLD}Remote VPS${RESET}                         — enter the server's public IP"
        prompt_visible "Public IP or hostname (press Enter for localhost)" "" "server_ip"
        SERVER_IP="${server_ip:-}"
    fi

    if [[ "${use_tailscale_early:-}" =~ ^[Yy]$ ]]; then
        USE_TAILSCALE=true
    else
        USE_TAILSCALE=false
    fi

    # Write .env (preserve extra vars if file exists)
    if [ -f "$ENV_FILE" ]; then
        # Update existing values
        if grep -q "^TELEGRAM_TOKEN=" "$ENV_FILE"; then
            sed -i.bak "s|^TELEGRAM_TOKEN=.*|TELEGRAM_TOKEN=$(escape_env_value "$TELEGRAM_TOKEN")|" "$ENV_FILE"
            rm -f "$ENV_FILE.bak"
        else
            echo "TELEGRAM_TOKEN=$(escape_env_value "$TELEGRAM_TOKEN")" >> "$ENV_FILE"
        fi
        if grep -q "^ADMIN_USER_ID=" "$ENV_FILE"; then
            sed -i.bak "s|^ADMIN_USER_ID=.*|ADMIN_USER_ID=$(escape_env_value "$ADMIN_USER_ID")|" "$ENV_FILE"
            rm -f "$ENV_FILE.bak"
        else
            echo "ADMIN_USER_ID=$(escape_env_value "$ADMIN_USER_ID")" >> "$ENV_FILE"
        fi
        
        # Add WEB_URL if server IP was provided
        if [ -n "$SERVER_IP" ]; then
            if grep -q "^WEB_URL=" "$ENV_FILE"; then
                sed -i.bak "s|^WEB_URL=.*|WEB_URL=http://$(escape_env_value "$SERVER_IP"):8088|" "$ENV_FILE"
                rm -f "$ENV_FILE.bak"
            else
                echo "WEB_URL=http://$(escape_env_value "$SERVER_IP"):8088" >> "$ENV_FILE"
            fi
        fi
        if grep -q "^USE_TAILSCALE=" "$ENV_FILE"; then
            sed -i.bak "s|^USE_TAILSCALE=.*|USE_TAILSCALE=$USE_TAILSCALE|" "$ENV_FILE"
            rm -f "$ENV_FILE.bak"
        else
            echo "USE_TAILSCALE=$USE_TAILSCALE" >> "$ENV_FILE"
        fi
    else
        {
            echo "TELEGRAM_TOKEN=$(escape_env_value "$TELEGRAM_TOKEN")"
            echo "ADMIN_USER_ID=$(escape_env_value "$ADMIN_USER_ID")"
            echo "USE_TAILSCALE=$USE_TAILSCALE"
            if [ -n "$SERVER_IP" ]; then
                echo "WEB_URL=http://$(escape_env_value "$SERVER_IP"):8088"
            fi
        } > "$ENV_FILE"
    fi

    msg_ok ".env created"
    if [ -n "$SERVER_IP" ]; then
        msg_ok "Web server configured: http://$SERVER_IP:8088"
    fi
    telegram_configured=true
fi

echo ""

# ── Step 2: Hummingbot API ──────────────────────────

echo -e "${BOLD}Step 2: Hummingbot API${RESET}"
echo ""

hb_api_deployed=false

# Source .env again to get latest values
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE" 2>/dev/null
    set +a
fi
load_tailscale_choice

finish_remote_api=false
if [ -n "${DEPLOY_HUMMINGBOT_API:-}" ]; then
    if [ "${DEPLOY_HUMMINGBOT_API:-}" = "true" ]; then
        msg_ok "Hummingbot API already configured (enabled)"
        hb_api_deployed=true
    elif config_has_api_server; then
        msg_ok "Hummingbot API already configured (skipped)"
    else
        msg_info "Finishing remote API configuration..."
        finish_remote_api=true
    fi
fi

if [ -z "${DEPLOY_HUMMINGBOT_API:-}" ] || [ "$finish_remote_api" = true ]; then
    # Check if a Hummingbot API is already running on port 8000
    existing_api=false
    msg_info "Checking for an existing API on localhost:8000..."
    if api_health_check; then
        existing_api=true
        msg_warn "Hummingbot API already running on localhost:8000"
        echo ""
        prompt_visible "An API instance is already running. Override it? [y/N]" "N" "override_api"
        if [[ "${override_api:-}" =~ ^[Yy]$ ]]; then
            msg_info "Will reconfigure and restart the API."
        else
            echo "DEPLOY_HUMMINGBOT_API=false" >> "$ENV_FILE"
            msg_ok "Keeping existing API instance"
            hb_api_deployed=false
            # Skip the rest of the API setup block
            existing_api=skip
        fi
    fi

    if [ "$existing_api" != "skip" ]; then
    if [ "$finish_remote_api" = true ]; then
        deploy_hb="n"
        msg_info "Condor connects to Hummingbot Backend API for trading."
        echo ""
        msg_ok "Using remote Hummingbot API (local Docker deploy skipped)"
    else
    msg_info "Condor connects to Hummingbot Backend API for trading."
    echo ""
    prompt_visible "Configure and launch local Hummingbot API with Docker? [Y/n]" "Y" "deploy_hb"
    fi

    if [[ "${deploy_hb:-}" =~ ^[Nn]$ ]]; then
        if [ "$finish_remote_api" != true ]; then
            echo "DEPLOY_HUMMINGBOT_API=false" >> "$ENV_FILE"
        fi
        msg_ok "Skipped Hummingbot API deployment"
        echo ""
        msg_info "Enter the Hummingbot API connection details."
        if [[ "${use_tailscale_early:-}" =~ ^[Yy]$ ]]; then
            # Tailscale: host is resolved via MagicDNS after joining the tailnet — no URL needed
            HB_API_PROTOCOL="http"
            HB_API_HOST="hummingbot-api"
            HB_API_PORT="8000"
        else
            prompt_visible "API URL + port (e.g. http://your-server:8000)" "http://localhost:8000" "hb_api_url_raw"
            hb_api_url_raw="${hb_api_url_raw:-http://localhost:8000}"
            HB_API_PROTOCOL=$(python3 -c "from urllib.parse import urlparse; p=urlparse('${hb_api_url_raw}'); print(p.scheme or 'http')" 2>/dev/null || echo "http")
            HB_API_HOST=$(python3 -c "from urllib.parse import urlparse; p=urlparse('${hb_api_url_raw}'); print(p.hostname or 'localhost')" 2>/dev/null || echo "localhost")
            _def_port=$([ "$HB_API_PROTOCOL" = "https" ] && echo "443" || echo "8000")
            HB_API_PORT=$(python3 -c "from urllib.parse import urlparse; p=urlparse('${hb_api_url_raw}'); print(p.port or ${_def_port})" 2>/dev/null || echo "$_def_port")
        fi
        prompt_visible "API admin username" "admin" "hb_username"
        prompt_secret "API admin password" "admin" "hb_password"

        # ── Tailscale option for external API ──────────────
        use_tailscale_remote="${use_tailscale_early:-N}"
        if [[ "${use_tailscale_remote:-}" =~ ^[Yy]$ ]]; then
            if command_exists tailscale && tailscale status >/dev/null 2>&1; then
                msg_ok "Tailscale already connected on this machine"
                ts_auth_key="${ts_auth_key:-}"
            else
            echo ""
            echo -e "  ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
            echo -e "  ${CYAN}  How to get a Tailscale auth key:${RESET}"
            echo -e "  ${CYAN}    1. Create a free account at https://tailscale.com${RESET}"
            echo -e "  ${CYAN}    2. Go to: https://tailscale.com/admin/settings/keys${RESET}"
            echo -e "  ${CYAN}    3. Click 'Generate auth key'${RESET}"
            echo -e "  ${CYAN}    4. Check 'Reusable' for multiple deployments${RESET}"
            echo -e "  ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
            echo ""
            while true; do
                prompt_visible "Tailscale auth key (tskey-auth-...)" "" "ts_auth_key"
                if [ -z "${ts_auth_key:-}" ]; then
                    msg_warn "Auth key cannot be empty"
                    continue
                fi
                if [[ ! "$ts_auth_key" =~ ^tskey-auth- ]]; then
                    msg_warn "Auth key must start with 'tskey-auth-'"
                    continue
                fi
                break
            done
            msg_info "Installing Tailscale on this machine..."
            curl -fsSL https://tailscale.com/install.sh | sh
            msg_info "Connecting to Tailscale network..."
            tailscale_up --authkey="$ts_auth_key" --hostname="condor" --accept-dns=true
            fi
            ts_hostname="hummingbot-api"
            ts_condor_ip=$(tailscale ip -4 2>/dev/null | head -1)

            # Use the Tailscale MagicDNS hostname to reach hummingbot-api (plain HTTP — WireGuard encrypts in transit)
            HB_API_HOST="$ts_hostname"
            HB_API_PORT="8000"
            HB_API_PROTOCOL="http"
            msg_ok "Tailscale connected — server URL: http://$ts_hostname:8000"
            # On a VPS (SERVER_IP set), point the web dashboard at the Tailscale IP so the /web link works remotely
            if [ -n "${SERVER_IP:-}" ] && [ -n "${ts_condor_ip:-}" ]; then
                if grep -q "^WEB_URL=" "$ENV_FILE"; then
                    sed -i.bak "s|^WEB_URL=.*|WEB_URL=http://$ts_condor_ip:8088|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
                else
                    echo "WEB_URL=http://$ts_condor_ip:8088" >> "$ENV_FILE"
                fi
                msg_ok "Web dashboard: http://$ts_condor_ip:8088 (Tailscale access)"
            fi
        fi

        hb_api_configured=true
    else
        # Check Docker (only for hummingbot-api launch)
        if ! command_exists docker; then
            msg_warn "Docker not found. API config will be saved but launch skipped."
            msg_info "Install Docker: https://docs.docker.com/get-docker/"
            docker_available=false
        elif ! docker info >/dev/null 2>&1; then
            msg_warn "Docker is not running. API config will be saved but launch skipped."
            docker_available=false
        else
            docker_available=true
        fi

        # ── Tailscale option for Docker deploy ─────────────
        TS_DEPLOY=false
        ts_auth_key=""
        ts_hb_hostname="hummingbot-api"
        use_tailscale="${use_tailscale_early:-N}"
        if [[ "${use_tailscale:-}" =~ ^[Yy]$ ]]; then
            echo ""
            echo -e "  ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
            echo -e "  ${CYAN}  How to get a Tailscale auth key:${RESET}"
            echo -e "  ${CYAN}    1. Create a free account at https://tailscale.com${RESET}"
            echo -e "  ${CYAN}    2. Go to: https://tailscale.com/admin/settings/keys${RESET}"
            echo -e "  ${CYAN}    3. Click 'Generate auth key'${RESET}"
            echo -e "  ${CYAN}    4. Check 'Reusable' for multiple deployments${RESET}"
            echo -e "  ${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
            echo ""
            while true; do
                prompt_visible "Tailscale auth key (tskey-auth-...)" "" "ts_auth_key"
                if [ -z "${ts_auth_key:-}" ]; then
                    msg_warn "Auth key cannot be empty"
                    continue
                fi
                if [[ ! "$ts_auth_key" =~ ^tskey-auth- ]]; then
                    msg_warn "Auth key must start with 'tskey-auth-'"
                    continue
                fi
                break
            done
            # Hostname defaults to "hummingbot-api" — override TAILSCALE_HOSTNAME in hummingbot-api/.env if needed
            msg_info "Installing Tailscale on this machine..."
            curl -fsSL https://tailscale.com/install.sh | sh
            msg_info "Connecting to Tailscale network..."
            tailscale_up --authkey="$ts_auth_key" --hostname="condor" --accept-dns=true
            ts_condor_ip=$(tailscale ip -4 2>/dev/null | head -1)
            TS_DEPLOY=true
            msg_ok "Tailscale connected — hummingbot-api will be reachable at http://$ts_hb_hostname:8000"
            # On a VPS (SERVER_IP set), point the web dashboard at the Tailscale IP so the /web link works remotely
            if [ -n "${SERVER_IP:-}" ] && [ -n "${ts_condor_ip:-}" ]; then
                if grep -q "^WEB_URL=" "$ENV_FILE"; then
                    sed -i.bak "s|^WEB_URL=.*|WEB_URL=http://$ts_condor_ip:8088|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
                else
                    echo "WEB_URL=http://$ts_condor_ip:8088" >> "$ENV_FILE"
                fi
                msg_ok "Web dashboard: http://$ts_condor_ip:8088 (Tailscale access)"
            fi
        fi

        echo ""
        prompt_visible "API admin username" "admin" "hb_username"
        prompt_secret "API admin password" "admin" "hb_password"
        prompt_secret "Config password" "admin" "hb_config_password"

        # Save to condor's .env
        echo "DEPLOY_HUMMINGBOT_API=true" >> "$ENV_FILE"

        # Clone hummingbot-api if not present
        if [ -d "$HB_API_DIR" ]; then
            msg_ok "hummingbot-api already cloned at $HB_API_DIR"
        else
            msg_info "Cloning hummingbot-api to $HB_API_DIR..."
            if git clone --depth 1 "$HB_API_REPO" "$HB_API_DIR" 2>/dev/null; then
                msg_ok "Cloned hummingbot-api"
            else
                msg_error "Failed to clone hummingbot-api"
                msg_info "You can clone it manually: git clone $HB_API_REPO $HB_API_DIR"
            fi
        fi

        # Generate hummingbot-api .env
        if [ -d "$HB_API_DIR" ]; then
            hb_api_abs_path="$(cd "$HB_API_DIR" 2>/dev/null && pwd)"
            cat > "$HB_API_DIR/.env" << HBEOF
USERNAME=${hb_username}
PASSWORD=${hb_password}
CONFIG_PASSWORD=${hb_config_password}
DEBUG_MODE=false
BROKER_HOST=localhost
BROKER_PORT=1883
BROKER_USERNAME=admin
BROKER_PASSWORD=password
DATABASE_URL=postgresql+asyncpg://hbot:hummingbot-api@localhost:5432/hummingbot_api
GATEWAY_URL=http://localhost:15888
GATEWAY_PASSPHRASE=${hb_config_password}
BOTS_PATH=${hb_api_abs_path}
TAILSCALE_ENABLED=${TS_DEPLOY}
TAILSCALE_AUTH_KEY=${ts_auth_key}
TAILSCALE_HOSTNAME=${ts_hb_hostname}
HBEOF
            msg_ok "Hummingbot API .env configured"

            # Generate docker-compose.tailscale.yml if Tailscale is enabled
            if [ "$TS_DEPLOY" = true ] && [ ! -f "$HB_API_DIR/docker-compose.tailscale.yml" ]; then
                cat > "$HB_API_DIR/docker-compose.tailscale.yml" << 'TSEOF'
services:
  tailscale:
    image: tailscale/tailscale:latest
    container_name: hummingbot-tailscale
    network_mode: host
    environment:
      - TS_AUTHKEY=${TAILSCALE_AUTH_KEY}
      - TS_STATE_DIR=/var/lib/tailscale
      - TS_USERSPACE=false
      - TS_HOSTNAME=${TAILSCALE_HOSTNAME:-hummingbot-api}
    volumes:
      - tailscale_state:/var/lib/tailscale
      - /dev/net/tun:/dev/net/tun
    cap_add:
      - NET_ADMIN
      - NET_RAW
    restart: unless-stopped
volumes:
  tailscale_state:
TSEOF
                msg_ok "docker-compose.tailscale.yml created"
            fi

            # Patch hummingbot-api Makefile so future 'make deploy' stays Tailscale-aware
            if [ "$TS_DEPLOY" = true ] && [ -f "$HB_API_DIR/Makefile" ]; then
                python3 - "$HB_API_DIR/Makefile" << 'PYEOF'
import sys
with open(sys.argv[1]) as f:
    content = f.read()
old = "# Deploy with Docker\ndeploy: $(SETUP_SENTINEL)\n\tdocker compose up -d"
new = (
    "# Deploy with Docker (Tailscale-aware: reads TAILSCALE_ENABLED from .env)\n"
    "deploy: $(SETUP_SENTINEL)\n"
    "\t@set -a; [ -f .env ] && . ./.env; set +a; \\\n"
    "\tif [ \"$${TAILSCALE_ENABLED:-false}\" = \"true\" ]; then \\\n"
    "\t\techo \"[INFO] Deploying with Tailscale sidecar...\"; \\\n"
    "\t\tdocker compose -f docker-compose.yml -f docker-compose.tailscale.yml up -d; \\\n"
    "\telse \\\n"
    "\t\tdocker compose up -d; \\\n"
    "\tfi"
)
content = content.replace(old, new)
content = content.replace(
    ".PHONY: setup run run-https deploy stop install uninstall build install-pre-commit generate-certs show-certs",
    ".PHONY: setup run run-https deploy stop install uninstall build install-pre-commit generate-certs show-certs tailscale-status"
)
if "tailscale-status" not in content:
    content += (
        "\n# Show Tailscale connection status\n"
        "tailscale-status:\n"
        "\t@if command -v tailscale >/dev/null 2>&1; then \\\n"
        "\t\ttailscale status; \\\n"
        "\telse \\\n"
        "\t\techo \"Tailscale is not installed or not on PATH.\"; \\\n"
        "\tfi\n"
    )
with open(sys.argv[1], "w") as f:
    f.write(content)
PYEOF
                msg_ok "Hummingbot API Makefile patched for Tailscale-aware deploy"
            fi

            # Deploy if Docker is available
            if [ "$docker_available" = true ] && [ -f "$HB_API_DIR/docker-compose.yml" ]; then
                msg_info "Starting Hummingbot API stack..."
                if [ "$TS_DEPLOY" = true ] && [ -f "$HB_API_DIR/docker-compose.tailscale.yml" ]; then
                    _compose_cmd="docker compose -f docker-compose.yml -f docker-compose.tailscale.yml up -d"
                    msg_info "Using Tailscale sidecar overlay..."
                else
                    _compose_cmd="docker compose up -d"
                fi
                if (cd "$HB_API_DIR" && eval "$_compose_cmd" 2>/dev/null); then
                    msg_ok "Hummingbot API stack started"

                    # Wait for API to be healthy
                    msg_info "Waiting for API to be ready..."
                    for i in $(seq 1 30); do
                        if api_health_check; then
                            msg_ok "Hummingbot API is healthy"
                            break
                        fi
                        sleep 2
                    done
                    if ! api_health_check; then
                        msg_warn "API not responding yet (may still be starting)"
                        msg_info "Check status: cd $HB_API_DIR && docker compose ps"
                    fi
                else
                    msg_error "Failed to start Hummingbot API stack"
                    msg_info "Try manually: cd $HB_API_DIR && docker compose up -d"
                fi
            else
                msg_info "Start API later: cd $HB_API_DIR && docker compose up -d"
            fi

            msg_ok "Hummingbot API credentials saved"
            hb_api_deployed=true
        fi
    fi
    fi  # end existing_api != skip
fi

echo ""

# ── Step 3: Create/Update config.yml ─────────────────

echo -e "${BOLD}Step 3: Configuration Files${RESET}"
echo ""

# Get current date
current_date=$(date "+%Y-%m-%d")

# Source .env to get ADMIN_USER_ID
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE" 2>/dev/null
    set +a
fi

# Always create/update config.yml with template
if [ ! -f "$CONFIG_FILE" ] || [ ! -s "$CONFIG_FILE" ]; then
    msg_info "Creating $CONFIG_FILE with template..."
    cat > "$CONFIG_FILE" << 'CONFIGEOF'
servers:
  local:
    host: localhost
    port: 8000
    username: admin
    password: admin

default_server: local

admin_id: ADMIN_USER_ID_PLACEHOLDER

users: {}

server_access:
  local:
    owner_id: ADMIN_USER_ID_PLACEHOLDER
    created_at: null
    shared_with: {}

chat_defaults: 
    ADMIN_USER_ID_PLACEHOLDER: local

version: 1
CONFIGEOF
    msg_ok "Created $CONFIG_FILE with template"
fi

# Replace placeholders if they exist and we have values
config_updated=false

if [ -n "${ADMIN_USER_ID:-}" ]; then
    if grep -q "ADMIN_USER_ID_PLACEHOLDER" "$CONFIG_FILE" 2>/dev/null; then
        sed -i.bak "s/ADMIN_USER_ID_PLACEHOLDER/$ADMIN_USER_ID/g" "$CONFIG_FILE"
        rm -f "$CONFIG_FILE.bak"
        msg_ok "Set authorized user ID in $CONFIG_FILE"
        config_updated=true
    fi
fi

if grep -q "DATE_PLACEHOLDER" "$CONFIG_FILE" 2>/dev/null; then
    sed -i.bak "s/DATE_PLACEHOLDER/$current_date/g" "$CONFIG_FILE"
    rm -f "$CONFIG_FILE.bak"
    if [ "$config_updated" = false ]; then
        msg_ok "Updated $CONFIG_FILE with current date"
    fi
    config_updated=true
fi

# If API was deployed, sync credentials (and Tailscale host if applicable) to config.yml
if [ "${hb_api_deployed:-}" = true ]; then
    # Determine credentials (re-read from HB API .env if we didn't just set them)
    if [ -z "${hb_username:-}" ] && [ -f "$HB_API_DIR/.env" ]; then
        hb_username=$(grep "^USERNAME=" "$HB_API_DIR/.env" 2>/dev/null | cut -d= -f2)
        hb_password=$(grep "^PASSWORD=" "$HB_API_DIR/.env" 2>/dev/null | cut -d= -f2)
    fi

    if [ -n "${hb_username:-}" ]; then
        if grep -A5 "servers:" "$CONFIG_FILE" | grep -q "username:"; then
            sed -i.bak "/servers:/,/^[^ ]/ s/username: .*/username: $hb_username/" "$CONFIG_FILE"
            rm -f "$CONFIG_FILE.bak"
        fi
        if grep -A5 "servers:" "$CONFIG_FILE" | grep -q "password:"; then
            sed -i.bak "/servers:/,/^[^ ]/ s/password: .*/password: $hb_password/" "$CONFIG_FILE"
            rm -f "$CONFIG_FILE.bak"
        fi
        msg_ok "Synced API credentials to $CONFIG_FILE"
    fi

    # When Tailscale is enabled, update the server host to the Tailscale MagicDNS hostname
    # so condor reaches hummingbot-api via the encrypted tailnet even on the same machine
    if [ "${TS_DEPLOY:-false}" = true ]; then
        sed -i.bak "/servers:/,/^[^ ]/ s/host: .*/host: $ts_hb_hostname/" "$CONFIG_FILE" && rm -f "$CONFIG_FILE.bak"
        sed -i.bak "/servers:/,/^[^ ]/ s/port: .*/port: 8000/" "$CONFIG_FILE" && rm -f "$CONFIG_FILE.bak"
        msg_ok "Updated server host to Tailscale hostname: $ts_hb_hostname"
    fi
fi

# If user provided a remote API URL (skipped local deployment), update config.yml
if [ "${hb_api_configured:-false}" = true ] && [ -f "$CONFIG_FILE" ]; then
    if update_config_api_server "$HB_API_HOST" "$HB_API_PORT" "${hb_username:-admin}" "${hb_password:-admin}"; then
        msg_ok "Configured $CONFIG_FILE: ${HB_API_PROTOCOL:-http}://${HB_API_HOST}:${HB_API_PORT}"
    else
        msg_warn "Configured $CONFIG_FILE with a basic edit -- review the servers: section to confirm it looks right"
    fi
fi

if [ "$config_updated" = false ] && [ -f "$CONFIG_FILE" ]; then
    msg_ok "$CONFIG_FILE exists and is configured"
fi

echo ""

# ── Step 4: Data directory ──────────────────────────

if [ ! -d "$DATA_DIR" ]; then
    mkdir -p "$DATA_DIR"
fi

# ── Step 5: Summary ────────────────────────────────

# Source nvm to make node/npm available in current shell
if [ -s "$HOME/.nvm/nvm.sh" ]; then
    \. "$HOME/.nvm/nvm.sh"
fi

echo -e "${BOLD}══════════════════════════════════════════════${RESET}"
echo -e "  ${GREEN}Setup complete!${RESET}"
echo ""
echo -e "  ${BOLD}Installed dependencies:${RESET}"
echo -e "    • uv:         $(command_exists uv && uv --version 2>/dev/null || echo 'not found')"
echo -e "    • tmux:       $(command_exists tmux && tmux -V 2>/dev/null || echo 'not found')"
echo -e "    • node:       $(command_exists node && node --version 2>/dev/null || echo 'not found')"
echo -e "    • npm:        $(command_exists npm && npm --version 2>/dev/null || echo 'not found')"
echo -e "    • typescript: $(command_exists tsc && tsc --version 2>/dev/null || echo 'not installed')"
echo ""
echo -e "  ${BOLD}Configuration:${RESET}"
echo -e "    • Telegram:   $([ -n "${TELEGRAM_TOKEN:-}" ] && echo 'configured' || echo 'not set')"
echo ""
echo -e "  ${BOLD}Next steps:${RESET}"
echo -e "  ${BOLD}make install${RESET}      Install Python dependencies"
echo -e "  ${BOLD}make run${RESET}          Run Condor locally (dev)"
if [ "${hb_api_deployed:-}" = true ]; then
echo ""
echo -e "  Hummingbot API is running — config at ${BOLD}../hummingbot-api/.env${RESET}"
fi
if [ "${TS_DEPLOY:-false}" = true ]; then
echo ""
echo -e "  ${BOLD}Tailscale:${RESET}"
echo -e "    hummingbot-api URL:  http://${ts_hb_hostname}:8000"
if [ -n "${ts_condor_ip:-}" ] && [ -n "${SERVER_IP:-}" ]; then
echo -e "    Web dashboard URL:   http://${ts_condor_ip}:8088  ${CYAN}(Tailscale only)${RESET}"
else
echo -e "    Web dashboard URL:   http://localhost:8088"
fi
echo -e "    Tailscale status:    tailscale status"
elif [[ "${use_tailscale_remote:-}" =~ ^[Yy]$ ]]; then
echo ""
echo -e "  ${BOLD}Tailscale:${RESET}"
echo -e "    hummingbot-api URL:  http://${ts_hostname:-hummingbot-api}:8000"
if [ -n "${ts_condor_ip:-}" ] && [ -n "${SERVER_IP:-}" ]; then
echo -e "    Web dashboard URL:   http://${ts_condor_ip}:8088  ${CYAN}(Tailscale only)${RESET}"
else
echo -e "    Web dashboard URL:   http://localhost:8088"
fi
echo -e "    Tailscale status:    tailscale status"
fi
if [ "${TS_DEPLOY:-false}" = true ] || [[ "${use_tailscale_remote:-}" =~ ^[Yy]$ ]]; then
echo ""
echo -e "  ${BOLD}Accessing the web dashboard from another device:${RESET}"
echo -e "  ${CYAN}  Install Tailscale on that device first, then connect with the same key:${RESET}"
echo -e "    Linux / WSL:   curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up --authkey=${ts_auth_key}"
echo -e "    macOS / Win:   https://tailscale.com/download — then run: sudo tailscale up --authkey=${ts_auth_key}"
fi
echo -e "${BOLD}══════════════════════════════════════════════${RESET}"
echo ""
