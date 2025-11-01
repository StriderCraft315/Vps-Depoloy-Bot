#!/bin/bash

# ================= Check Root =================
if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root"
   exit 1
fi

# ================= Variables =================
BOT_DIR="$HOME/vps-deploy-bot"
GITHUB_REPO_RAW="https://raw.githubusercontent.com/StriderCraft315/Vps-Depoloy-Bot/main"
BOT_PY="bot.py"
REQUIREMENTS="requirements.txt"

# ================= Update System =================
apt update && apt upgrade -y

# ================= Install Dependencies =================
apt install -y python3 python3-pip docker.io git

# ================= Start Docker if not running =================
systemctl enable docker
systemctl start docker

# ================= Create Bot Directory =================
mkdir -p "$BOT_DIR"
cd "$BOT_DIR" || exit

# ================= Download Files =================
echo "Downloading bot.py and requirements.txt..."
curl -s "$GITHUB_REPO_RAW/$BOT_PY" -o "$BOT_PY"
curl -s "$GITHUB_REPO_RAW/$REQUIREMENTS" -o "$REQUIREMENTS"

# ================= Install Python Modules =================
pip3 install --upgrade pip
pip3 install -r "$REQUIREMENTS"

# ================= Set Discord Bot Token =================
read -p "Enter your Discord bot token: " BOT_TOKEN
sed -i "s|YOUR_BOT_TOKEN|$BOT_TOKEN|g" "$BOT_PY"

echo "✅ Installation complete!"
echo "To run the bot, use:"
echo "cd $BOT_DIR && python3 bot.py"
