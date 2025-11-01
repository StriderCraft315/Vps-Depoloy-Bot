import discord
from discord import app_commands, Interaction
from discord.ui import View, Button, Select
import asyncio
import random
import string
import aiosqlite
import subprocess
from apscheduler.schedulers.asyncio import AsyncIOScheduler

TOKEN = "YOUR_BOT_TOKEN_HERE"
GUILD_ID = 123456789012345678  # replace with your guild ID
LOG_CHANNEL_ID = None  # can be set with /set-log-channel

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
scheduler = AsyncIOScheduler()

DB_PATH = "vpsbot.db"

# Utility functions
async def get_db():
    db = await aiosqlite.connect(DB_PATH)
    await db.execute(
        """CREATE TABLE IF NOT EXISTS admins(user_id INTEGER PRIMARY KEY)"""
    )
    await db.execute(
        """CREATE TABLE IF NOT EXISTS vpses(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER,
            number INTEGER,
            hostname TEXT,
            os TEXT,
            status TEXT
        )"""
    )
    await db.commit()
    return db

def random_hostname():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

async def is_admin(user_id: int):
    db = await get_db()
    async with db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)) as cursor:
        row = await cursor.fetchone()
    await db.close()
    return row is not None

async def send_log(message: str):
    if LOG_CHANNEL_ID:
        channel = client.get_channel(LOG_CHANNEL_ID)
        if channel:
            await channel.send(message)
# ---------------- COMMANDS ---------------- #

@tree.command(name="create", description="🖥️ Create a new VPS (admin only)")
@app_commands.describe(user="The user to assign the VPS to", os="OS: debian or ubuntu", ram="RAM in GB", disk="Disk in GB", cpu="CPU cores")
async def create(interaction: Interaction, user: discord.User, os: str, ram: int, disk: int, cpu: int):
    if not await is_admin(interaction.user.id):
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return

    os = os.lower()
    if os not in ["debian", "ubuntu"]:
        await interaction.response.send_message("❌ OS must be 'debian' or 'ubuntu'.", ephemeral=True)
        return

    db = await get_db()
    # Get next VPS number for user
    async with db.execute("SELECT MAX(number) FROM vpses WHERE owner_id = ?", (user.id,)) as cursor:
        row = await cursor.fetchone()
        next_number = 1 if row[0] is None else row[0] + 1

    hostname = random_hostname()
    status = "running"

    # Create LXC container
    try:
        subprocess.run([
            "lxc", "launch", f"images:{os}/latest", hostname,
            "-c", f"limits.memory={ram}GB",
            "-c", f"limits.cpu={cpu}"
        ], check=True)
    except subprocess.CalledProcessError:
        await interaction.response.send_message("❌ Failed to create LXC container.", ephemeral=True)
        await db.close()
        return

    await db.execute(
        "INSERT INTO vpses(owner_id, number, hostname, os, status) VALUES (?, ?, ?, ?, ?)",
        (user.id, next_number, hostname, os, status)
    )
    await db.commit()
    await db.close()

    embed = discord.Embed(title=f"✅ VPS Created: {os.capitalize()} #{next_number}",
                          description=f"Owner: {user.mention}\nHostname: `{hostname}`\nRAM: {ram}GB\nDisk: {disk}GB\nCPU: {cpu} cores",
                          color=discord.Color.green())
    await interaction.response.send_message(embed=embed, ephemeral=True)
    await send_log(f"@{user} Deployed {os.capitalize()} {next_number} For @{user}")

