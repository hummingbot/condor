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

# Prompt for passwords (no echo). Never echoes ``default`` in cleartext --
# unlike prompt_visible, a bracketed hint here would defeat the whole point
# of a masked prompt the moment a caller passed one.
#
# Only the *edges* are trimmed. prompt_visible strips all whitespace, which is
# right for a token or a hostname but wrong for a password: the user would
# type "correct horse battery", get "correcthorsebattery" written to two
# different .env files, and then be unable to log in anywhere with the
# passphrase they believe they set. Inner whitespace is rejected instead --
# see prompt_required_secret -- because .env is read by three different
# parsers (python-dotenv, docker compose, and `source`) that do not agree on
# how to quote it.
prompt_secret() {
    local prompt="$1"
    local default="$2"
    local var_name="$3"
    if [ -n "$default" ]; then
        echo -ne "  ${prompt} ${DIM}[hidden]${RESET}: " >&2
    else
        echo -ne "  ${prompt}: " >&2
    fi
    read -rs value < /dev/tty || value=""
    echo "" >&2
    value="$(printf '%s' "$value" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    if [ -z "$value" ] && [ -n "$default" ]; then
        value="$default"
    fi
    eval "$var_name=\"$value\""
}

# Prompt visibly, looping until non-empty. No default -- for values (like
# credentials) that must never silently fall back to something guessable.
prompt_required_visible() {
    local prompt="$1" var_name="$2" warn_msg="${3:-This cannot be empty}"
    while true; do
        prompt_visible "$prompt" "" "$var_name"
        if [ -z "${!var_name}" ]; then
            msg_warn "$warn_msg"
            continue
        fi
        break
    done
}

# Same as prompt_required_visible, but masked (see prompt_secret).
prompt_required_secret() {
    local prompt="$1" var_name="$2" warn_msg="${3:-This cannot be empty}"
    while true; do
        prompt_secret "$prompt" "" "$var_name"
        if [ -z "${!var_name}" ]; then
            msg_warn "$warn_msg"
            continue
        fi
        if [[ "${!var_name}" =~ [[:space:]] ]]; then
            msg_warn "Spaces are not supported in this value -- please retype it without any"
            continue
        fi
        break
    done
}

# Escape special characters for .env file
escape_env_value() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//\$/\\\$}"
    echo "$value"
}

# Set (or replace) KEY=VALUE in .env, creating the file if it does not exist.
set_env_var() {
    local key="$1"
    local value
    value="$(escape_env_value "$2")"
    if [ ! -f "$ENV_FILE" ]; then
        touch "$ENV_FILE"
        # .env holds the bot token and the API password. 644 (the usual umask
        # default) means every other account on a shared box or VPS can read
        # them. Best-effort: a no-op on filesystems without POSIX modes.
        chmod 600 "$ENV_FILE" 2>/dev/null || true
    fi
    if grep -q "^${key}=" "$ENV_FILE"; then
        sed -i.bak "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
        rm -f "$ENV_FILE.bak"
    else
        echo "${key}=${value}" >> "$ENV_FILE"
    fi
}

