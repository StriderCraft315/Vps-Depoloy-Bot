import discord
from discord.ext import commands, tasks
from discord import app_commands, Embed, Interaction
from discord.ui import View, Select, Button
import docker
import sqlite3
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ============================ CONFIG ============================
LOGO_URL = "https://images-ext-1.discordapp.net/external/peg7WjSzp9Jw8CAsr_R4HKVK6yqNHflRUv1n_U2dyUE/%3Fsize%3D1024/https/cdn.discordapp.com/avatars/1432425414906871808/e58627513bdda2364695c47fef1c5260.png?format=webp&quality=lossless"
TOKEN = "YOUR_DISCORD_BOT_TOKEN"
DEFAULT_ADMIN_ID = 1421860082894766183
VPS_USER_ROLE_ID = 1434000306357928047

intents = discord.Intents.default()
intents.message_content = False
bot = commands.Bot(command_prefix="/", intents=intents)
scheduler = AsyncIOScheduler()

client = docker.from_env()
# ============================ DATABASE SETUP ============================
conn = sqlite3.connect('vpsbot.db')
c = conn.cursor()

# VPS table
c.execute('''CREATE TABLE IF NOT EXISTS vps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    container_id TEXT,
    os TEXT,
    expires_at TEXT,
    status TEXT,
    vps_number INTEGER
)''')

# Admins table
c.execute('''CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER UNIQUE
)''')

# Logs table
c.execute('''CREATE TABLE IF NOT EXISTS logs (
    action TEXT,
    user_id INTEGER,
    target_user INTEGER,
    time TEXT
)''')

# Config table
c.execute('''CREATE TABLE IF NOT EXISTS config (
    key TEXT UNIQUE,
    value TEXT
)''')

# Shared VPS table
c.execute('''CREATE TABLE IF NOT EXISTS vps_shared (
    vps_id INTEGER,
    user_id INTEGER
)''')

conn.commit()

# Add default admin if missing
c.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (DEFAULT_ADMIN_ID,))
conn.commit()

# ============================ HELPER FUNCTIONS ============================
def log_action(action, user_id, target_user=None):
    c.execute('INSERT INTO logs (action, user_id, target_user, time) VALUES (?, ?, ?, ?)',
              (action, user_id, target_user, datetime.utcnow().isoformat()))
    conn.commit()

def is_admin(user_id):
    c.execute('SELECT 1 FROM admins WHERE user_id=?', (user_id,))
    return c.fetchone() is not None

def get_log_channel():
    c.execute('SELECT value FROM config WHERE key="log_channel"')
    res = c.fetchone()
    return int(res[0]) if res else None

async def send_log_embed(title, description):
    channel_id = get_log_channel()
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if channel:
        embed = Embed(title=title, description=description, color=0x57F287)
        embed.set_thumbnail(url=LOGO_URL)
        await channel.send(embed=embed)
# ============================ BOT STARTUP ============================
@bot.event
async def on_ready():
    await bot.tree.sync()
    scheduler.start()
    await send_log_embed("Bot is Online ✅", "")
    print(f"Logged in as {bot.user}")

# ============================ AUTO-SUSPEND TASK ============================
@scheduler.scheduled_job('interval', hours=12)
def suspend_expired_vps():
    now = datetime.utcnow().isoformat()
    c.execute('SELECT user_id, container_id FROM vps WHERE expires_at<? AND status="active"', (now,))
    for user_id, container_id in c.fetchall():
        try:
            container = client.containers.get(container_id)
            container.stop()
            c.execute('UPDATE vps SET status="suspended" WHERE user_id=?', (user_id,))
            conn.commit()
            log_action('auto_suspend', 0, user_id)
        except Exception as e:
            print(f"Error suspending VPS for {user_id}: {e}")
