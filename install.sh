#!/bin/bash
set -e

echo "───────────────────────────────────────────────"
echo " 🚀 Zycron VPS Deploy Bot Installer (Full LXC Setup)"
echo "───────────────────────────────────────────────"
echo ""

# Step 1: Update system
echo "📦 Updating system packages..."
sudo apt update -y && sudo apt upgrade -y

# Step 2: Install dependencies
echo "⚙️ Installing dependencies..."
sudo apt install -y python3 python3-pip python3-venv git lxc lxc-utils bridge-utils dnsmasq-base sqlite3 curl

# Step 3: Setup network bridge (lxcbr0)
echo "🌐 Setting up LXC default bridge (lxcbr0)..."
if ! ip link show lxcbr0 > /dev/null 2>&1; then
  sudo bash -c 'cat <<EOF > /etc/network/interfaces.d/lxcbr0.cfg
auto lxcbr0
iface lxcbr0 inet static
    bridge_ports none
    bridge_stp off
    bridge_fd 0
    bridge_maxwait 0
    address 10.0.3.1
    netmask 255.255.255.0
EOF'
  sudo systemctl restart networking || true
fi

# Step 4: Enable and configure LXC
echo "🧰 Configuring LXC..."
sudo mkdir -p /etc/lxc /var/lib/lxc
sudo bash -c 'cat <<EOF > /etc/lxc/default.conf
lxc.net.0.type = veth
lxc.net.0.link = lxcbr0
lxc.net.0.flags = up
lxc.apparmor.profile = generated
lxc.apparmor.allow_nesting = 1
lxc.cgroup.devices.allow = a
EOF'

# Enable LXC service
sudo systemctl enable lxc-net || true
sudo systemctl restart lxc-net || true

# Step 5: Create bot directory
echo "📂 Setting up bot files..."
cd /root
if [ -d "vps-deploy-bot" ]; then
  echo "🧹 Old directory found — removing..."
  rm -rf vps-deploy-bot
fi

# Clone latest bot
git clone https://github.com/StriderCraft315/Vps-Depoloy-Bot.git vps-deploy-bot
cd vps-deploy-bot

# Step 6: Setup Python environment
echo "🐍 Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Step 7: Install requirements
echo "📦 Installing Python packages..."
pip install --upgrade pip
pip install -r requirements.txt

# Step 8: Create .env
if [ ! -f ".env" ]; then
  echo "⚙️ Creating default .env..."
  cat <<EOF > .env
DISCORD_TOKEN=YOUR_BOT_TOKEN
LOG_CHANNEL_ID=YOUR_LOG_CHANNEL_ID
LXC_PATH=/var/lib/lxc
EOF
fi

# Step 9: Permissions
chmod +x install.sh
chmod 755 .

echo ""
echo "✅ Installation complete!"
echo ""
echo "To start the bot, run these commands:"
echo "-----------------------------------------"
echo "cd /root/vps-deploy-bot"
echo "source venv/bin/activate"
echo "python3 bot.py"
echo "-----------------------------------------"
echo ""
echo "🧠 Tip: To auto-start on boot, I can generate a systemd service if you’d like."
