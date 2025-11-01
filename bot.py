import discord
from discord import app_commands, Interaction
from discord.ui import View, Select, Button
import sqlite3
from datetime import datetime, timedelta
import docker
import asyncio
import random
import time

# ================= CONFIG =================
TOKEN = "YOUR_BOT_TOKEN"
GUILD_ID = 123456789012345678  # Replace with your guild ID
VPS_USER_ROLE_ID = 1434000306357928047
ADMIN_IDS = [1421860082894766183]
LOG_CHANNEL_ID = None
RENEWAL_CHANNEL_ID = None

# ================= SETUP BOT =================
intents = discord.Intents.default()
intents.members = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)
client = docker.from_env()
scheduler = None  # will assign AsyncIOScheduler later

# ================= DATABASE =================
conn = sqlite3.connect('vps.db')
c = conn.cursor()
c.execute('''
CREATE TABLE IF NOT EXISTS vps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    vps_number INTEGER,
    container_id TEXT,
    status TEXT,
    os_type TEXT,
    ram INTEGER,
    cpu REAL,
    disk INTEGER,
    expires_at TEXT
)
''')
c.execute('''
CREATE TABLE IF NOT EXISTS vps_shared (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vps_id INTEGER,
    user_id INTEGER
)
''')
conn.commit()

# ================= HELPERS =================
def is_admin(user_id):
    return user_id in ADMIN_IDS

def log_action(action, admin_id, user_id):
    if LOG_CHANNEL_ID:
        channel = bot.get_channel(LOG_CHANNEL_ID)
        if channel:
            asyncio.create_task(channel.send(f"📌 Action: {action}, Admin: <@{admin_id}>, User: <@{user_id}>"))

def next_vps_number(user_id):
    c.execute('SELECT MAX(vps_number) FROM vps WHERE user_id=?', (user_id,))
    row = c.fetchone()[0]
    return (row or 0) + 1