# ============================ MANAGE COMMAND ============================
class ManageVPSView(View):
    def __init__(self, user_id):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.vps_options = []

        c.execute('SELECT id, vps_number, status FROM vps WHERE user_id=?', (user_id,))
        for vps_id, number, status in c.fetchall():
            label = f"VPS#{number} ({status})"
            self.vps_options.append(discord.SelectOption(label=label, value=str(vps_id)))

        if self.vps_options:
            self.add_item(ManageVPSSelect(self.vps_options, self.user_id))

class ManageVPSSelect(Select):
    def __init__(self, options, user_id):
        super().__init__(placeholder="Select your VPS", min_values=1, max_values=1, options=options)
        self.user_id = user_id

    async def callback(self, interaction: Interaction):
        vps_id = int(self.values[0])
        c.execute('SELECT container_id, os, status FROM vps WHERE id=?', (vps_id,))
        row = c.fetchone()
        if not row:
            await interaction.response.send_message("VPS not found.", ephemeral=True)
            return
        container_id, os_type, status = row

        view = View(timeout=None)
        view.add_item(Button(label="Start", style=discord.ButtonStyle.green, custom_id=f"start-{vps_id}"))
        view.add_item(Button(label="Stop", style=discord.ButtonStyle.red, custom_id=f"stop-{vps_id}"))
        view.add_item(Button(label="Restart", style=discord.ButtonStyle.blurple, custom_id=f"restart-{vps_id}"))
        view.add_item(Button(label="SSH (tmate)", style=discord.ButtonStyle.gray, custom_id=f"ssh-{vps_id}"))

        embed = Embed(title=f"Manage VPS#{vps_id}", color=0x00FFFF)
        embed.add_field(name="Container ID", value=container_id, inline=False)
        embed.add_field(name="OS", value=os_type, inline=True)
        embed.add_field(name="Status", value=status, inline=True)

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="manage", description="Manage your VPS")
async def manage(interaction: Interaction):
    view = ManageVPSView(interaction.user.id)
    if not view.vps_options:
        await interaction.response.send_message("You have no VPSes.", ephemeral=True)
        return
    await interaction.response.send_message("Select a VPS to manage:", view=view, ephemeral=True)
# ============================ BUTTON INTERACTIONS ============================
@bot.event
async def on_interaction(interaction: discord.Interaction):
    if not interaction.type == discord.InteractionType.component:
        return

    custom_id = interaction.data['custom_id']
    if custom_id.startswith("start-"):
        vps_id = int(custom_id.split("-")[1])
        c.execute('SELECT container_id FROM vps WHERE id=?', (vps_id,))
        row = c.fetchone()
        if not row:
            await interaction.response.send_message("VPS not found.", ephemeral=True)
            return
        container_id = row[0]
        try:
            container = client.containers.get(container_id)
            container.start()
            c.execute('UPDATE vps SET status="active" WHERE id=?', (vps_id,))
            conn.commit()
            log_action("start", interaction.user.id, vps_id)
            await interaction.response.send_message(f"VPS#{vps_id} started.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error starting VPS: {e}", ephemeral=True)

    elif custom_id.startswith("stop-"):
        vps_id = int(custom_id.split("-")[1])
        c.execute('SELECT container_id FROM vps WHERE id=?', (vps_id,))
        row = c.fetchone()
        if not row:
            await interaction.response.send_message("VPS not found.", ephemeral=True)
            return
        container_id = row[0]
        try:
            container = client.containers.get(container_id)
            container.stop()
            c.execute('UPDATE vps SET status="stopped" WHERE id=?', (vps_id,))
            conn.commit()
            log_action("stop", interaction.user.id, vps_id)
            await interaction.response.send_message(f"VPS#{vps_id} stopped.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error stopping VPS: {e}", ephemeral=True)

    elif custom_id.startswith("restart-"):
        vps_id = int(custom_id.split("-")[1])
        c.execute('SELECT container_id FROM vps WHERE id=?', (vps_id,))
        row = c.fetchone()
        if not row:
            await interaction.response.send_message("VPS not found.", ephemeral=True)
            return
        container_id = row[0]
        try:
            container = client.containers.get(container_id)
            container.restart()
            log_action("restart", interaction.user.id, vps_id)
            await interaction.response.send_message(f"VPS#{vps_id} restarted.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error restarting VPS: {e}", ephemeral=True)

    elif custom_id.startswith("ssh-"):
        vps_id = int(custom_id.split("-")[1])
        # Placeholder for tmate SSH link generation
        tmate_link = f"https://tmate.io/t/{vps_id}"
        log_action("ssh", interaction.user.id, vps_id)
        await interaction.response.send_message(f"SSH link for VPS#{vps_id}: {tmate_link}", ephemeral=True)