# Admin-only suspend command
@tree.command(name="suspend", description="⛔ Suspend a VPS (admin only)")
@app_commands.describe(owner="Owner of VPS", number="VPS number")
async def suspend(interaction: Interaction, owner: discord.User, number: int):
    if not await is_admin(interaction.user.id):
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return
    db = await get_db()
    async with db.execute("SELECT hostname FROM vpses WHERE owner_id=? AND number=?", (owner.id, number)) as cursor:
        row = await cursor.fetchone()
    if not row:
        await interaction.response.send_message("❌ VPS not found.", ephemeral=True)
        await db.close()
        return
    hostname = row[0]
    subprocess.run(["lxc", "stop", hostname], check=False)
    await db.execute("UPDATE vpses SET status='suspended' WHERE owner_id=? AND number=?", (owner.id, number))
    await db.commit()
    await db.close()
    await interaction.response.send_message(f"✅ VPS #{number} suspended.", ephemeral=True)
    await send_log(f"@{interaction.user} Suspended VPS #{number} for @{owner}")

# Admin-only unsuspend
@tree.command(name="unsuspend", description="✅ Unsuspend a VPS (admin only)")
@app_commands.describe(owner="Owner of VPS", number="VPS number")
async def unsuspend(interaction: Interaction, owner: discord.User, number: int):
    if not await is_admin(interaction.user.id):
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return
    db = await get_db()
    async with db.execute("SELECT hostname FROM vpses WHERE owner_id=? AND number=?", (owner.id, number)) as cursor:
        row = await cursor.fetchone()
    if not row:
        await interaction.response.send_message("❌ VPS not found.", ephemeral=True)
        await db.close()
        return
    hostname = row[0]
    subprocess.run(["lxc", "start", hostname], check=False)
    await db.execute("UPDATE vpses SET status='running' WHERE owner_id=? AND number=?", (owner.id, number))
    await db.commit()
    await db.close()
    await interaction.response.send_message(f"✅ VPS #{number} unsuspended.", ephemeral=True)
    await send_log(f"@{interaction.user} Unsuspended VPS #{number} for @{owner}")

# Admin-only remove
@tree.command(name="remove", description="❌ Remove a VPS (admin only)")
@app_commands.describe(owner="Owner of VPS", number="VPS number")
async def remove(interaction: Interaction, owner: discord.User, number: int):
    if not await is_admin(interaction.user.id):
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return
    db = await get_db()
    async with db.execute("SELECT hostname FROM vpses WHERE owner_id=? AND number=?", (owner.id, number)) as cursor:
        row = await cursor.fetchone()
    if not row:
        await interaction.response.send_message("❌ VPS not found.", ephemeral=True)
        await db.close()
        return
    hostname = row[0]
    subprocess.run(["lxc", "delete", "--force", hostname], check=False)
    await db.execute("DELETE FROM vpses WHERE owner_id=? AND number=?", (owner.id, number))
    await db.commit()
    await db.close()
    await interaction.response.send_message(f"✅ VPS #{number} removed.", ephemeral=True)
    await send_log(f"@{interaction.user} Removed VPS #{number} for @{owner}")

# Share VPS (only owner can share)
@tree.command(name="share", description="🔗 Share your VPS with another user")
@app_commands.describe(number="Your VPS number", target="User to share with")
async def share(interaction: Interaction, number: int, target: discord.User):
    db = await get_db()
    async with db.execute("SELECT owner_id FROM vpses WHERE owner_id=? AND number=?", (interaction.user.id, number)) as cursor:
        row = await cursor.fetchone()
    if not row:
        await interaction.response.send_message("❌ You do not own this VPS.", ephemeral=True)
        await db.close()
        return
    # Here you would add sharing logic (permissions table)
    await db.close()
    await interaction.response.send_message(f"✅ VPS #{number} shared with {target.mention}", ephemeral=True)
    await send_log(f"@{interaction.user} Shared VPS #{number} with @{target}")
# ---------------- LIST COMMANDS ---------------- #

