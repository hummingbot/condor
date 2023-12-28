#!/bin/bash

# Prompt for Telegram Bot Token
read -p "Enter your Telegram Bot Token: " telegram_token

# Prompt for Authorized User IDs
echo "Enter the User IDs that are allowed to talk with the bot."
echo "Separate multiple User IDs with a comma (e.g., 12345,67890,23456)."
read -p "User IDs: " user_ids

# Remove spaces (if any) and ensure comma separation
user_ids=$(echo $user_ids | tr -d '[:space:]')

# Create or update .env file
echo "TELEGRAM_TOKEN=$telegram_token" > .env
echo "AUTHORIZED_USERS=$user_ids" >> .env

echo ".env file setup completed."
