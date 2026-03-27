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
    if [ -n "$TERM" ] && [ "$TERM" != "dumb" ]; then
        echo -e "\033]8;;${url}\033\\${text}\033]8;;\033\\"
    else
        echo "$text ($url)"
    fi
}

# ── Banner ───────────────────────────────────────────

echo ""
echo -e "${BOLD}╔═══════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║            Condor Setup                   ║${RESET}"
echo -e "${BOLD}╚═══════════════════════════════════════════╝${RESET}"
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

if [ -n "$TELEGRAM_TOKEN" ] && [ -n "$ADMIN_USER_ID" ]; then
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
    else
        {
            echo "TELEGRAM_TOKEN=$(escape_env_value "$TELEGRAM_TOKEN")"
            echo "ADMIN_USER_ID=$(escape_env_value "$ADMIN_USER_ID")"
        } > "$ENV_FILE"
    fi

    msg_ok ".env created"
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

if [ -n "$DEPLOY_HUMMINGBOT_API" ]; then
    if [ "$DEPLOY_HUMMINGBOT_API" = "true" ]; then
        msg_ok "Hummingbot API already configured (enabled)"
        hb_api_deployed=true
    else
        msg_ok "Hummingbot API already configured (skipped)"
    fi
else
    msg_info "Condor connects to Hummingbot Backend API for trading."
    echo ""
    prompt_visible "Deploy Hummingbot API locally with Docker? [Y/n]" "Y" "deploy_hb"

    if [[ "$deploy_hb" =~ ^[Nn]$ ]]; then
        echo "DEPLOY_HUMMINGBOT_API=false" >> "$ENV_FILE"
        msg_ok "Skipped Hummingbot API deployment"
    else
        # Check Docker
        if ! command -v docker >/dev/null 2>&1; then
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

            # Deploy if Docker is available
            if [ "$docker_available" = true ] && [ -f "$HB_API_DIR/docker-compose.yml" ]; then
                msg_info "Starting Hummingbot API stack..."
                if (cd "$HB_API_DIR" && docker compose up -d 2>/dev/null); then
                    msg_ok "Hummingbot API stack started"

                    # Wait for API to be healthy
                    msg_info "Waiting for API to be ready..."
                    for i in $(seq 1 30); do
                        if curl -sf http://localhost:8000/docs >/dev/null 2>&1; then
                            msg_ok "Hummingbot API is healthy"
                            hb_api_deployed=true
                            break
                        fi
                        sleep 2
                    done
                    if [ "$hb_api_deployed" = false ]; then
                        msg_warn "API not responding yet (may still be starting)"
                        msg_info "Check status: cd $HB_API_DIR && docker compose ps"
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

# ── Step 3: Auto-register server in config.yml ─────

if [ "$hb_api_deployed" = true ]; then
    # Determine credentials (re-read from HB API .env if we didn't just set them)
    if [ -z "$hb_username" ] && [ -f "$HB_API_DIR/.env" ]; then
        hb_username=$(grep "^USERNAME=" "$HB_API_DIR/.env" 2>/dev/null | cut -d= -f2)
        hb_password=$(grep "^PASSWORD=" "$HB_API_DIR/.env" 2>/dev/null | cut -d= -f2)
    fi

    if [ -n "$hb_username" ]; then
        # Check if config.yml already has a server entry
        if [ -f "$CONFIG_FILE" ] && grep -q "^servers:" "$CONFIG_FILE" && grep -q "  local:" "$CONFIG_FILE"; then
            msg_ok "Server 'local' already registered in $CONFIG_FILE"
        else
            cat > "$CONFIG_FILE" << CFGEOF
servers:
  local:
    host: localhost
    port: 8000
    username: ${hb_username}
    password: ${hb_password}
CFGEOF
            msg_ok "Registered 'local' server in $CONFIG_FILE"
        fi
    fi
fi

# ── Step 4: Data directory ──────────────────────────

if [ ! -d "$DATA_DIR" ]; then
    mkdir -p "$DATA_DIR"
fi

if [ ! -f "$CONFIG_FILE" ]; then
    touch "$CONFIG_FILE"
fi

# ── Step 5: Summary ────────────────────────────────

echo -e "${BOLD}══════════════════════════════════════════════${RESET}"
echo -e "  ${GREEN}Setup complete!${RESET}"
echo ""
echo -e "  ${BOLD}make run${RESET}          Run Condor locally (dev)"
echo -e "  ${BOLD}make deploy${RESET}       Deploy Condor (Docker)"
if [ "$hb_api_deployed" = true ]; then
echo -e "  ${BOLD}make deploy-full${RESET}  Deploy Condor + Hummingbot API (Docker)"
fi
echo -e "  ${BOLD}make stop${RESET}         Stop everything"
echo -e "  ${BOLD}make status${RESET}       Show container status"
echo -e "${BOLD}══════════════════════════════════════════════${RESET}"
echo ""
