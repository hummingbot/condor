#!/bin/bash

# Prompt for Telegram Bot Token
read -p "Enter your Telegram Bot Token: " telegram_token

# Prompt for Authorized User IDs
echo "Enter the User IDs that are allowed to talk with the bot."
echo "Separate multiple User IDs with a comma (e.g., 12345,67890,23456)."
read -p "User IDs: " user_ids

# Prompt for extra Hummingbot images
echo "Enter extra Hummingbot images to download, separated by a comma."
echo "Example: image1:tag1,image2:tag2"
read -p "Extra Hummingbot Images: " extra_hummingbot_images

# Remove spaces and ensure comma separation
user_ids=$(echo $user_ids | tr -d '[:space:]')
extra_hummingbot_images=$(echo $extra_hummingbot_images | tr -d '[:space:]')

# Include default Hummingbot image in the list
all_hummingbot_images="hummingbot/hummingbot:latest"
if [ -n "$extra_hummingbot_images" ]; then
    all_hummingbot_images="$extra_hummingbot_images,$all_hummingbot_images"
fi

# Create or update .env file
echo "TELEGRAM_TOKEN=$telegram_token" > .env
echo "AUTHORIZED_USERS=$user_ids" >> .env
echo "EXTRA_HUMMINGBOT_IMAGES=$extra_hummingbot_images" >> .env
echo "ALL_HUMMINGBOT_IMAGES=$all_hummingbot_images" >> .env

echo ".env file setup completed."
echo "You can now run 'docker-compose up -d' to start the bot."
