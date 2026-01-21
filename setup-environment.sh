#!/bin/bash

# Configuration
ENV_FILE=".env"
CONFIG_FILE="config.yml"
DATA_DIR="data"

# Escape special characters for .env file
escape_env_value() {
    local value="$1"
    # Escape backslashes, double quotes, and dollar signs
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//\$/\\\$}"
    echo "$value"
}

echo "==================================="
echo "      Condor Bot Setup"
echo "==================================="

# 1. Check if .env already exists
if [ -f "$ENV_FILE" ]; then
    echo ""
    echo ">> Found existing $ENV_FILE file."
    echo ">> Credentials already exist. Skipping setup params."
    echo ""
else
    # 2. Prompt for Telegram Bot Token with validation
    echo ""
    while true; do
        echo -n "Enter your Telegram Bot Token: "
        read -r telegram_token < /dev/tty || telegram_token=""
        telegram_token=$(echo "$telegram_token" | tr -d '[:space:]')
        
        if [ -z "$telegram_token" ]; then
            echo "⚠️  Telegram Bot Token cannot be empty. Please try again."
            continue
        fi
        
        # Validate token format: digits:alphanumeric
        if ! [[ "$telegram_token" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
            echo "⚠️  Invalid Telegram Bot Token format."
            echo "    Expected format: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
            echo "    Please enter a valid token."
            continue
        fi
        
        break
    done

    # 3. Prompt for Admin User ID with validation
    echo ""
    echo "Enter your Telegram User ID (you will be the admin)."
    echo "(Tip: Message @userinfobot on Telegram to get your ID)"
    while true; do
        echo -n "Admin User ID: "
        read -r admin_id < /dev/tty || admin_id=""
        admin_id=$(echo "$admin_id" | tr -d '[:space:]')
        
        if [ -z "$admin_id" ]; then
            echo "⚠️  Admin User ID cannot be empty. Please try again."
            continue
        fi
        
        # Validate user ID is numeric
        if ! [[ "$admin_id" =~ ^[0-9]+$ ]]; then
            echo "⚠️  Invalid User ID. User ID should be numeric (e.g., 123456789)."
            continue
        fi
        
        break
    done

    # 4. Prompt for OpenAI API Key (optional)
    echo ""
    echo "Enter your OpenAI API Key (optional, for AI features)."
    echo "Press Enter to skip if not using AI features."
    echo -n "OpenAI API Key: "
    read -r openai_key < /dev/tty || openai_key=""
    openai_key=$(echo "$openai_key" | tr -d '[:space:]')

    # 5. Create .env file with escaped values
    {
        echo "TELEGRAM_TOKEN=$(escape_env_value "$telegram_token")"
        echo "ADMIN_USER_ID=$(escape_env_value "$admin_id")"
        if [ -n "$openai_key" ]; then
            echo "OPENAI_API_KEY=$(escape_env_value "$openai_key")"
        fi
    } > "$ENV_FILE"

    echo ""
    echo "✅ $ENV_FILE file created successfully!"
fi

# 6. Ensure data directory exists
if [ ! -d "$DATA_DIR" ]; then
    echo "Ensuring $DATA_DIR directory exists for persistence..."
    mkdir -p "$DATA_DIR"
fi

# 7. Create blank config.yml if it doesn't exist
if [ -f "$CONFIG_FILE" ]; then
    echo ""
    echo ">> Found existing $CONFIG_FILE file."
    echo ">> Configuration already exists."
else
    echo ""
    echo "Creating blank $CONFIG_FILE file..."
    touch "$CONFIG_FILE"
    echo "✅ $CONFIG_FILE created successfully!"
    echo ""
    echo "Note: You can add Hummingbot API servers and manage access"
    echo "      using the /config command in your Telegram bot."
fi

# 8. Display Run Instructions
echo ""
echo "✅ Setup Complete! Environment and config files are ready."
echo "=========================================================="
echo "🚀 RUN:   'make deploy' (Docker) or 'make run' (Local)"
echo "🛠️ CMDS:  make stop, restart, logs, ps, install"
echo "🤖 NEXT:  Open Telegram, use /servers to add API servers"
echo "=========================================================="
