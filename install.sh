#!/bin/bash

# ================= INSTALL SCRIPT =================
set -e

# Ensure script is run as root
if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root"
   exit 1
fi

echo "🔄 Updating system..."
apt update && apt upgrade -y

echo "📦 Installing dependencies..."
apt install -y python3 python3-pip git docker.io

echo "🛠 Enabling Docker..."
systemctl enable docker
systemctl start docker

# ---------------- GitHub Repo ----------------
REPO_URL="https://github.com/StriderCraft315/Vps-Depoloy-Bot.git"
DIR_NAME="vps-deploy-bot"

if [ -d "$DIR_NAME" ]; then
    echo "📂 Repo already exists. Pulling latest changes..."
    cd "$DIR_NAME"
    git pull
else
    echo "📂 Cloning repo..."
    git clone "$REPO_URL" "$DIR_NAME"
    cd "$DIR_NAME"
fi

# ---------------- Bot Token ----------------
read -p "🔑 Enter your Discord Bot Token: " BOT_TOKEN

# Replace TOKEN placeholder in bot.py
sed -i "s|TOKEN = \".*\"|TOKEN = \"$BOT_TOKEN\"|g" bot.py

# ---------------- Python Requirements ----------------
pip3 install --upgrade pip
if [ -f requirements.txt ]; then
    pip3 install -r requirements.txt
else
    pip3 install discord.py docker apscheduler
fi

echo "✅ Installation complete!"
echo "📂 Your bot files are located in: $(pwd)"
echo "▶️ Run the bot with: python3 bot.py"
