import os
import discord
from discord import app_commands, Interaction
from discord.ui import View, Select, Button
import sqlite3
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import docker

# ================= CONFIG =================
TOKEN = os.getenv("BOT_TOKEN")  # Or ask on first run
GUILD_ID = None  # Optional: set your guild ID
ADMIN_IDS = [1421860082894766183]  # default admin
VPS_USER_ROLE_ID = 1434000306357928047

LOG_CHANNEL_ID = None
RENEWAL_CHANNEL_ID = None

# ================= DISCORD BOT =================
intents = discord.Intents.default()
intents.members = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ================= DATABASE =================
conn = sqlite3.connect('vps.db')
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS vps
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER,
              vps_number INTEGER,
              container_id TEXT,
              status TEXT,
              os_type TEXT,
              ram INTEGER,
              cpu REAL,
              disk INTEGER,
              expires_at TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS vps_shared
             (vps_id INTEGER, user_id INTEGER, PRIMARY KEY(vps_id,user_id))''')
c.execute('''CREATE TABLE IF NOT EXISTS config
             (key TEXT PRIMARY KEY, value TEXT)''')
conn.commit()

# ================= DOCKER =================
client = docker.from_env()

# ================= SCHEDULER =================
scheduler = AsyncIOScheduler()

# ================= UTILITIES =================
def is_admin(user_id):
    return user_id in ADMIN_IDS

def log_action(action, actor_id, target_id=None):
    if LOG_CHANNEL_ID:
        channel = bot.get_channel(LOG_CHANNEL_ID)
        if channel:
            msg = f"**{action}** by <@{actor_id}>"
            if target_id:
                msg += f" on <@{target_id}>"
            import asyncio; asyncio.create_task(channel.send(msg))

def next_vps_number(user_id):
    c.execute('SELECT MAX(vps_number) FROM vps WHERE user_id=?', (user_id,))
    row = c.fetchone()
    return (row[0] or 0) + 1

# ================= COMMANDS =================
@tree.command(name="create", description="🖥️ Create a new VPS (Admin only)")
@app_commands.describe(user="👤 User", os_type="💻 OS type (ubuntu/debian)", ram="💾 RAM in MB", cpu="⚡ CPU cores", disk="📦 Disk in MB")
async def create(interaction: Interaction, user: discord.User, os_type: str, ram: int, cpu: float, disk: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return

    vps_number = next_vps_number(user.id)
    container_name = f"vps-{user.id}-{vps_number}"

    # Select image
    if os_type.lower() == "debian":
        image = "debian:bookworm-slim"
    elif os_type.lower() == "ubuntu":
        image = "ubuntu:22.04"
    else:
        await interaction.response.send_message("❌ Invalid OS type", ephemeral=True)
        return

    # Run container with limits
    container = client.containers.run(
        image,
        detach=True,
        tty=True,
        name=container_name,
        stdin_open=True,
        mem_limit=f"{ram}m",
        cpu_quota=int(cpu*100000),
        command="/bin/bash"
    )

    # Install required packages
    container.exec_run("apt update && apt install -y systemd sudo neofetch tmate")

    expires = datetime.utcnow() + timedelta(days=14)
    c.execute('INSERT INTO vps(user_id,vps_number,container_id,status,os_type,ram,cpu,disk,expires_at) VALUES (?,?,?,?,?,?,?,?,?)',
              (user.id,vps_number,container.id,"active",os_type,ram,cpu,disk,expires.isoformat()))
    conn.commit()
    log_action("create_vps", interaction.user.id, user.id)

    # Give VPS user role
    guild = bot.get_guild(GUILD_ID) if GUILD_ID else None
    if guild:
        member = guild.get_member(user.id)
        if member:
            role = guild.get_role(VPS_USER_ROLE_ID)
            if role:
                await member.add_roles(role)

    await interaction.response.send_message(f"✅ VPS#{vps_number} created for {user.mention}.", ephemeral=True)

# ================= MANAGE COMMAND =================
@tree.command(name="manage", description="🛠️ Manage your VPSes")
@app_commands.describe(user="👤 Optional: manage another user (Admin only)")
async def manage(interaction: Interaction, user: discord.User = None):
    # implementation similar to previous parts with Start/Stop/Restart/SSH buttons
    pass

# ================= SHARED MANAGEMENT =================
@tree.command(name="manage-shared", description="🔗 Manage VPSes shared with you")
@app_commands.describe(user="👤 Optional: Admin manage shared VPSes for someone else")
async def manage_shared(interaction: Interaction, user: discord.User = None):
    # implementation similar to previous snippet
    pass

# ================= STARTUP =================
@bot.event
async def on_ready():
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        await tree.sync(guild=guild)
    else:
        await tree.sync()
    print("✅ Bot is Online")
    global LOG_CHANNEL_ID, RENEWAL_CHANNEL_ID
    c.execute("SELECT value FROM config WHERE key='log_channel'")
    row = c.fetchone()
    if row:
        LOG_CHANNEL_ID = int(row[0])
    c.execute("SELECT value FROM config WHERE key='renewal_channel'")
    row = c.fetchone()
    if row:
        RENEWAL_CHANNEL_ID = int(row[0])
    scheduler.start()

# ================= RUN BOT =================
bot.run(TOKEN)
