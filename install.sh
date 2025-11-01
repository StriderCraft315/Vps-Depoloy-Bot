#!/bin/bash
# VPS Deploy Bot Installer

if [[ $EUID -ne 0 ]]; then
   echo "Run as root"
   exit 1
fi

apt update && apt upgrade -y
apt install -y python3 python3-pip docker.io git

docker --version || systemctl start docker

# Clone repo
if [ -d "vps-deploy-bot" ]; then
    cd vps-deploy-bot && git pull
else
    git clone https://github.com/StriderCraft315/Vps-Depoloy-Bot.git vps-deploy-bot
    cd vps-deploy-bot
fi

pip3 install --upgrade pip
pip3 install -r requirements.txt || pip3 install discord.py docker apscheduler

# Ask for token
read -p "Enter your Discord bot token: " BOT_TOKEN
sed -i "s|TOKEN = .*|TOKEN = \"$BOT_TOKEN\"|" bot.py

echo "✅ Installation complete. Your bot.py is in: $(pwd)"
