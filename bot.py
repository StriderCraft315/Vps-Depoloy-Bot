import os
import discord
from discord import app_commands, Interaction
from discord.ui import View, Select, Button
import sqlite3
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import docker

# ================= CONFIG =================
TOKEN = "YOUR_BOT_TOKEN"  # Will be replaced by installer
GUILD_ID = 123456789012345678  # Replace with your guild/server ID
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
# ================= CREATE VPS =================
@tree.command(name="create", description="🖥️ Create a new VPS (Admin only)")
@app_commands.describe(user="👤 User", os_type="💻 OS type (ubuntu/debian)", ram="💾 RAM in MB", cpu="⚡ CPU cores", disk="📦 Disk in MB")
async def create(interaction: Interaction, user: discord.User, os_type: str, ram: int, cpu: float, disk: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)  # defer for long tasks
    vps_number = next_vps_number(user.id)
    container_name = f"vps-{user.id}-{vps_number}"

    # OS selection
    if os_type.lower() == "debian":
        image = "debian:bookworm-slim"
    elif os_type.lower() == "ubuntu":
        image = "ubuntu:22.04"
    else:
        await interaction.followup.send("❌ Invalid OS type", ephemeral=True)
        return

    # Run container with resource limits
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
    guild = bot.get_guild(GUILD_ID)
    if guild:
        member = guild.get_member(user.id)
        if member:
            role = guild.get_role(VPS_USER_ROLE_ID)
            if role:
                await member.add_roles(role)

    await interaction.followup.send(f"✅ VPS#{vps_number} created for {user.mention}.", ephemeral=True)