# ============================ SHARE USER COMMANDS ============================
@bot.tree.command(name="share-user", description="Share your VPS with another user")
@app_commands.describe(vps_number="Number of your VPS", user="User to share with")
async def share_user(interaction: Interaction, vps_number: int, user: discord.User):
    c.execute('SELECT id FROM vps WHERE user_id=? AND vps_number=?', (interaction.user.id, vps_number))
    row = c.fetchone()
    if not row:
        await interaction.response.send_message("VPS not found.", ephemeral=True)
        return
    vps_id = row[0]
    c.execute('INSERT OR IGNORE INTO vps_shared (vps_id, user_id) VALUES (?, ?)', (vps_id, user.id))
    conn.commit()
    log_action("share_user", interaction.user.id, user.id)
    await interaction.response.send_message(f"VPS#{vps_number} shared with {user.mention}.", ephemeral=True)

@bot.tree.command(name="share-ruser", description="Remove a shared user from your VPS")
@app_commands.describe(vps_number="Number of your VPS", user="User to remove")
async def share_ruser(interaction: Interaction, vps_number: int, user: discord.User):
    c.execute('SELECT id FROM vps WHERE user_id=? AND vps_number=?', (interaction.user.id, vps_number))
    row = c.fetchone()
    if not row:
        await interaction.response.send_message("VPS not found.", ephemeral=True)
        return
    vps_id = row[0]
    c.execute('DELETE FROM vps_shared WHERE vps_id=? AND user_id=?', (vps_id, user.id))
    conn.commit()
    log_action("remove_shared", interaction.user.id, user.id)
    await interaction.response.send_message(f"{user.mention} removed from shared VPS#{vps_number}.", ephemeral=True)
# ============================ ADMIN COMMANDS ============================

from discord import app_commands

# ---------- /create ----------
@bot.tree.command(name="create", description="Create a new VPS for a user (Admin only)")
@app_commands.describe(user="User to give VPS", os_type="OS for the VPS (debian/ubuntu)")
async def create(interaction: Interaction, user: discord.User, os_type: str):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("You are not allowed to use this command.", ephemeral=True)
        return

    # Determine next VPS number for the user
    c.execute('SELECT COUNT(*) FROM vps WHERE user_id=?', (user.id,))
    count = c.fetchone()[0]
    vps_number = count + 1

    # Create Docker container (simplified)
    image = "debian:latest" if os_type.lower() == "debian" else "ubuntu:latest"
    try:
        container = client.containers.run(image, detach=True, tty=True, name=f"vps-{user.id}-{vps_number}")
        container_id = container.id
        expires_at = (datetime.utcnow() + timedelta(weeks=2)).isoformat()  # 2-week expiration

        # Insert into DB
        c.execute('INSERT INTO vps (user_id, container_id, os, expires_at, status, vps_number) VALUES (?, ?, ?, ?, ?, ?)',
                  (user.id, container_id, os_type, expires_at, "active", vps_number))
        conn.commit()

        log_action("create_vps", interaction.user.id, user.id)
        await interaction.response.send_message(f"VPS#{vps_number} created for {user.mention}.", ephemeral=True)

        # Give VPS role if first VPS
        if count == 0:
            role = discord.utils.get(interaction.guild.roles, id=VPS_USER_ROLE_ID)
            if role:
                await user.add_roles(role)
    except Exception as e:
        await interaction.response.send_message(f"Error creating VPS: {e}", ephemeral=True)