# Read one KEY out of .env *without executing the file*.
#
# `source` runs .env as a shell script, so one value containing shell
# metacharacters (an API key with parentheses is enough) is a syntax error that
# aborts the read and silently drops every variable below that line. That is how
# a hand-added CONDOR_MODE=local could become invisible here — and then be
# rewritten to `telegram` by the legacy-install migration below. The variables
# that decide the mode are read with this instead.
read_env_var() {
    local key="$1" line
    [ -f "$ENV_FILE" ] || return 0
    line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" "$ENV_FILE" 2>/dev/null | tail -1)" || return 0
    [ -n "$line" ] || return 0
    line="${line#*=}"
    line="$(printf '%s' "$line" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    case "$line" in
        \"*\") line="${line%\"}"; line="${line#\"}" ;;
        \'*\') line="${line%\'}"; line="${line#\'}" ;;
    esac
    printf '%s' "$line"
}

# What local mode costs, said out loud. It is a dashboard with no login at all,
# so the only thing standing between it and the network is the loopback bind.
local_mode_warning() {
    echo ""
    msg_warn "Local mode has NO login — whoever reaches the dashboard controls your funds"
    echo -e "    • It binds ${BOLD}127.0.0.1${RESET} only: reachable from this machine, nowhere else."
    echo -e "    • Exposing it takes an explicit ${BOLD}WEB_HOST=0.0.0.0${RESET} in .env, which puts full"
    echo -e "      trading control in reach of anyone who can hit the port. Only do that behind"
    echo -e "      something that authenticates (Tailscale, an SSH tunnel, a reverse proxy)."
    echo -e "    • Dashboard: ${BOLD}http://localhost:8088${RESET}"
    echo ""
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

# Quick localhost API probe — avoid hanging Step 3 when port 8000 is slow/unreachable
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

# Does anything already own this host's tailnet interface?
#
# Prints: sidecar | native | none. Mirrors hummingbot-api's tailnet-state.sh --
# duplicated deliberately, because Condor installs on machines where that
# checkout does not exist.
#
# Detects the RESOURCE, not the sibling product: the most common conflict is a
# VPS already running Tailscale for the admin's own SSH access, which no
# "is hummingbot-api here?" check would ever see. Two kernel-mode tailscaled
# in one network namespace is fatal and silent -- the loser dies with
# tstun.New("tailscale0"): device or resource busy while its container keeps
# reporting "running".
tailnet_state() {
    if command_exists docker &&
       docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'hummingbot-tailscale'; then
        echo sidecar; return
    fi
    if pgrep -x tailscaled >/dev/null 2>&1; then echo native; return; fi
    if ip link show tailscale0 >/dev/null 2>&1; then echo native; return; fi
    if command_exists tailscale && tailscale status >/dev/null 2>&1; then echo native; return; fi
    echo none
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

# ── Step 1: Run mode (Telegram or local) ────────────

echo -e "${BOLD}Step 1: How will you use Condor?${RESET}"
echo ""

telegram_configured=false

# Source existing .env if present. Best-effort only: the rest of the wizard
# reads incidental values (DEPLOY_HUMMINGBOT_API, CONDOR_DEFAULT_AGENT, the
# Tailscale settings) from here, but nothing that decides the *mode* — those are
# re-read below with read_env_var, which cannot be defeated by one unquoted
# value halfway down the file.
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE" 2>/dev/null
    set +a
fi

if [ -f "$ENV_FILE" ]; then
    CONDOR_MODE="$(read_env_var CONDOR_MODE)"
    TELEGRAM_TOKEN="$(read_env_var TELEGRAM_TOKEN)"
    ADMIN_USER_ID="$(read_env_var ADMIN_USER_ID)"
fi

# An install from before local mode existed has a token but no CONDOR_MODE.
# That is telegram — recorded now, so the mode is always explicit on disk and
# is never inferred later from whether a token happens to be there.
if [ -z "${CONDOR_MODE:-}" ] && [ -n "${TELEGRAM_TOKEN:-}" ] && [ -n "${ADMIN_USER_ID:-}" ]; then
    CONDOR_MODE="telegram"
    set_env_var CONDOR_MODE "telegram"
fi

# A configured install is *offered the other mode*, never just told it is
# already configured. Switching is the one thing a re-run has to be able to do:
# the boot errors for a broken mode all say "run make setup", and that has to be
# advice that leads somewhere.
choose_mode() {
    echo -e "    ${BOLD}1) Telegram${RESET}  — control Condor from a Telegram bot (recommended)"
    echo -e "    ${BOLD}2) Local${RESET}     — no Telegram; the dashboard runs on this machine, no login"
    echo ""
    while true; do
        prompt_visible "Choose [1/2]" "$1" "condor_mode_choice"
        case "${condor_mode_choice:-}" in
            1|2) break ;;
            *) msg_warn "Enter 1 (Telegram) or 2 (Local)" ;;
        esac
    done
    echo ""
}

if [ "${CONDOR_MODE:-}" = "local" ]; then
    msg_ok "Currently: Local mode — dashboard only, no Telegram"
    echo ""
    prompt_visible "Keep local mode? [Y/n]" "Y" "keep_mode"
    echo ""
    if [[ "${keep_mode:-Y}" =~ ^[Nn]$ ]]; then
        choose_mode "1"
    else
        condor_mode_choice=2
    fi
elif [ -n "${TELEGRAM_TOKEN:-}" ] && [ -n "${ADMIN_USER_ID:-}" ]; then
    msg_ok "Currently: Telegram (bot configured, admin ${ADMIN_USER_ID})"
    echo ""
    prompt_visible "Keep Telegram mode? [Y/n]" "Y" "keep_mode"
    echo ""
    if [[ "${keep_mode:-Y}" =~ ^[Nn]$ ]]; then
        choose_mode "2"
    else
        telegram_configured=true
        load_tailscale_choice
    fi
else
    choose_mode "1"
fi

if [ "${condor_mode_choice:-}" = "2" ]; then
    # Local mode: nothing to ask about a bot. No Telegram, no token — the
    # dashboard logs in as ADMIN_USER_ID, the same id config.yml, chat
    # defaults, preferences, memory and session keys already key on.
    #
    # An existing numeric id is KEPT, never overwritten with 1: a Telegram
    # install switching to local keeps its own admin, so the dashboard logs in
    # as you with every server, preference and default intact. 1 is only the
    # answer for an install that never had a Telegram id.
    CONDOR_MODE="local"
    if [[ "${ADMIN_USER_ID:-}" =~ ^[1-9][0-9]*$ ]]; then
        msg_info "Local mode will log in as user ${ADMIN_USER_ID} (ADMIN_USER_ID in .env)"
    else
        ADMIN_USER_ID="1"
        set_env_var ADMIN_USER_ID "1"
    fi
    set_env_var CONDOR_MODE "local"
    set_env_var WEB_URL "http://localhost:8088"
    msg_ok ".env configured for local mode"
    local_mode_warning

    # Tailscale is not just an option here — it is the answer to the warning
    # above: an unauthenticated dashboard is only as safe as what can actually
    # reach it. Asked the same way Telegram mode asks it (below), so local
    # mode gets the identical Tailscale install/connect treatment in Step 3
    # for hummingbot-api — and for the dashboard itself, main.py's _run_dual()
    # binds loopback and proxies it via `tailscale serve` whenever
    # USE_TAILSCALE is set, local mode or not.
    prompt_visible "Use Tailscale to secure the dashboard and the hummingbot-api connection? [y/N]" "N" "use_tailscale_early"
    if [[ "${use_tailscale_early:-}" =~ ^[Yy]$ ]]; then
        USE_TAILSCALE=true
    else
        USE_TAILSCALE=false
    fi
    set_env_var USE_TAILSCALE "$USE_TAILSCALE"
elif [ "${condor_mode_choice:-}" = "1" ]; then
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
        chmod 600 "$ENV_FILE" 2>/dev/null || true
    fi

    # The mode is written explicitly even for the default, so nothing downstream
    # has to guess it from the presence of a token.
    CONDOR_MODE="telegram"
    set_env_var CONDOR_MODE "telegram"

    msg_ok ".env created"
    if [ -n "$SERVER_IP" ]; then
        msg_ok "Web server configured: http://$SERVER_IP:8088"
    fi
    telegram_configured=true
fi

echo ""

# ── Step 2: AI Model (LLM) ──────────────────────────

echo -e "${BOLD}Step 2: AI Model (LLM)${RESET}"
echo ""

# The wizard (condor.setup_llm) renders the same readiness probes the bot
# uses, so it has to run in the project's Python env. Sync it FIRST, before
# asking anything below -- otherwise `uv run`'s own venv-creation/install
# output shows up sandwiched between "Pick an AI model now?" and the actual
# model menu, which reads as the prompt getting interrupted mid-conversation
# rather than as one continuous step. Unconditional (not gated behind the
# Y/n) since both branches below need it: picking a model runs the wizard
# directly, and skipping still needs it synced for `--status`/no-tty.
msg_info "Setting up Condor's Python environment (~250MB first run, 1-3 min)..."
uv run python -c "pass"
echo ""

if (: </dev/tty) 2>/dev/null; then
    prompt_visible "Pick an AI model now? [Y/n]" "Y" "pick_model_now"
    if [[ "${pick_model_now:-Y}" =~ ^[Nn]$ ]]; then
        msg_ok "Skipped -- run 'make pick-model' any time"
    else
        # The venv is already warm above, so this goes straight to the model
        # menu -- and if the model picked (or kept) needs a CLI bridge that
        # isn't installed yet (e.g. `npm install -g @google/gemini-cli`),
        # installs and confirms it right here, no separate pass afterward.
        uv run python -m condor.setup_llm < /dev/tty || \
            msg_warn "Model selection did not complete -- run 'make pick-model' later"
    fi
else
    msg_info "No terminal available -- skipping model selection"
    uv run python -m condor.setup_llm --status || true
fi

echo ""

# ── Step 3: Hummingbot API ──────────────────────────

echo -e "${BOLD}Step 3: Hummingbot API${RESET}"
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
            set_env_var DEPLOY_HUMMINGBOT_API "false"
            msg_ok "Keeping existing API instance"
            hb_api_deployed=false

            # Keeping someone else's API still means Condor has to log in to
            # it. Skipping this used to leave config.yml on the template's
            # placeholder credentials, so the very next thing the user saw was
            # every command failing with a 401 and no hint as to why.
            echo ""
            msg_info "Condor still needs this API's credentials to talk to it."
            msg_info "They are USERNAME / PASSWORD in that instance's .env."
            prompt_required_visible "API admin username" "hb_username" "Username cannot be empty"
            prompt_required_secret "API admin password" "hb_password" "Password cannot be empty"
            HB_API_PROTOCOL="http"
            HB_API_HOST="localhost"
            HB_API_PORT="8000"
            hb_api_configured=true

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
    # An install that exists on disk is protected even when it is not running.
    # The api_health_check above only sees a *live* API on :8000, so a stopped
    # stack used to fall straight through to the deploy branch, which
    # overwrote .env -- replacing generated broker credentials with weak ones
    # while the broker's own bootstrap file (and its mnesia volume) kept the
    # old password. That mismatch is not repairable by re-running setup; it
    # needs `make emqx-auth-reset`. So the default flips to "n" here, and the
    # existing .env becomes read-only input.
    hb_api_preexisting=false
    if [ -f "$HB_API_DIR/.env" ]; then
        hb_api_preexisting=true
        msg_ok "Existing hummingbot-api install detected at $HB_API_DIR"
        msg_info "Its .env (API and broker credentials) will be left untouched."
        # "Restart", not "reconfigure": .env stays untouched either way -- this
        # only decides whether Condor brings the stack up via `make deploy`.
        prompt_visible "Restart its Docker stack now? [y/N]" "N" "redeploy_hb"
        if [[ "${redeploy_hb:-}" =~ ^[Yy]$ ]]; then
            deploy_hb="y"
        else
            deploy_hb="n"
        fi
    else
        prompt_visible "Configure and launch local Hummingbot API with Docker? [Y/n]" "Y" "deploy_hb"
    fi
    fi

    if [[ "${deploy_hb:-}" =~ ^[Nn]$ ]]; then
        if [ "$finish_remote_api" != true ]; then
            set_env_var DEPLOY_HUMMINGBOT_API "false"
        fi
        msg_ok "Skipped Hummingbot API deployment"
        echo ""
        if [ "${hb_api_preexisting:-false}" = true ]; then
            # Co-located install we chose not to touch: its credentials are
            # already on disk, so asking the user to retype them invites a
            # typo and a 401. localhost, not the tailnet name -- Condor is on
            # this machine, so MagicDNS would be a longer route to the same
            # port and one more thing that can be down.
            hb_username=$(grep -m1 "^USERNAME=" "$HB_API_DIR/.env" 2>/dev/null | cut -d= -f2-)
            hb_password=$(grep -m1 "^PASSWORD=" "$HB_API_DIR/.env" 2>/dev/null | cut -d= -f2-)
            HB_API_PROTOCOL="http"
            HB_API_HOST="localhost"
            HB_API_PORT="8000"
            if [ -n "${hb_username:-}" ] && [ -n "${hb_password:-}" ]; then
                msg_ok "Read API credentials from $HB_API_DIR/.env"
            else
                msg_warn "Could not read USERNAME/PASSWORD from $HB_API_DIR/.env"
                prompt_required_visible "API admin username" "hb_username" "Username cannot be empty"
                prompt_required_secret "API admin password" "hb_password" "Password cannot be empty"
            fi
            hb_api_configured=true
        else
        msg_info "Enter the Hummingbot API connection details."
        if [[ "${use_tailscale_early:-}" =~ ^[Yy]$ ]]; then
            # Tailscale: host is resolved via MagicDNS after joining the tailnet — no URL needed
            HB_API_PROTOCOL="http"
            HB_API_HOST="hummingbot-api"
            HB_API_PORT="8000"
            # Skipping local deploy + using Tailscale means hummingbot-api is
            # on another machine (a VPS, most likely) -- this is the default
            # hostname its own Tailscale setup assigns it, not a guess made
            # up here.
            msg_info "Assuming the default tailnet address: http://hummingbot-api:8000"
            msg_info "If that machine's hummingbot-api used a different TAILSCALE_HOSTNAME"
            msg_info "(or you renamed the device in the Tailscale admin console), update the"
            msg_info "host afterward in config.yml, or via /servers in Telegram."
        else
            prompt_visible "API URL + port (e.g. http://your-server:8000)" "http://localhost:8000" "hb_api_url_raw"
            hb_api_url_raw="${hb_api_url_raw:-http://localhost:8000}"
            HB_API_PROTOCOL=$(python3 -c "from urllib.parse import urlparse; p=urlparse('${hb_api_url_raw}'); print(p.scheme or 'http')" 2>/dev/null || echo "http")
            HB_API_HOST=$(python3 -c "from urllib.parse import urlparse; p=urlparse('${hb_api_url_raw}'); print(p.hostname or 'localhost')" 2>/dev/null || echo "localhost")
            _def_port=$([ "$HB_API_PROTOCOL" = "https" ] && echo "443" || echo "8000")
            HB_API_PORT=$(python3 -c "from urllib.parse import urlparse; p=urlparse('${hb_api_url_raw}'); print(p.port or ${_def_port})" 2>/dev/null || echo "$_def_port")
        fi
        prompt_required_visible "API admin username" "hb_username" "Username cannot be empty"
        prompt_required_secret "API admin password" "hb_password" "Password cannot be empty"
        fi

        # ── Tailscale option for external API ──────────────
        # Skipped for a co-located pre-existing install: HB_API_HOST is already
        # localhost, and this block would rewrite it to a MagicDNS name that
        # points back at this same machine by a longer path.
        use_tailscale_remote="${use_tailscale_early:-N}"
        if [ "${hb_api_preexisting:-false}" = true ]; then
            use_tailscale_remote="N"
        fi
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
        # Condor is deploying hummingbot-api right here, on this same machine
        # -- so Condor itself always reaches it over localhost, tailnet or not.
        # Answering yes below connects THIS HOST to the tailnet (so the
        # dashboard can be reached remotely via `tailscale serve`). Giving
        # hummingbot-api its OWN separate tailnet node is a second, distinct
        # question asked further down, only if this one is yes -- most
        # installs don't need a second node just to reach something already
        # local, and spinning one up unconditionally used to mean two tailnet
        # devices (and two `tailscale up` connections) for one machine.
        TS_DEPLOY=false
        HB_OWN_TAILNET_NODE=false
        ts_auth_key=""
        ts_hb_hostname="hummingbot-api"
        HB_TAILSCALE_MODE=none
        use_tailscale="${use_tailscale_early:-N}"
        _ts_state="$(tailnet_state)"
        if [[ "${use_tailscale:-}" =~ ^[Yy]$ ]]; then
          # Already on the tailnet: no key needed to reuse this machine's own
          # node. Asking for one would demand a credential for a join we are
          # about to skip.
          if [ "$_ts_state" != none ]; then
            msg_ok "Tailscale already active on this machine ($_ts_state) — no auth key needed"
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
          fi
            # Only join the tailnet if nothing here has already. This branch
            # used to install and `tailscale up` unconditionally: a smoke test
            # with a daemon already running produced output byte-identical to
            # one without, because nothing ever looked. The remote-API branch
            # above has always checked; this one never did.
            if [ "$_ts_state" = none ]; then
                msg_info "Installing Tailscale on this machine..."
                curl -fsSL https://tailscale.com/install.sh | sh
                msg_info "Connecting to Tailscale network..."
                tailscale_up --authkey="$ts_auth_key" --hostname="condor" --accept-dns=true
            else
                msg_ok "Tailscale already active on this machine ($_ts_state) — reusing it"
                msg_info "Not joining the tailnet a second time: one machine, one node."
            fi
            ts_condor_ip=$(tailscale ip -4 2>/dev/null | head -1)
            TS_DEPLOY=true
            msg_ok "Tailscale connected — this machine (and its dashboard) is reachable on your tailnet"
            # On a VPS (SERVER_IP set), point the web dashboard at the Tailscale IP so the /web link works remotely
            if [ -n "${SERVER_IP:-}" ] && [ -n "${ts_condor_ip:-}" ]; then
                if grep -q "^WEB_URL=" "$ENV_FILE"; then
                    sed -i.bak "s|^WEB_URL=.*|WEB_URL=http://$ts_condor_ip:8088|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
                else
                    echo "WEB_URL=http://$ts_condor_ip:8088" >> "$ENV_FILE"
                fi
                msg_ok "Web dashboard: http://$ts_condor_ip:8088 (Tailscale access)"
            fi

            echo ""
            msg_info "hummingbot-api runs on this same machine, so Condor reaches it over"
            msg_info "localhost — it doesn't need a tailnet node of its own for that."
            prompt_visible "Also give hummingbot-api its own Tailscale node, so other devices (e.g. an MCP client on another machine) can reach it directly? [y/N]" "N" "hb_tailscale_choice"
            if [[ "${hb_tailscale_choice:-}" =~ ^[Yy]$ ]]; then
                # A second node is safe now: this machine already runs a
                # kernel-mode daemon, so hummingbot-api's `make deploy` starts
                # its sidecar in userspace mode. Netstack claims no TUN device
                # and no host routes, so both coexist -- verified on a live
                # tailnet, serving a loopback-bound port to another node with a
                # single tailscale0 between them. Before that, answering yes
                # here produced a sidecar that died on startup and a container
                # that still reported "running".
                HB_OWN_TAILNET_NODE=true
                HB_TAILSCALE_MODE=sidecar
                if [ -z "${ts_auth_key:-}" ]; then
                    msg_info "A separate node needs its own auth key (this machine's node is already registered)."
                    while true; do
                        prompt_visible "Tailscale auth key for hummingbot-api (tskey-auth-...)" "" "ts_auth_key"
                        if [ -z "${ts_auth_key:-}" ]; then
                            msg_warn "Auth key cannot be empty"; continue
                        fi
                        if [[ ! "$ts_auth_key" =~ ^tskey-auth- ]]; then
                            msg_warn "Auth key must start with 'tskey-auth-'"; continue
                        fi
                        break
                    done
                fi
                msg_ok "hummingbot-api will join the tailnet as '$ts_hb_hostname' — reachable at http://$ts_hb_hostname:8000"
            else
                # One machine, one node: port 8000 is served on the node this
                # host already has, so nothing new registers and nothing
                # contends for tailscale0.
                HB_TAILSCALE_MODE=host
                msg_ok "hummingbot-api will be served on this machine's existing tailnet node"
            fi
        fi

        echo ""
        prompt_required_visible "API admin username" "hb_username" "Username cannot be empty"
        prompt_required_secret "API admin password" "hb_password" "Password cannot be empty"
        prompt_required_secret "Config password" "hb_config_password" "Config password cannot be empty"

        # Save to condor's .env
        set_env_var DEPLOY_HUMMINGBOT_API "true"

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

        # Configure hummingbot-api by running ITS OWN setup, not by writing its
        # .env from here.
        #
        # Condor used to hand-write that file. Two authors meant two schemas:
        # this side shipped BROKER_PASSWORD=password (the well-known default
        # hummingbot-api's setup.sh deliberately stopped shipping), omitted
        # BROKER_DASHBOARD_PASSWORD so compose fell back to another well-known
        # default, and wrote API_BIND_HOST -- a key nothing on that side reads,
        # since docker-compose.yml wants API_BIND. Delegating removes the drift
        # by construction: .env has exactly one author. Everything setup.sh
        # generates -- both broker passwords especially -- stays generated
        # there, so nothing Condor passes can weaken them.
        if [ -d "$HB_API_DIR" ]; then
            if [ -f "$HB_API_DIR/.env" ]; then
                msg_ok "hummingbot-api .env already exists — leaving it untouched"
            elif [ -x "$HB_API_DIR/setup.sh" ] || [ -f "$HB_API_DIR/setup.sh" ]; then
                msg_info "Running hummingbot-api's own setup (non-interactive)..."
                if (cd "$HB_API_DIR" && chmod +x setup.sh 2>/dev/null; \
                    HBAPI_NONINTERACTIVE=1 \
                    HBAPI_SKIP_DEPS=1 \
                    HBAPI_USERNAME="$hb_username" \
                    HBAPI_PASSWORD="$hb_password" \
                    HBAPI_CONFIG_PASSWORD="$hb_config_password" \
                    TAILSCALE_ENABLED="$([ "$HB_TAILSCALE_MODE" = none ] && echo false || echo true)" \
                    TAILSCALE_MODE="$HB_TAILSCALE_MODE" \
                    TAILSCALE_AUTH_KEY="$ts_auth_key" \
                    TAILSCALE_HOSTNAME="$ts_hb_hostname" \
                    ./setup.sh); then
                    msg_ok "Hummingbot API configured by its own setup.sh"
                else
                    msg_error "hummingbot-api setup.sh failed — see the output above"
                    msg_info "Run it yourself: cd $HB_API_DIR && ./setup.sh"
                fi
            else
                msg_error "No setup.sh found in $HB_API_DIR — cannot configure the API"
                msg_info "Update that checkout, then run: cd $HB_API_DIR && ./setup.sh"
            fi
            # setup.sh already chmods this; harmless belt-and-braces if an
            # older checkout did not.
            chmod 600 "$HB_API_DIR/.env" 2>/dev/null || true

            # The Tailscale overlay and the Tailscale-aware deploy target are
            # hummingbot-api's own, checked into that repo. Condor used to
            # generate the overlay and string-patch the Makefile to add both;
            # against current main all three of those patch replacements match
            # nothing and silently no-op, while still reporting success. Owning
            # one copy upstream is the fix -- there is nothing to inject here.

            # Deploy if Docker is available
            if [ "$docker_available" = true ] && [ -f "$HB_API_DIR/docker-compose.yml" ]; then
                msg_info "Starting Hummingbot API stack..."
                # `make deploy`, not a raw `docker compose up -d`.
                #
                # deploy depends on emqx-auth, which generates
                # .emqx/auth-bootstrap.csv from the broker credentials in .env.
                # Skipping it did not merely miss a nicety: the bind-mount
                # source stayed absent, Docker created it as a root-owned
                # DIRECTORY, EMQX logged boostrap_authn_built_in_database_failed
                # and came up with authentication enabled and zero accounts --
                # so every MQTT connection was refused while the API's HTTP
                # health check still passed and this installer reported
                # success. The Makefile also picks the Tailscale overlay itself
                # from TAILSCALE_ENABLED, so the compose-file juggling that
                # used to live here is upstream's job now.
                _deploy_cmd="make deploy"
                if ! command_exists make; then
                    msg_warn "make not found — falling back to docker compose"
                    _deploy_cmd="docker compose up -d"
                fi
                if (cd "$HB_API_DIR" && eval "$_deploy_cmd"); then
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

# ── Step 4: Create/Update config.yml ─────────────────

echo -e "${BOLD}Step 4: Configuration Files${RESET}"
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
    # username/password are deliberately EMPTY, not admin/admin. A template
    # that ships working-looking credentials is a template someone deploys
    # with, and `make doctor` cannot tell "the operator chose admin/admin"
    # apart from "nobody ever filled this in". Empty is unambiguous: doctor
    # names it, and the API answers 401 rather than letting a default in.
    cat > "$CONFIG_FILE" << 'CONFIGEOF'
servers:
  local:
    host: localhost
    port: 8000
    username: ""
    password: ""

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
    # config.yml carries the hummingbot-api password once setup fills it in.
    chmod 600 "$CONFIG_FILE" 2>/dev/null || true
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

    # host/port are left at the template default (localhost:8000): Condor is
    # deploying this hummingbot-api right here, on this same machine, so it
    # always reaches it over localhost -- whether or not hummingbot-api also
    # has its own tailnet node for other clients (HB_OWN_TAILNET_NODE, above).
fi

# If user provided a remote API URL (skipped local deployment), update config.yml
if [ "${hb_api_configured:-false}" = true ] && [ -f "$CONFIG_FILE" ]; then
    if update_config_api_server "$HB_API_HOST" "$HB_API_PORT" "${hb_username:-}" "${hb_password:-}"; then
        msg_ok "Configured $CONFIG_FILE: ${HB_API_PROTOCOL:-http}://${HB_API_HOST}:${HB_API_PORT}"
    else
        msg_warn "Configured $CONFIG_FILE with a basic edit -- review the servers: section to confirm it looks right"
    fi
fi

if [ "$config_updated" = false ] && [ -f "$CONFIG_FILE" ]; then
    msg_ok "$CONFIG_FILE exists and is configured"
fi

echo ""

# ── Step 5: Data directory ──────────────────────────

if [ ! -d "$DATA_DIR" ]; then
    mkdir -p "$DATA_DIR"
fi

# ── Step 6: Summary ────────────────────────────────

echo -e "${BOLD}══════════════════════════════════════════════${RESET}"
echo -e "  ${GREEN}Setup complete!${RESET}"
echo ""
echo -e "  ${BOLD}Next:${RESET}"
echo -e "    make run     ${DIM}- start Condor (tmux session 'condor')${RESET}"
echo -e "    make doctor  ${DIM}- re-check dependencies, config and API access${RESET}"
echo -e "    make logs    ${DIM}- attach to the running session (detach: Ctrl+B then D)${RESET}"
if [ "${CONDOR_MODE:-telegram}" = "local" ]; then
echo ""
echo -e "  Then open ${BOLD}http://localhost:8088${RESET} — you are logged in already."
echo -e "  ${DIM}No login, loopback only. See 'Local mode' in the README before exposing it.${RESET}"
else
echo ""
echo -e "  Then send ${BOLD}/start${RESET} to your Telegram bot."
fi
if [ "${hb_api_deployed:-}" = true ]; then
echo ""
echo -e "  Hummingbot API is running — config at ${BOLD}../hummingbot-api/.env${RESET}"
fi
if [ "${TS_DEPLOY:-false}" = true ]; then
echo ""
echo -e "  ${BOLD}Tailscale:${RESET}"
if [ "${HB_OWN_TAILNET_NODE:-false}" = true ]; then
echo -e "    hummingbot-api URL:  http://${ts_hb_hostname}:8000  ${CYAN}(own tailnet node)${RESET}"
else
echo -e "    hummingbot-api:      http://localhost:8000  ${CYAN}(local — no tailnet node needed)${RESET}"
fi
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
echo -e "  ${CYAN}  Install Tailscale on that device, then sign in to the same account:${RESET}"
echo -e "    Linux / WSL:   curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up"
echo -e "    macOS / Win:   https://tailscale.com/download — then sign in to the same account"
echo -e "  ${DIM}A reusable auth key works too (sudo tailscale up --authkey=tskey-auth-...).${RESET}"
echo -e "  ${DIM}Yours is not reprinted here — it is a credential, and this output ends up in${RESET}"
echo -e "  ${DIM}scrollback and install logs. Find or reissue it at${RESET}"
echo -e "  ${DIM}https://login.tailscale.com/admin/settings/keys${RESET}"
fi
echo -e "${BOLD}══════════════════════════════════════════════${RESET}"
echo ""
