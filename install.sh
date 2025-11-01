#!/bin/bash
set -e

# Ensure the script is run as root
if [[ $EUID -ne 0 ]]; then
   echo "Run this script as root."
   exit 1
fi

# Update system
apt update && apt upgrade -y

# Install dependencies
apt install -y python3 python3-pip git lxd tmate

# Initialize LXD if not already
if ! lxc info >/dev/null 2>&1; then
    echo "Initializing LXD..."
    lxd init --auto
fi

# Clone the repository
if [ -d "vps-deploy-bot" ]; then
    cd vps-deploy-bot
    echo "Pulling latest changes..."
    git pull
else
    git clone https://github.com/StriderCraft315/Vps-Depoloy-Bot.git vps-deploy-bot
    cd vps-deploy-bot
fi

# Install Python requirements
pip3 install --upgrade pip
pip3 install -r requirements.txt

# Prompt for Discord bot token and guild ID
read -p "Enter your Discord bot token: " BOT_TOKEN
read -p "Enter your Discord guild/server ID: " GUILD_ID

# Save them to .env
echo "DISCORD_TOKEN=$BOT_TOKEN" > .env
echo "GUILD_ID=$GUILD_ID" >> .env

echo "Installation complete!"
echo "Run the bot using: python3 bot.py"
