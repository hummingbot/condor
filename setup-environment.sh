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

    # Prompt for Server IP (optional - for VPS deployments)
    echo ""
    msg_info "If running on a VPS, enter the server's IP address."
    msg_info "Otherwise, press Enter to use localhost."
    prompt_visible "Server IP address (or press Enter for localhost)" "" "server_ip"
    SERVER_IP="$server_ip"

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
        
        # Add WEB_URL and WEB_PORT if server IP was provided
        if [ -n "$SERVER_IP" ]; then
            if grep -q "^WEB_URL=" "$ENV_FILE"; then
                sed -i.bak "s|^WEB_URL=.*|WEB_URL=http://$(escape_env_value "$SERVER_IP")|" "$ENV_FILE"
                rm -f "$ENV_FILE.bak"
            else
                echo "WEB_URL=http://$(escape_env_value "$SERVER_IP")" >> "$ENV_FILE"
            fi
            
            if grep -q "^WEB_PORT=" "$ENV_FILE"; then
                sed -i.bak "s|^WEB_PORT=.*|WEB_PORT=8088|" "$ENV_FILE"
                rm -f "$ENV_FILE.bak"
            else
                echo "WEB_PORT=8088" >> "$ENV_FILE"
            fi
        fi
    else
        {
            echo "TELEGRAM_TOKEN=$(escape_env_value "$TELEGRAM_TOKEN")"
            echo "ADMIN_USER_ID=$(escape_env_value "$ADMIN_USER_ID")"
            if [ -n "$SERVER_IP" ]; then
                echo "WEB_URL=http://$(escape_env_value "$SERVER_IP")"
                echo "WEB_PORT=8088"
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

if [ -n "${DEPLOY_HUMMINGBOT_API:-}" ]; then
    if [ "${DEPLOY_HUMMINGBOT_API:-}" = "true" ]; then
        msg_ok "Hummingbot API already configured (enabled)"
        hb_api_deployed=true
    else
        msg_ok "Hummingbot API already configured (skipped)"
    fi
else
    msg_info "Condor connects to Hummingbot Backend API for trading."
    echo ""
    prompt_visible "Deploy Hummingbot API locally with Docker? [Y/n]" "Y" "deploy_hb"

    if [[ "${deploy_hb:-}" =~ ^[Nn]$ ]]; then
        echo "DEPLOY_HUMMINGBOT_API=false" >> "$ENV_FILE"
        msg_ok "Skipped Hummingbot API deployment"
    else
        # Check Docker
        if ! command_exists docker; then
            msg_warn "Docker not found. Config will be saved but deployment skipped."
            msg_info "Install Docker: https://docs.docker.com/get-docker/"
            docker_available=false
        elif ! docker info >/dev/null 2>&1; then
            msg_warn "Docker is not running. Config will be saved but deployment skipped."
            docker_available=false
        else
            docker_available=true
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
HBEOF
            msg_ok "Hummingbot API .env configured"

            # ── Optional: HTTPS cert generation ──────────────────
            echo ""
            msg_info "You can secure the connection between Condor and Hummingbot API with HTTPS."
            prompt_visible "Enable HTTPS cert generation for Hummingbot API? [y/N]" "N" "enable_https"

            HB_API_HTTPS_ENABLED=false
            HB_API_SSL_PORT=8443
            HB_API_CA_BUNDLE=""
            HB_API_CLIENT_CERT=""
            HB_API_CLIENT_KEY=""

            if [[ "${enable_https:-}" =~ ^[Yy]$ ]]; then
                if ! command_exists openssl; then
                    msg_warn "openssl not found — skipping HTTPS setup."
                    msg_info "Install openssl and re-run setup, or run 'make generate-certs' in the hummingbot-api directory."
                else
                    prompt_visible "HTTPS port" "8443" "ssl_port"
                    ssl_port="${ssl_port:-8443}"
                    prompt_visible "Certificate hostname or IP" "localhost" "ssl_host"
                    ssl_host="${ssl_host:-localhost}"
                    prompt_visible "Also generate client certificate for mTLS? [y/N]" "N" "gen_mtls"

                    certs_dir="$hb_api_abs_path/certs"
                    server_ext="$certs_dir/server-ext.cnf"
                    msg_info "Generating SSL certificates in $certs_dir ..."

                    mkdir -p "$certs_dir"
                    openssl genrsa -out "$certs_dir/ca.key" 4096 >/dev/null 2>&1
                    openssl req -x509 -new -nodes -key "$certs_dir/ca.key" -sha256 -days 3650 \
                        -out "$certs_dir/ca.pem" -subj "/CN=Hummingbot Local CA" >/dev/null 2>&1

                    # SAN is required — Python 3.12+ ssl rejects CN-only certs at hostname verification
                    {
                        echo "subjectAltName = @alt_names"
                        echo "[alt_names]"
                        if [[ "$ssl_host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
                            echo "IP.1 = $ssl_host"
                            echo "DNS.1 = localhost"
                            echo "IP.2 = 127.0.0.1"
                        else
                            echo "DNS.1 = $ssl_host"
                            if [[ "$ssl_host" != "localhost" ]]; then echo "DNS.2 = localhost"; fi
                            echo "IP.1 = 127.0.0.1"
                        fi
                    } > "$server_ext"

                    openssl genrsa -out "$certs_dir/server.key" 2048 >/dev/null 2>&1
                    openssl req -new -key "$certs_dir/server.key" -out "$certs_dir/server.csr" \
                        -subj "/CN=$ssl_host" >/dev/null 2>&1
                    openssl x509 -req -in "$certs_dir/server.csr" -CA "$certs_dir/ca.pem" \
                        -CAkey "$certs_dir/ca.key" -CAcreateserial -out "$certs_dir/server.pem" \
                        -days 825 -sha256 -extfile "$server_ext" >/dev/null 2>&1
                    rm -f "$certs_dir/server.csr" "$server_ext"

                    if [[ "${gen_mtls:-}" =~ ^[Yy]$ ]]; then
                        openssl genrsa -out "$certs_dir/client.key" 2048 >/dev/null 2>&1
                        openssl req -new -key "$certs_dir/client.key" -out "$certs_dir/client.csr" \
                            -subj "/CN=condor-client" >/dev/null 2>&1
                        openssl x509 -req -in "$certs_dir/client.csr" -CA "$certs_dir/ca.pem" \
                            -CAkey "$certs_dir/ca.key" -CAserial "$certs_dir/ca.srl" \
                            -out "$certs_dir/client.pem" -days 825 -sha256 >/dev/null 2>&1
                        rm -f "$certs_dir/client.csr"
                        HB_API_CLIENT_CERT="$certs_dir/client.pem"
                        HB_API_CLIENT_KEY="$certs_dir/client.key"
                    fi

                    msg_ok "Certificates generated"

                    # Update hummingbot-api .env with SSL settings
                    _upsert_hbenv() {
                        local file="$1" key="$2" val="$3"
                        if grep -q "^${key}=" "$file" 2>/dev/null; then
                            local tmp; tmp="$(mktemp)"
                            awk -v k="$key" -v v="$val" \
                                'BEGIN{FS=OFS="="} $1==k{print k "=" v; next} {print}' \
                                "$file" > "$tmp" && mv "$tmp" "$file"
                        else
                            echo "${key}=${val}" >> "$file"
                        fi
                    }

                    hb_env="$hb_api_abs_path/.env"
                    _upsert_hbenv "$hb_env" "SSL_ENABLED"  "true"
                    _upsert_hbenv "$hb_env" "SSL_PORT"     "$ssl_port"
                    _upsert_hbenv "$hb_env" "SSL_CERT_PATH" "$certs_dir/server.pem"
                    _upsert_hbenv "$hb_env" "SSL_KEY_PATH"  "$certs_dir/server.key"
                    _upsert_hbenv "$hb_env" "SSL_CA_PATH"   "$certs_dir/ca.pem"
                    if [ -n "$HB_API_CLIENT_CERT" ]; then
                        _upsert_hbenv "$hb_env" "SSL_CLIENT_CERT_PATH" "$HB_API_CLIENT_CERT"
                        _upsert_hbenv "$hb_env" "SSL_CLIENT_KEY_PATH"  "$HB_API_CLIENT_KEY"
                    fi
                    msg_ok "Hummingbot API .env updated with SSL settings"

                    HB_API_HTTPS_ENABLED=true
                    HB_API_SSL_PORT="$ssl_port"
                    HB_API_CA_BUNDLE="$certs_dir/ca.pem"
                fi
            fi

            # Deploy if Docker is available
            if [ "$docker_available" = true ] && [ -f "$HB_API_DIR/docker-compose.yml" ]; then
                msg_info "Starting Hummingbot API stack..."
                if (cd "$HB_API_DIR" && docker compose up -d 2>/dev/null); then
                    msg_ok "Hummingbot API stack started"

                    # Wait for API to be healthy
                    msg_info "Waiting for API to be ready..."
                    if [ "${HB_API_HTTPS_ENABLED:-false}" = true ]; then
                        _health_url="https://localhost:${HB_API_SSL_PORT}/docs"
                        _curl_opts="--cacert $HB_API_CA_BUNDLE"
                    else
                        _health_url="http://localhost:8000/docs"
                        _curl_opts=""
                    fi
                    for i in $(seq 1 30); do
                        # shellcheck disable=SC2086
                        if curl -sf $_curl_opts "$_health_url" >/dev/null 2>&1; then
                            msg_ok "Hummingbot API is healthy"
                            hb_api_deployed=true
                            break
                        fi
                        sleep 2
                    done
                    if [ "$hb_api_deployed" = false ]; then
                        msg_warn "API not responding yet (may still be starting)"
                        msg_info "Check status: cd $HB_API_DIR && docker compose ps"
                        msg_info "API URL: $_health_url"
                        hb_api_deployed=true  # Config is still valid
                    fi
                else
                    msg_error "Failed to start Hummingbot API stack"
                    msg_info "Try manually: cd $HB_API_DIR && docker compose up -d"
                    hb_api_deployed=true  # Config is still valid
                fi
            elif [ "$docker_available" = false ]; then
                msg_info "Start it later: cd $HB_API_DIR && docker compose up -d"
                hb_api_deployed=true  # Config is valid, just not running
            fi
        fi
    fi
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

# If API was deployed, sync credentials to config.yml
if [ "${hb_api_deployed:-}" = true ]; then
    # Determine credentials (re-read from HB API .env if we didn't just set them)
    if [ -z "${hb_username:-}" ] && [ -f "$HB_API_DIR/.env" ]; then
        hb_username=$(grep "^USERNAME=" "$HB_API_DIR/.env" 2>/dev/null | cut -d= -f2)
        hb_password=$(grep "^PASSWORD=" "$HB_API_DIR/.env" 2>/dev/null | cut -d= -f2)
    fi

    if [ -n "${hb_username:-}" ]; then
        # Update config.yml 'local' server credentials using sed
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

    # If HTTPS was configured in this run, update the local server entry in config.yml
    if [ "${HB_API_HTTPS_ENABLED:-false}" = true ] && [ -f "$CONFIG_FILE" ]; then
        _apply_https_to_config() {
            local cfg="$1" port="$2" ca="$3" cert="${4:-}" key="${5:-}"

            # Prefer Python+PyYAML (handles re-runs and arbitrary config structure cleanly)
            for py in python3 "uv run python"; do
                if $py -c "import yaml" 2>/dev/null; then
                    YAML_CFG="$cfg" YAML_PORT="$port" YAML_CA="$ca" \
                    YAML_CERT="$cert" YAML_KEY="$key" \
                    $py - << 'PYEOF'
import os, yaml

cfg = os.environ["YAML_CFG"]
with open(cfg) as f:
    data = yaml.safe_load(f) or {}

srv = data.setdefault("servers", {}).setdefault("local", {})
srv["port"] = int(os.environ["YAML_PORT"])
srv["protocol"] = "https"
srv["tls_verify"] = True
srv["ca_bundle_path"] = os.environ["YAML_CA"]
cert = os.environ.get("YAML_CERT", "")
key = os.environ.get("YAML_KEY", "")
if cert:
    srv["client_cert_path"] = cert
    srv["client_key_path"] = key
else:
    srv.pop("client_cert_path", None)
    srv.pop("client_key_path", None)

with open(cfg, "w") as f:
    yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
PYEOF
                    return $?
                fi
            done

            # Fallback: awk-based update for the known config.yml template structure.
            # Updates port in-place; appends TLS fields after the password line if not present.
            local tmp; tmp="$(mktemp)"
            awk -v port="$port" -v ca="$ca" -v cert="$cert" -v key="$key" '
                /^  local:/             { in_local=1 }
                in_local && /^[^ ]/     { in_local=0 }
                in_local && /^  [^ ]/ && !/^  local:/ { in_local=0 }

                in_local && /^ *port:/ { print "    port: " port; next }

                in_local && /^ *password:/ {
                    print
                    # Only add TLS fields if not already present (idempotency guard)
                    if (!tls_added) {
                        print "    protocol: https"
                        print "    tls_verify: true"
                        print "    ca_bundle_path: " ca
                        if (cert != "") {
                            print "    client_cert_path: " cert
                            print "    client_key_path: " key
                        }
                        tls_added=1
                    }
                    next
                }

                # Drop stale TLS fields so re-runs stay clean
                in_local && /^ *(protocol|tls_verify|ca_bundle_path|client_cert_path|client_key_path):/ { next }

                { print }
            ' "$cfg" > "$tmp" && mv "$tmp" "$cfg"
        }

        _apply_https_to_config "$CONFIG_FILE" "$HB_API_SSL_PORT" "$HB_API_CA_BUNDLE" \
            "$HB_API_CLIENT_CERT" "$HB_API_CLIENT_KEY"
        msg_ok "Updated $CONFIG_FILE with HTTPS settings (port $HB_API_SSL_PORT)"
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
echo -e "  ${BOLD}Next steps:${RESET}"
echo -e "  ${BOLD}make install${RESET}      Install Python dependencies"
echo -e "  ${BOLD}make run${RESET}          Run Condor locally (dev)"
echo -e "  ${BOLD}make deploy${RESET}       Deploy Condor (Docker)"
if [ "${hb_api_deployed:-}" = true ]; then
echo -e "  ${BOLD}make deploy-full${RESET}  Deploy Condor + Hummingbot API (Docker)"
fi
echo -e "  ${BOLD}make stop${RESET}         Stop everything"
echo -e "  ${BOLD}make status${RESET}       Show container status"
echo -e "${BOLD}══════════════════════════════════════════════${RESET}"
echo ""
