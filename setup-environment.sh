#!/bin/bash

echo "==================================="
echo "  Condor Bot Setup"
echo "==================================="
echo ""

# Prompt for Telegram Bot Token
read -p "Enter your Telegram Bot Token: " telegram_token

# Prompt for Admin User ID
echo ""
echo "Enter your Telegram User ID (you will be the admin)."
echo "(Tip: Message @userinfobot on Telegram to get your ID)"
read -p "Admin User ID: " admin_id

# Prompt for OpenAI API Key (optional)
echo ""
echo "Enter your OpenAI API Key (optional, for AI features)."
echo "Press Enter to skip if not using AI features."
read -p "OpenAI API Key: " openai_key

# Remove spaces
admin_id=$(echo $admin_id | tr -d '[:space:]')

# Create or update .env file
echo "TELEGRAM_TOKEN=$telegram_token" > .env
echo "ADMIN_USER_ID=$admin_id" >> .env
if [ -n "$openai_key" ]; then
    echo "OPENAI_API_KEY=$openai_key" >> .env
fi

echo ""
echo ".env file created successfully!"

echo ""
echo "Ensuring data directory exists for persistence..."
mkdir -p data

echo "==================================="
echo "  How to Run Condor"
echo "==================================="
echo ""
echo "Option 1: Docker (Recommended)"
echo "  docker compose up -d"
echo ""
echo "Option 2: Local Python"
echo "  make install"
echo "  conda activate condor"
echo "  python main.py"
echo ""
echo "On first run, config.yml will be auto-created."
echo "Use /config in the bot to add servers and manage access."
echo ""
