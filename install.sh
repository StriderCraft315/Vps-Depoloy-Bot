#!/bin/bash
# VPS Deploy Bot Installer (Patched Version)

set -e

# --- Root Check ---
if [[ $EUID -ne 0 ]]; then
   echo "❌ Please run this installer as root!"
   exit 1
fi

# --- System Update ---
echo "🔄 Updating system..."
apt update -y && apt upgrade -y

# --- Dependencies ---
echo "📦 Installing dependencies..."
apt install -y python3 python3-pip docker.io git curl

# --- Ensure Docker is running ---
echo "🐳 Starting Docker..."
systemctl enable docker
systemctl start docker

# --- Clone or Update Repository ---
if [ -d "/root/vps-deploy-bot" ]; then
  echo "📁 Existing installation found. Updating..."
  cd /root/vps-deploy-bot && git pull
else
  echo "⬇️ Cloning repository..."
  git clone https://github.com/StriderCraft315/Vps-Depoloy-Bot.git /root/vps-deploy-bot
  cd /root/vps-deploy-bot
fi

# --- Python Dependencies ---
echo "🐍 Installing Python dependencies..."
pip3 install --upgrade pip
pip3 install -r requirements.txt || {
  echo "⚠️ requirements.txt failed — installing manually..."
  pip3 install discord.py docker apscheduler aiofiles
}

# --- Ask for Bot Token ---
echo ""
read -p "🤖 Enter your Discord Bot Token: " BOT_TOKEN
read -p "🏠 Enter your Discord Guild (Server) ID: " GUILD_ID

# --- Insert into bot.py ---
echo "🧠 Writing token and guild ID into bot.py..."
sed -i "s|TOKEN = .*|TOKEN = \"$BOT_TOKEN\"|" bot.py
sed -i "s|GUILD_ID = .*|GUILD_ID = $GUILD_ID|" bot.py

# --- Run the Bot ---
echo ""
echo "✅ Installation Complete!"
echo "To start the bot, run:"
echo "cd /root/vps-deploy-bot && python3 bot.py"