# ================= SUSPEND VPS =================
@tree.command(name="suspend-vps", description="⛔ Suspend a VPS (Admin only)")
@app_commands.describe(user="👤 User", vps_number="VPS number to suspend")
async def suspend_vps(interaction: Interaction, user: discord.User, vps_number: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    c.execute('SELECT container_id,status FROM vps WHERE user_id=? AND vps_number=?', (user.id, vps_number))
    row = c.fetchone()
    if not row:
        await interaction.followup.send("❌ VPS not found.", ephemeral=True)
        return

    container_id, status = row
    if status == "suspended":
        await interaction.followup.send("⚠️ VPS already suspended.", ephemeral=True)
        return

    container = client.containers.get(container_id)
    container.stop()
    c.execute('UPDATE vps SET status=? WHERE user_id=? AND vps_number=?', ("suspended", user.id, vps_number))
    conn.commit()
    log_action("suspend_vps", interaction.user.id, user.id)
    await interaction.followup.send(f"✅ VPS#{vps_number} suspended.", ephemeral=True)

# ================= UNSUSPEND VPS =================
@tree.command(name="unsuspend-vps", description="✅ Unsuspend a VPS (Admin only)")
@app_commands.describe(user="👤 User", vps_number="VPS number to unsuspend")
async def unsuspend_vps(interaction: Interaction, user: discord.User, vps_number: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    c.execute('SELECT container_id,status FROM vps WHERE user_id=? AND vps_number=?', (user.id, vps_number))
    row = c.fetchone()
    if not row:
        await interaction.followup.send("❌ VPS not found.", ephemeral=True)
        return

    container_id, status = row
    if status != "suspended":
        await interaction.followup.send("⚠️ VPS is not suspended.", ephemeral=True)
        return

    container = client.containers.get(container_id)
    container.start()
    c.execute('UPDATE vps SET status=? WHERE user_id=? AND vps_number=?', ("active", user.id, vps_number))
    conn.commit()
    log_action("unsuspend_vps", interaction.user.id, user.id)
    await interaction.followup.send(f"✅ VPS#{vps_number} unsuspended.", ephemeral=True)

# ================= REMOVE VPS =================
@tree.command(name="remove", description="❌ Remove a VPS (Admin only)")
@app_commands.describe(user="👤 User", vps_number="VPS number to remove")
async def remove(interaction: Interaction, user: discord.User, vps_number: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    c.execute('SELECT container_id FROM vps WHERE user_id=? AND vps_number=?', (user.id, vps_number))
    row = c.fetchone()
    if not row:
        await interaction.followup.send("❌ VPS not found.", ephemeral=True)
        return

    container_id = row[0]
    container = client.containers.get(container_id)
    container.remove(force=True)
    c.execute('DELETE FROM vps WHERE user_id=? AND vps_number=?', (user.id, vps_number))
    c.execute('DELETE FROM vps_shared WHERE vps_id=?', (vps_number,))
    conn.commit()
    log_action("remove_vps", interaction.user.id, user.id)
    await interaction.followup.send(f"✅ VPS#{vps_number} removed.", ephemeral=True)
# ================= MANAGE VPS =================
@tree.command(name="manage", description="🛠️ Manage your VPS")
@app_commands.describe(vps_number="Optional VPS number to manage (Admins can select user VPSes)")
async def manage(interaction: Interaction, vps_number: int = None):
    await interaction.response.defer(ephemeral=True)
    guild = bot.get_guild(GUILD_ID)
    user = interaction.user

    # Admin option to manage others
    if vps_number is None:
        c.execute('SELECT vps_number, user_id FROM vps WHERE user_id=?', (user.id,))
    else:
        if is_admin(user.id):
            c.execute('SELECT vps_number, user_id FROM vps WHERE vps_number=?', (vps_number,))
        else:
            c.execute('SELECT vps_number, user_id FROM vps WHERE user_id=? AND vps_number=?', (user.id, vps_number))
    rows = c.fetchall()
    if not rows:
        await interaction.followup.send("❌ No VPS found.", ephemeral=True)
        return

    # Create a dropdown menu for VPS selection
    options = [discord.SelectOption(label=f"VPS#{r[0]} - UserID:{r[1]}", value=str(r[0])) for r in rows]
    select = Select(placeholder="Select VPS", options=options)

    async def select_callback(sel_inter: Interaction):
        selected = int(sel_inter.data["values"][0])
        c.execute('SELECT container_id,status FROM vps WHERE vps_number=?', (selected,))
        row = c.fetchone()
        status = row[1]
        container_id = row[0]

        view = View()
        view.add_item(Button(label="Start", style=discord.ButtonStyle.green, custom_id=f"start_{selected}"))
        view.add_item(Button(label="Stop", style=discord.ButtonStyle.red, custom_id=f"stop_{selected}"))
        view.add_item(Button(label="Restart", style=discord.ButtonStyle.blurple, custom_id=f"restart_{selected}"))
        view.add_item(Button(label="SSH / tmate", style=discord.ButtonStyle.gray, custom_id=f"ssh_{selected}"))
        await sel_inter.response.send_message(f"Manage VPS#{selected} (Status: {status})", view=view, ephemeral=True)

    select.callback = select_callback
    view = View()
    view.add_item(select)
    await interaction.followup.send("Select a VPS to manage:", view=view, ephemeral=True)

# ================= SHARE USER =================
@tree.command(name="share-user", description="🔗 Share your VPS with another user")
@app_commands.describe(vps_number="VPS number", user="User to share with")
async def share_user(interaction: Interaction, vps_number: int, user: discord.User):
    await interaction.response.defer(ephemeral=True)
    c.execute('SELECT id FROM vps WHERE user_id=? AND vps_number=?', (interaction.user.id, vps_number))
    row = c.fetchone()
    if not row:
        await interaction.followup.send("❌ VPS not found or not yours.", ephemeral=True)
        return
    vps_id = row[0]
    c.execute('INSERT OR IGNORE INTO vps_shared(vps_id,user_id) VALUES (?,?)', (vps_id, user.id))
    conn.commit()
    log_action("share_user", interaction.user.id, user.id)
    await interaction.followup.send(f"✅ VPS#{vps_number} shared with {user.mention}", ephemeral=True)

# ================= REMOVE SHARED USER =================
@tree.command(name="share-ruser", description="❌ Remove shared user from VPS")
@app_commands.describe(vps_number="VPS number", user="User to remove")
async def share_ruser(interaction: Interaction, vps_number: int, user: discord.User):
    await interaction.response.defer(ephemeral=True)
    c.execute('SELECT id FROM vps WHERE user_id=? AND vps_number=?', (interaction.user.id, vps_number))
    row = c.fetchone()
    if not row:
        await interaction.followup.send("❌ VPS not found or not yours.", ephemeral=True)
        return
    vps_id = row[0]
    c.execute('DELETE FROM vps_shared WHERE vps_id=? AND user_id=?', (vps_id, user.id))
    conn.commit()
    log_action("remove_shared_user", interaction.user.id, user.id)
    await interaction.followup.send(f"✅ Removed {user.mention} from VPS#{vps_number}", ephemeral=True)

# ================= MANAGE SHARED =================
@tree.command(name="manage-shared", description="🛠️ Manage VPSes shared with you")
async def manage_shared(interaction: Interaction):
    await interaction.response.defer(ephemeral=True)
    c.execute('SELECT v.vps_number, v.user_id FROM vps_shared s JOIN vps v ON s.vps_id=v.id WHERE s.user_id=?', (interaction.user.id,))
    rows = c.fetchall()
    if not rows:
        await interaction.followup.send("❌ No shared VPSes.", ephemeral=True)
        return

    options = [discord.SelectOption(label=f"VPS#{r[0]} - Owner:{r[1]}", value=str(r[0])) for r in rows]
    select = Select(placeholder="Select shared VPS", options=options)

    async def shared_select_callback(sel_inter: Interaction):
        selected = int(sel_inter.data["values"][0])
        c.execute('SELECT container_id,status FROM vps WHERE vps_number=?', (selected,))
        row = c.fetchone()
        status = row[1]
        view = View()
        view.add_item(Button(label="SSH / tmate", style=discord.ButtonStyle.gray, custom_id=f"ssh_{selected}"))
        await sel_inter.response.send_message(f"Shared VPS#{selected} (Status: {status})", view=view, ephemeral=True)

    select.callback = shared_select_callback
    view = View()
    view.add_item(select)
    await interaction.followup.send("Select a shared VPS to manage:", view=view, ephemeral=True)

# ================= PORT GIVE (Admin Only) =================
@tree.command(name="port-give", description="🔌 Give a port to a VPS (Admin only)")
@app_commands.describe(user="User", vps_number="VPS number", port="Port to assign")
async def port_give(interaction: Interaction, user: discord.User, vps_number: int, port: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    # For simplicity, we just log port assignment
    log_action("port_give", interaction.user.id, user.id)
    await interaction.followup.send(f"✅ Port {port} assigned to VPS#{vps_number} for {user.mention}", ephemeral=True)
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
def auto_suspend_expired():
    now = datetime.utcnow()
    c.execute('SELECT user_id,vps_number,container_id FROM vps WHERE status="active"')
    rows = c.fetchall()
    for user_id, vps_number, container_id in rows:
        c.execute('SELECT expires_at FROM vps WHERE user_id=? AND vps_number=?', (user_id, vps_number))
        expires = datetime.fromisoformat(c.fetchone()[0])
        if now > expires:
            container = client.containers.get(container_id)
            container.stop()
            c.execute('UPDATE vps SET status="suspended" WHERE user_id=? AND vps_number=?', (user_id, vps_number))
            conn.commit()
            log_action("auto_suspend", 0, user_id)

scheduler.add_job(auto_suspend_expired, 'interval', minutes=5)
scheduler.start()

# ================= BOT STARTUP =================
@bot.event
async def on_ready():
    print("✅ Bot is Online")
    guild = bot.get_guild(GUILD_ID)
    if guild:
        # Clear old commands and sync
        await tree.sync(guild=guild)
    else:
        await tree.sync()
    print("✅ Commands synced")

# ================= RUN BOT =================
bot.run(TOKEN)