# ================= CREATE VPS (ADMIN ONLY) =================
@tree.command(name="create", description="🆕 Create a new VPS (Admin only)")
@app_commands.describe(user="User to assign VPS", os_type="OS type: debian or ubuntu", ram="RAM in MB", cpu="CPU cores", disk="Disk size in MB")
async def create(interaction: Interaction, user: discord.User, os_type: str, ram: int, cpu: float, disk: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    vps_number = next_vps_number(user.id)
    timestamp = int(time.time())
    container_name = f"vps-{user.id}-{vps_number}-{timestamp}"
    image = "ubuntu:22.04" if os_type.lower() == "ubuntu" else "debian:12"

    try:
        container = client.containers.run(
            image=image,
            name=container_name,
            detach=True,
            tty=True,
            mem_limit=f"{ram}m",
            cpu_quota=int(cpu*100000),
        )
        expires_at = (datetime.utcnow() + timedelta(days=14)).isoformat()
        c.execute('INSERT INTO vps(user_id,vps_number,container_id,status,os_type,ram,cpu,disk,expires_at) VALUES (?,?,?,?,?,?,?,?,?)',
                  (user.id, vps_number, container.id, "active", os_type.lower(), ram, cpu, disk, expires_at))
        conn.commit()

        # Assign role if first VPS
        member = interaction.guild.get_member(user.id)
        if member and VPS_USER_ROLE_ID not in [r.id for r in member.roles]:
            role = interaction.guild.get_role(VPS_USER_ROLE_ID)
            asyncio.create_task(member.add_roles(role))

        await interaction.followup.send(f"✅ VPS#{vps_number} created for {user.mention}.", ephemeral=True)
        log_action("create", interaction.user.id, user.id)

    except Exception as e:
        await interaction.followup.send(f"❌ Error creating VPS: {e}", ephemeral=True)

# ================= SUSPEND VPS (ADMIN ONLY) =================
@tree.command(name="suspend-vps", description="⏸️ Suspend a VPS (Admin only)")
@app_commands.describe(vps_number="VPS number")
async def suspend_vps(interaction: Interaction, vps_number: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    c.execute('SELECT container_id,user_id FROM vps WHERE vps_number=?', (vps_number,))
    row = c.fetchone()
    if not row:
        await interaction.followup.send("❌ VPS not found.", ephemeral=True)
        return
    container_id, user_id = row
    container = client.containers.get(container_id)
    container.stop()
    c.execute('UPDATE vps SET status="suspended" WHERE vps_number=?', (vps_number,))
    conn.commit()
    await interaction.followup.send(f"✅ VPS#{vps_number} suspended.", ephemeral=True)
    log_action("suspend_vps", interaction.user.id, user_id)

# ================= UNSUSPEND VPS (ADMIN ONLY) =================
@tree.command(name="unsuspend-vps", description="▶️ Unsuspend a VPS (Admin only)")
@app_commands.describe(vps_number="VPS number")
async def unsuspend_vps(interaction: Interaction, vps_number: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    c.execute('SELECT container_id,user_id FROM vps WHERE vps_number=?', (vps_number,))
    row = c.fetchone()
    if not row:
        await interaction.followup.send("❌ VPS not found.", ephemeral=True)
        return
    container_id, user_id = row
    container = client.containers.get(container_id)
    container.start()
    c.execute('UPDATE vps SET status="active" WHERE vps_number=?', (vps_number,))
    conn.commit()
    await interaction.followup.send(f"✅ VPS#{vps_number} unsuspended.", ephemeral=True)
    log_action("unsuspend_vps", interaction.user.id, user_id)

# ================= REMOVE VPS (ADMIN ONLY) =================
@tree.command(name="remove", description="🗑️ Remove a VPS (Admin only)")
@app_commands.describe(vps_number="VPS number")
async def remove(interaction: Interaction, vps_number: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    c.execute('SELECT container_id,user_id FROM vps WHERE vps_number=?', (vps_number,))
    row = c.fetchone()
    if not row:
        await interaction.followup.send("❌ VPS not found.", ephemeral=True)
        return
    container_id, user_id = row
    try:
        container = client.containers.get(container_id)
        container.remove(force=True)
    except docker.errors.NotFound:
        pass
    c.execute('DELETE FROM vps WHERE vps_number=?', (vps_number,))
    c.execute('DELETE FROM vps_shared WHERE vps_id=?', (vps_number,))
    conn.commit()
    await interaction.followup.send(f"✅ VPS#{vps_number} removed.", ephemeral=True)
    log_action("remove_vps", interaction.user.id, user_id)
# ================= MANAGE VPS =================
@tree.command(name="manage", description="🛠️ Manage your VPS")
@app_commands.describe(vps_number="Optional VPS number (Admins can manage others)")
async def manage(interaction: Interaction, vps_number: int = None):
    await interaction.response.defer(ephemeral=True)
    user = interaction.user

    # Admin can manage any VPS if number provided
    if vps_number and is_admin(user.id):
        c.execute('SELECT vps_number,user_id,container_id,status FROM vps WHERE vps_number=?', (vps_number,))
    else:
        c.execute('SELECT vps_number,user_id,container_id,status FROM vps WHERE user_id=?', (user.id,))
    rows = c.fetchall()
    if not rows:
        await interaction.followup.send("❌ No VPS found.", ephemeral=True)
        return

    # If multiple VPSes, create a dropdown
    if len(rows) > 1:
        options = [discord.SelectOption(label=f"VPS#{row[0]} ({row[3]})", value=str(row[0])) for row in rows]
        select = Select(placeholder="Select a VPS to manage", options=options)

        async def callback(select_interaction):
            selected = int(select.values[0])
            await select_interaction.response.send_message(f"Selected VPS#{selected}", ephemeral=True)

        select.callback = callback
        view = View()
        view.add_item(select)
        await interaction.followup.send("Select a VPS to manage:", view=view, ephemeral=True)
    else:
        vps_number, vps_user_id, container_id, status = rows[0]
        await interaction.followup.send(f"VPS#{vps_number} - Status: {status}", ephemeral=True)

# ================= SHARE VPS =================
@tree.command(name="share-user", description="🔗 Share a VPS with another user")
@app_commands.describe(vps_number="VPS number", user="User to share with")
async def share_user(interaction: Interaction, vps_number: int, user: discord.User):
    await interaction.response.defer(ephemeral=True)
    c.execute('SELECT id FROM vps WHERE vps_number=?', (vps_number,))
    row = c.fetchone()
    if not row:
        await interaction.followup.send("❌ VPS not found.", ephemeral=True)
        return
    vps_id = row[0]
    c.execute('INSERT INTO vps_shared(vps_id,user_id) VALUES (?,?)', (vps_id, user.id))
    conn.commit()
    await interaction.followup.send(f"✅ VPS#{vps_number} shared with {user.mention}.", ephemeral=True)

# ================= UNSHARE VPS =================
@tree.command(name="share-ruser", description="❌ Remove shared user from VPS")
@app_commands.describe(vps_number="VPS number", user="User to remove")
async def share_ruser(interaction: Interaction, vps_number: int, user: discord.User):
    await interaction.response.defer(ephemeral=True)
    c.execute('SELECT id FROM vps WHERE vps_number=?', (vps_number,))
    row = c.fetchone()
    if not row:
        await interaction.followup.send("❌ VPS not found.", ephemeral=True)
        return
    vps_id = row[0]
    c.execute('DELETE FROM vps_shared WHERE vps_id=? AND user_id=?', (vps_id, user.id))
    conn.commit()
    await interaction.followup.send(f"✅ Removed {user.mention} from VPS#{vps_number}.", ephemeral=True)

# ================= MANAGE SHARED VPS =================
@tree.command(name="manage-shared", description="🛠️ Manage VPS shared with you")
async def manage_shared(interaction: Interaction):
    await interaction.response.defer(ephemeral=True)
    c.execute('SELECT v.vps_number, v.user_id FROM vps_shared s JOIN vps v ON s.vps_id=v.id WHERE s.user_id=?', (interaction.user.id,))
    rows = c.fetchall()
    if not rows:
        await interaction.followup.send("❌ No shared VPSes found.", ephemeral=True)
        return

    options = [discord.SelectOption(label=f"VPS#{v[0]} (Owner: {v[1]})", value=str(v[0])) for v in rows]
    select = Select(placeholder="Select a shared VPS", options=options)

    async def callback(select_interaction):
        selected = int(select.values[0])
        await select_interaction.response.send_message(f"Selected shared VPS#{selected}", ephemeral=True)

    select.callback = callback
    view = View()
    view.add_item(select)
    await interaction.followup.send("Select a shared VPS:", view=view, ephemeral=True)

# ================= PORT GIVE (ADMIN ONLY) =================
@tree.command(name="port-give", description="📦 Give a port to a VPS (Admin only)")
@app_commands.describe(vps_number="VPS number", user="User", port="Port number")
async def port_give(interaction: Interaction, vps_number: int, user: discord.User, port: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    # Just log the assignment for now
    await interaction.response.send_message(f"✅ Port {port} given to VPS#{vps_number} for {user.mention}.", ephemeral=True)
    log_action("port_give", interaction.user.id, user.id)
# ================= ADMIN ADD =================
@tree.command(name="admin-add", description="👑 Add another admin")
@app_commands.describe(user="User to make admin")
async def admin_add(interaction: Interaction, user: discord.User):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    if user.id not in ADMIN_IDS:
        ADMIN_IDS.append(user.id)
    await interaction.response.send_message(f"✅ {user.mention} is now an admin.", ephemeral=True)
    log_action("admin_add", interaction.user.id, user.id)

# ================= LOG CHANNEL SET =================
@tree.command(name="log-channel-set", description="📄 Set the public log channel")
@app_commands.describe(channel="Channel for logging")
async def log_channel_set(interaction: Interaction, channel: discord.TextChannel):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    global LOG_CHANNEL_ID
    LOG_CHANNEL_ID = channel.id
    await interaction.response.send_message(f"✅ Log channel set to {channel.mention}", ephemeral=True)

# ================= RENEWAL REQUEST CHANNEL =================
@tree.command(name="set-renewal-request-channel", description="📩 Set the renewal request channel")
@app_commands.describe(channel="Channel for renewal requests")
async def set_renewal_request_channel(interaction: Interaction, channel: discord.TextChannel):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    global RENEWAL_CHANNEL_ID
    RENEWAL_CHANNEL_ID = channel.id
    await interaction.response.send_message(f"✅ Renewal request channel set to {channel.mention}", ephemeral=True)

# ================= SCHEDULER TASK =================
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

def auto_suspend_expired():
    now = datetime.utcnow()
    c.execute('SELECT user_id,vps_number,container_id FROM vps WHERE status="active"')
    rows = c.fetchall()
    for user_id, vps_number, container_id in rows:
        c.execute('SELECT expires_at FROM vps WHERE user_id=? AND vps_number=?', (user_id, vps_number))
        expires = datetime.fromisoformat(c.fetchone()[0])
        if now > expires:
            try:
                container = client.containers.get(container_id)
                container.stop()
            except docker.errors.NotFound:
                pass
            c.execute('UPDATE vps SET status="suspended" WHERE user_id=? AND vps_number=?', (user_id, vps_number))
            conn.commit()
            log_action("auto_suspend", 0, user_id)

scheduler.add_job(auto_suspend_expired, 'interval', minutes=60)

# ================= BOT STARTUP =================
@bot.event
async def on_ready():
    print("✅ Bot is Online")
    guild = bot.get_guild(GUILD_ID)
    if guild:
        await tree.sync(guild=guild)
    else:
        await tree.sync()
    print("✅ Commands synced")
    if not scheduler.running:
        scheduler.start()
        print("✅ Scheduler started")

# ================= RUN BOT =================
bot.run(TOKEN)