# ---------- /suspend-vps ----------
@bot.tree.command(name="suspend-vps", description="Suspend a VPS (Admin only)")
@app_commands.describe(user="Owner of the VPS", vps_number="VPS number")
async def suspend_vps(interaction: Interaction, user: discord.User, vps_number: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("Not allowed.", ephemeral=True)
        return

    c.execute('SELECT id, container_id FROM vps WHERE user_id=? AND vps_number=?', (user.id, vps_number))
    row = c.fetchone()
    if not row:
        await interaction.response.send_message("VPS not found.", ephemeral=True)
        return
    vps_id, container_id = row
    try:
        container = client.containers.get(container_id)
        container.stop()
        c.execute('UPDATE vps SET status="suspended" WHERE id=?', (vps_id,))
        conn.commit()
        log_action("suspend_vps", interaction.user.id, user.id)
        await interaction.response.send_message(f"VPS#{vps_number} suspended.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error suspending VPS: {e}", ephemeral=True)

# ---------- /unsuspend-vps ----------
@bot.tree.command(name="unsuspend-vps", description="Unsuspend a VPS (Admin only)")
@app_commands.describe(user="Owner of the VPS", vps_number="VPS number")
async def unsuspend_vps(interaction: Interaction, user: discord.User, vps_number: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("Not allowed.", ephemeral=True)
        return

    c.execute('SELECT id, container_id FROM vps WHERE user_id=? AND vps_number=?', (user.id, vps_number))
    row = c.fetchone()
    if not row:
        await interaction.response.send_message("VPS not found.", ephemeral=True)
        return
    vps_id, container_id = row
    try:
        container = client.containers.get(container_id)
        container.start()
        c.execute('UPDATE vps SET status="active" WHERE id=?', (vps_id,))
        conn.commit()
        log_action("unsuspend_vps", interaction.user.id, user.id)
        await interaction.response.send_message(f"VPS#{vps_number} unsuspended.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error unsuspending VPS: {e}", ephemeral=True)

# ---------- /remove ----------
@bot.tree.command(name="remove", description="Remove a VPS (Admin only)")
@app_commands.describe(user="Owner of the VPS", vps_number="VPS number")
async def remove(interaction: Interaction, user: discord.User, vps_number: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("Not allowed.", ephemeral=True)
        return

    c.execute('SELECT id, container_id FROM vps WHERE user_id=? AND vps_number=?', (user.id, vps_number))
    row = c.fetchone()
    if not row:
        await interaction.response.send_message("VPS not found.", ephemeral=True)
        return
    vps_id, container_id = row
    try:
        container = client.containers.get(container_id)
        container.stop()
        container.remove()
        c.execute('DELETE FROM vps WHERE id=?', (vps_id,))
        c.execute('DELETE FROM vps_shared WHERE vps_id=?', (vps_id,))
        conn.commit()
        log_action("remove_vps", interaction.user.id, user.id)
        await interaction.response.send_message(f"VPS#{vps_number} removed.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error removing VPS: {e}", ephemeral=True)

# ---------- /set-renewal-request-channel ----------
@bot.tree.command(name="set-renewal-request-channel", description="Set the renewal request channel")
@app_commands.describe(channel="Channel for VPS renewal requests")
async def set_renewal_request_channel(interaction: Interaction, channel: discord.TextChannel):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("Not allowed.", ephemeral=True)
        return
    c.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', ("renewal_request_channel", str(channel.id)))
    conn.commit()
    await interaction.response.send_message(f"Renewal request channel set to {channel.mention}.", ephemeral=True)
# ============================ RUN BOT ============================
bot.run(TOKEN)
