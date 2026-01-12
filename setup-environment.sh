#!/bin/bash

# Configuration
ENV_FILE=".env"
DATA_DIR="data"

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
    # 2. Prompt for Telegram Bot Token
    echo ""
    read -p "Enter your Telegram Bot Token: " telegram_token

    # 3. Prompt for Admin User ID
    echo ""
    echo "Enter your Telegram User ID (you will be the admin)."
    echo "(Tip: Message @userinfobot on Telegram to get your ID)"
    read -p "Admin User ID: " admin_id

    # 4. Prompt for OpenAI API Key (optional)
    echo ""
    echo "Enter your OpenAI API Key (optional, for AI features)."
    echo "Press Enter to skip if not using AI features."
    read -p "OpenAI API Key: " openai_key

    # Clean whitespaces from inputs
    telegram_token=$(echo "$telegram_token" | tr -d '[:space:]')
    admin_id=$(echo "$admin_id" | tr -d '[:space:]')
    openai_key=$(echo "$openai_key" | tr -d '[:space:]')

    # 5. Create .env file
    {
        echo "TELEGRAM_TOKEN=$telegram_token"
        echo "ADMIN_USER_ID=$admin_id"
        if [ -n "$openai_key" ]; then
            echo "OPENAI_API_KEY=$openai_key"
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

# 7. Display Run Instructions
echo "==================================="
echo "      How to Run Condor"
echo "==================================="
echo ""
echo "Option 1: Docker (Recommended)"
echo "  make deploy"
echo ""
echo "Option 2: Local Python"
echo "  make run"
echo ""
echo "On first run, config.yml will be auto-created."
echo "Use /config in the bot to add servers and manage access."
echo "==================================="