# User command to list their VPSes
@tree.command(name="list", description="📄 List your VPSes")
async def list_vps(interaction: Interaction):
    db = await get_db()
    async with db.execute("SELECT number, os, status FROM vpses WHERE owner_id=?", (interaction.user.id,)) as cursor:
        rows = await cursor.fetchall()
    await db.close()

    if not rows:
        await interaction.response.send_message("❌ You have no VPSes.", ephemeral=True)
        return

    embed = discord.Embed(title="🖥️ Your VPSes", color=discord.Color.blue())
    for number, os_name, status in rows:
        embed.add_field(name=f"VPS #{number}", value=f"OS: {os_name.capitalize()}\nStatus: {status}", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

# Admin command to list all VPSes
@tree.command(name="list-all", description="📋 List all VPSes (admin only)")
async def list_all(interaction: Interaction):
    if not await is_admin(interaction.user.id):
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return

    db = await get_db()
    async with db.execute("SELECT owner_id, number, os, status FROM vpses") as cursor:
        rows = await cursor.fetchall()
    await db.close()

    if not rows:
        await interaction.response.send_message("❌ No VPSes found.", ephemeral=True)
        return

    embed = discord.Embed(title="🖥️ All VPSes", color=discord.Color.blue())
    for owner_id, number, os_name, status in rows:
        owner = bot.get_user(owner_id)
        embed.add_field(name=f"VPS #{number}", value=f"Owner: {owner.mention if owner else owner_id}\nOS: {os_name.capitalize()}\nStatus: {status}", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------------- MANAGE COMMAND ---------------- #

async def vps_action_button(interaction: Interaction, hostname: str, action: str):
    try:
        if action == "start":
            subprocess.run(["lxc", "start", hostname], check=False)
        elif action == "stop":
            subprocess.run(["lxc", "stop", hostname], check=False)
        elif action == "reinstall":
            # confirmation should be handled before
            subprocess.run(["lxc", "delete", "--force", hostname], check=False)
            subprocess.run(["lxc", "launch", "images:debian/12", hostname], check=False)
        elif action == "ssh":
            tmate = subprocess.run(["tmate", "-S", f"/tmp/{hostname}.sock", "new-session", "-d"], capture_output=True, text=True)
            ssh_cmd = tmate.stdout.strip()
            await interaction.response.send_message(f"🔗 SSH Session: `{ssh_cmd}`", ephemeral=True)
        await send_log(f"@{interaction.user} performed {action} on {hostname}")
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

@tree.command(name="manage", description="⚙️ Manage your VPS")
@app_commands.describe(number="VPS number to manage")
async def manage(interaction: Interaction, number: int, owner: Optional[discord.User] = None):
    db = await get_db()
    target_id = owner.id if owner else interaction.user.id
    async with db.execute("SELECT hostname, os, status FROM vpses WHERE owner_id=? AND number=?", (target_id, number)) as cursor:
        row = await cursor.fetchone()
    await db.close()

    if not row:
        await interaction.response.send_message("❌ VPS not found.", ephemeral=True)
        return

    hostname, os_name, status = row
    embed = discord.Embed(title=f"⚙️ Manage VPS #{number}", color=discord.Color.green())
    embed.add_field(name="Owner", value=f"<@{target_id}>", inline=True)
    embed.add_field(name="OS", value=os_name.capitalize(), inline=True)
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Hostname", value=hostname, inline=False)

    # Buttons
    view = discord.ui.View(timeout=180)
    for action, label, style in [("start","Start",discord.ButtonStyle.green),
                                 ("stop","Stop",discord.ButtonStyle.red),
                                 ("ssh","SSH",discord.ButtonStyle.blurple),
                                 ("reinstall","Reinstall",discord.ButtonStyle.gray)]:
        async def cb(interaction: Interaction, act=action):
            await vps_action_button(interaction, hostname, act)
        view.add_item(discord.ui.Button(label=label, style=style, custom_id=action))
        view.children[-1].callback = cb

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# ---------------- MANAGE SHARED ---------------- #

@tree.command(name="manage-shared", description="🔑 Manage shared VPSes")
@app_commands.describe(number="VPS number", owner="VPS owner")
async def manage_shared(interaction: Interaction, number: int, owner: discord.User):
    db = await get_db()
    async with db.execute("SELECT hostname, os, status FROM vpses WHERE owner_id=? AND number=?", (owner.id, number)) as cursor:
        row = await cursor.fetchone()
    await db.close()

    if not row:
        await interaction.response.send_message("❌ VPS not found.", ephemeral=True)
        return

    hostname, os_name, status = row
    embed = discord.Embed(title=f"🔑 Manage Shared VPS #{number}", color=discord.Color.orange())
    embed.add_field(name="Owner", value=owner.mention, inline=True)
    embed.add_field(name="OS", value=os_name.capitalize(), inline=True)
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Hostname", value=hostname, inline=False)

    # Buttons without reinstall
    view = discord.ui.View(timeout=180)
    for action, label, style in [("start","Start",discord.ButtonStyle.green),
                                 ("stop","Stop",discord.ButtonStyle.red),
                                 ("ssh","SSH",discord.ButtonStyle.blurple)]:
        async def cb(interaction: Interaction, act=action):
            await vps_action_button(interaction, hostname, act)
        view.add_item(discord.ui.Button(label=label, style=style, custom_id=action))
        view.children[-1].callback = cb

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
# ---------------- STATUS COMMAND ---------------- #

@tree.command(name="status", description="📊 View node status (admin only)")
async def status(interaction: Interaction):
    if not await is_admin(interaction.user.id):
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return
    try:
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent
        disk = psutil.disk_usage('/').percent
        embed = discord.Embed(title="📊 Node Status", color=discord.Color.blue())
        embed.add_field(name="CPU Usage", value=f"{cpu}%", inline=True)
        embed.add_field(name="RAM Usage", value=f"{ram}%", inline=True)
        embed.add_field(name="Disk Usage", value=f"{disk}%", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Error retrieving status: {e}", ephemeral=True)

# ---------------- ADMIN ADD ---------------- #

@tree.command(name="admin-add", description="➕ Add a new admin (admin only)")
@app_commands.describe(user="User to grant admin rights")
async def admin_add(interaction: Interaction, user: discord.User):
    if not await is_admin(interaction.user.id):
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return
    db = await get_db()
    await db.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user.id,))
    await db.commit()
    await db.close()
    await interaction.response.send_message(f"✅ {user.mention} is now an admin.", ephemeral=True)

# ---------------- PORT GIVE ---------------- #

@tree.command(name="port-give", description="🔌 Give a port to a VPS (admin only)")
@app_commands.describe(user="VPS owner", vps_number="VPS number", port="Port to assign")
async def port_give(interaction: Interaction, user: discord.User, vps_number: int, port: int):
    if not await is_admin(interaction.user.id):
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return
    db = await get_db()
    await db.execute("UPDATE vpses SET port=? WHERE owner_id=? AND number=?", (port, user.id, vps_number))
    await db.commit()
    await db.close()
    await interaction.response.send_message(f"✅ Port {port} assigned to VPS#{vps_number} of {user.mention}", ephemeral=True)
    await send_log(f"@{interaction.user} assigned port {port} to VPS#{vps_number} of @{user}")

# ---------------- LOG CHANNEL SET ---------------- #

@tree.command(name="set-log-channel", description="📥 Set the public log channel (admin only)")
@app_commands.describe(channel="Channel to send logs")
async def set_log_channel(interaction: Interaction, channel: discord.TextChannel):
    if not await is_admin(interaction.user.id):
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return
    db = await get_db()
    await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ("log_channel", channel.id))
    await db.commit()
    await db.close()
    await interaction.response.send_message(f"✅ Log channel set to {channel.mention}", ephemeral=True)

# ---------------- STARTUP ---------------- #

@bot.event
async def on_ready():
    try:
        await tree.sync(guild=GUILD_ID)
        print(f"Bot ready: {bot.user} (ID: {bot.user.id})")
        await send_log("✅ Bot is Online")
    except Exception as e:
        print(f"Error syncing commands: {e}")

# ---------------- SCHEDULER ---------------- #

scheduler = AsyncIOScheduler()
scheduler.add_job(lambda: print("Scheduler heartbeat"), "interval", seconds=60)
scheduler.start()

# ---------------- RUN ---------------- #

bot.run(TOKEN)
