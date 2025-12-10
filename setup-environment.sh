#!/bin/bash

echo "==================================="
echo "  Condor Bot Setup"
echo "==================================="
echo ""

# Prompt for Telegram Bot Token
read -p "Enter your Telegram Bot Token: " telegram_token

# Prompt for Authorized User IDs
echo ""
echo "Enter the User IDs that are allowed to talk with the bot."
echo "Separate multiple User IDs with a comma (e.g., 12345,67890,23456)."
read -p "User IDs: " user_ids

# Prompt for OpenAI API Key (optional)
echo ""
echo "Enter your OpenAI API Key (optional, for AI features)."
echo "Press Enter to skip if not using AI features."
read -p "OpenAI API Key: " openai_key

# Remove spaces from user IDs
user_ids=$(echo $user_ids | tr -d '[:space:]')

# Create or update .env file
echo "TELEGRAM_TOKEN=$telegram_token" > .env
echo "AUTHORIZED_USERS=$user_ids" >> .env
if [ -n "$openai_key" ]; then
    echo "OPENAI_API_KEY=$openai_key" >> .env
fi

echo ""
echo ".env file created successfully!"

echo ""
echo "Installing Chrome for Plotly image generation..."
plotly_get_chrome || kaleido_get_chrome || python -c "import kaleido; kaleido.get_chrome_sync()"
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
