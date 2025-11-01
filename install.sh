#!/bin/bash

# Ensure script is run as root
if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root"
   exit 1
fi

# Update system and install dependencies
apt update && apt upgrade -y
apt install -y python3 python3-pip git lxc tmate sqlite3

# Clone repository if it doesn't exist
if [ -d "vps-deploy-bot" ]; then
    echo "Repository already exists, pulling latest changes..."
    cd vps-deploy-bot && git pull
else
    git clone https://github.com/StriderCraft315/Vps-Depoloy-Bot.git vps-deploy-bot
    cd vps-deploy-bot
fi

# Install Python requirements
pip3 install --upgrade pip
pip3 install discord
pip3 install apscheduler
pip3 install aiosqlite
pip3 install discord.py
pip3 install apscheduler
pip3 install aiosqlite
pip3 install psutil
pip3 install requests
# Instructions for user
echo "Installation complete!"
echo "1. Edit bot.py to set your TOKEN and GUILD_ID."
echo "2. Run 'python3 bot.py' to start the bot."
echo "3. Use /set-log-channel to set the public log channel."
