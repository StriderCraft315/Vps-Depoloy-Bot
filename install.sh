#!/bin/bash
# VPS Deploy Bot Installer Script (no start script version)
# Author: StriderCraft315

# =================== ROOT CHECK ===================
if [[ $EUID -ne 0 ]]; then
   echo "❌ Please run this script as root!"
   exit 1
fi

# =================== VARIABLES ===================
REPO_URL="https://github.com/StriderCraft315/Vps-Depoloy-Bot.git"
BOT_DIR="vps-deploy-bot"

# =================== INSTALL DEPENDENCIES ===================
echo "🔄 Updating system..."
apt update -y && apt upgrade -y

echo "📦 Installing dependencies..."
apt install -y python3 python3-pip git docker.io

systemctl enable docker
systemctl start docker

# =================== DOWNLOAD BOT ===================
if [ -d "$BOT_DIR" ]; then
  echo "📁 Directory already exists, pulling latest changes..."
  cd "$BOT_DIR" && git pull
else
  echo "⬇️ Cloning repository..."
  git clone "$REPO_URL" "$BOT_DIR"
  cd "$BOT_DIR"
fi

# =================== BOT TOKEN SETUP ===================
echo ""
read -p "🔑 Enter your Discord Bot Token: " BOT_TOKEN

BOT_TOKEN_ESCAPED=$(printf '%s\n' "$BOT_TOKEN" | sed -e 's/[\/&]/\\&/g')

if grep -q "TOKEN =" bot.py; then
    sed -i "s|TOKEN = .*|TOKEN = \"$BOT_TOKEN_ESCAPED\"|g" bot.py
else
    echo "TOKEN = \"$BOT_TOKEN_ESCAPED\"" >> bot.py
fi

# =================== INSTALL PYTHON MODULES ===================
echo "📦 Installing Python modules..."
pip3 install -r requirements.txt || pip3 install discord.py docker apscheduler

# =================== DONE ===================
echo ""
echo "✅ Installation complete!"
echo "📂 Bot directory located at: $(pwd)"
echo ""
echo "➡️ To start the bot, run:"
echo "   cd $(pwd) && python3 bot.py"
