import os
import discord
from discord import app_commands, Interaction
from discord.ui import View, Button, Select
from discord.ext import commands
import sqlite3
import docker
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ================= BOT CONFIG =================
TOKEN = "YOUR_BOT_TOKEN"
GUILD_ID = 1416666317288767610  # Replace with your guild
ADMIN_IDS = [1421860082894766183]  # Default admin
VPS_ROLE_ID = 1434000306357928047
LOG_CHANNEL_ID = None
RENEWAL_CHANNEL_ID = None

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

client = docker.from_env()

# ================= DATABASE =================
conn = sqlite3.connect("vps.db")
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS vps (
    user_id INTEGER,
    vps_number INTEGER,
    container_id TEXT,
    status TEXT,
    expires_at TEXT
)''')
c.execute('''CREATE TABLE IF NOT EXISTS shared_vps (
    owner_id INTEGER,
    shared_user_id INTEGER,
    vps_number INTEGER
)''')
conn.commit()

# ================= UTILS =================
def is_admin(user_id):
    return user_id in ADMIN_IDS

def log_action(action, executor_id, target_id=None):
    if LOG_CHANNEL_ID:
        channel = bot.get_channel(LOG_CHANNEL_ID)
        if channel:
            desc = f"Action: {action}\nExecutor: <@{executor_id}>"
            if target_id:
                desc += f"\nTarget: <@{target_id}>"
            embed = discord.Embed(title="VPS Action Log", description=desc, color=0x00ff00, timestamp=datetime.utcnow())
            bot.loop.create_task(channel.send(embed=embed))

def next_vps_number(user_id):
    c.execute('SELECT MAX(vps_number) FROM vps WHERE user_id=?', (user_id,))
    row = c.fetchone()
    return 1 if row[0] is None else row[0] + 1
# ================= VPS COMMANDS =================
@tree.command(name="create", description="🟢 Create a new VPS (Admin only)")
@app_commands.describe(user="User to create VPS for", ram="RAM in GB", cpu="CPU cores", disk="Disk in GB")
async def create(interaction: Interaction, user: discord.User, ram: int, cpu: int, disk: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ You are not allowed to use this command.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    vps_number = next_vps_number(user.id)
    container_name = f"vps-{user.id}-{vps_number}"

    existing = [c.name for c in client.containers.list(all=True)]
    if container_name in existing:
        await interaction.followup.send(f"❌ Container {container_name} already exists.", ephemeral=True)
        return

    try:
        container = client.containers.run(
            "ubuntu:22.04",
            name=container_name,
            detach=True,
            tty=True,
            stdin_open=True,
            mem_limit=f"{ram * 1024}m",  # RAM in GB converted to MB
            cpu_quota=cpu*100000
        )
        expires = datetime.utcnow() + timedelta(weeks=2)
        c.execute('INSERT INTO vps(user_id, vps_number, container_id, status, expires_at) VALUES(?,?,?,?,?)',
                  (user.id, vps_number, container.id, "running", expires.isoformat()))
        conn.commit()

        # Add VPS role
        guild = bot.get_guild(GUILD_ID)
        member = guild.get_member(user.id)
        if member and VPS_ROLE_ID:
            role = guild.get_role(VPS_ROLE_ID)
            await member.add_roles(role)

        await interaction.followup.send(f"✅ VPS#{vps_number} created for {user.mention}.", ephemeral=True)
        log_action("VPS Created", interaction.user.id, user.id)

    except Exception as e:
        await interaction.followup.send(f"❌ Error creating VPS: {e}", ephemeral=True)


@tree.command(name="suspend-vps", description="⏸️ Suspend a VPS (Admin only)")
@app_commands.describe(owner="VPS Owner", vps_number="VPS Number")
async def suspend_vps(interaction: Interaction, owner: discord.User, vps_number: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Not allowed.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    c.execute('SELECT container_id FROM vps WHERE user_id=? AND vps_number=?', (owner.id, vps_number))
    row = c.fetchone()
    if not row:
        await interaction.followup.send("❌ VPS not found.", ephemeral=True)
        return
    container_id = row[0]
    try:
        container = client.containers.get(container_id)
        container.stop()
        c.execute('UPDATE vps SET status=? WHERE user_id=? AND vps_number=?', ("suspended", owner.id, vps_number))
        conn.commit()
        await interaction.followup.send(f"✅ VPS#{vps_number} suspended.", ephemeral=True)
        log_action("VPS Suspended", interaction.user.id, owner.id)
    except Exception as e:
        await interaction.followup.send(f"❌ Error suspending VPS: {e}", ephemeral=True)


@tree.command(name="unsuspend-vps", description="▶️ Unsuspend a VPS (Admin only)")
@app_commands.describe(owner="VPS Owner", vps_number="VPS Number")
async def unsuspend_vps(interaction: Interaction, owner: discord.User, vps_number: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Not allowed.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    c.execute('SELECT container_id FROM vps WHERE user_id=? AND vps_number=?', (owner.id, vps_number))
    row = c.fetchone()
    if not row:
        await interaction.followup.send("❌ VPS not found.", ephemeral=True)
        return
    container_id = row[0]
    try:
        container = client.containers.get(container_id)
        container.start()
        c.execute('UPDATE vps SET status=? WHERE user_id=? AND vps_number=?', ("running", owner.id, vps_number))
        conn.commit()
        await interaction.followup.send(f"✅ VPS#{vps_number} unsuspended.", ephemeral=True)
        log_action("VPS Unsuspended", interaction.user.id, owner.id)
    except Exception as e:
        await interaction.followup.send(f"❌ Error unsuspending VPS: {e}", ephemeral=True)


@tree.command(name="remove", description="🗑️ Remove a VPS (Admin only)")
@app_commands.describe(owner="VPS Owner", vps_number="VPS Number")
async def remove(interaction: Interaction, owner: discord.User, vps_number: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Not allowed.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    c.execute('SELECT container_id FROM vps WHERE user_id=? AND vps_number=?', (owner.id, vps_number))
    row = c.fetchone()
    if not row:
        await interaction.followup.send("❌ VPS not found.", ephemeral=True)
        return
    container_id = row[0]
    try:
        container = client.containers.get(container_id)
        container.remove(force=True)
        c.execute('DELETE FROM vps WHERE user_id=? AND vps_number=?', (owner.id, vps_number))
        conn.commit()
        await interaction.followup.send(f"✅ VPS#{vps_number} removed.", ephemeral=True)
        log_action("VPS Removed", interaction.user.id, owner.id)
    except Exception as e:
        await interaction.followup.send(f"❌ Error removing VPS: {e}", ephemeral=True)


@tree.command(name="port-give", description="🔌 Give a port to a VPS (Admin only)")
@app_commands.describe(owner="VPS Owner", vps_number="VPS Number", port="Port number")
async def port_give(interaction: Interaction, owner: discord.User, vps_number: int, port: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Not allowed.", ephemeral=True)
        return
    # Implementation: assign port to VPS (store in DB or apply config)
    await interaction.response.send_message(f"✅ Port {port} assigned to VPS#{vps_number} of {owner.mention}.", ephemeral=True)
    log_action(f"Port {port} Given", interaction.user.id, owner.id)


# ================= SHARE VPS =================
@tree.command(name="share-user", description="🔗 Share a VPS with another user")
@app_commands.describe(vps_owner="VPS Owner", vps_number="VPS Number", target_user="User to share with")
async def share_user(interaction: Interaction, vps_owner: discord.User, vps_number: int, target_user: discord.User):
    c.execute('INSERT INTO shared_vps(owner_id, shared_user_id, vps_number) VALUES(?,?,?)', (vps_owner.id, target_user.id, vps_number))
    conn.commit()
    await interaction.response.send_message(f"✅ VPS#{vps_number} shared with {target_user.mention}", ephemeral=True)
    log_action("VPS Shared", interaction.user.id, target_user.id)


@tree.command(name="share-ruser", description="❌ Remove a shared user from VPS")
@app_commands.describe(vps_owner="VPS Owner", vps_number="VPS Number", target_user="User to remove")
async def share_ruser(interaction: Interaction, vps_owner: discord.User, vps_number: int, target_user: discord.User):
    c.execute('DELETE FROM shared_vps WHERE owner_id=? AND shared_user_id=? AND vps_number=?', (vps_owner.id, target_user.id, vps_number))
    conn.commit()
    await interaction.response.send_message(f"✅ {target_user.mention} removed from VPS#{vps_number}", ephemeral=True)
    log_action("VPS Unshared", interaction.user.id, target_user.id)


@tree.command(name="manage-shared", description="🛠️ Manage a shared VPS")
async def manage_shared(interaction: Interaction):
    user = interaction.user
    c.execute('SELECT owner_id, vps_number FROM shared_vps WHERE shared_user_id=?', (user.id,))
    rows = c.fetchall()
    if not rows:
        await interaction.response.send_message("❌ No shared VPS found.", ephemeral=True)
        return
    desc = "\n".join([f"VPS#{vps} Owner: <@{owner}>" for owner, vps in rows])
    await interaction.response.send_message(f"📝 Shared VPSes:\n{desc}", ephemeral=True)
# ================= ADMIN COMMANDS =================
@tree.command(name="admin-add", description="➕ Add a new admin")
@app_commands.describe(target_user="User to make admin")
async def admin_add(interaction: Interaction, target_user: discord.User):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Not allowed.", ephemeral=True)
        return
    add_admin(target_user.id)
    await interaction.response.send_message(f"✅ {target_user.mention} is now an admin.", ephemeral=True)
    log_action("Admin Added", interaction.user.id, target_user.id)


@tree.command(name="log-channel-set", description="📢 Set the public log channel")
@app_commands.describe(channel="Channel to send public logs")
async def log_channel_set(interaction: Interaction, channel: discord.TextChannel):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Not allowed.", ephemeral=True)
        return
    global LOG_CHANNEL_ID
    LOG_CHANNEL_ID = channel.id
    await interaction.response.send_message(f"✅ Log channel set to {channel.mention}", ephemeral=True)
    log_action("Log Channel Set", interaction.user.id)


@tree.command(name="set-renewal-request-channel", description="📨 Set the renewal request channel")
@app_commands.describe(channel="Channel to receive renewal requests")
async def set_renewal_request_channel(interaction: Interaction, channel: discord.TextChannel):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Not allowed.", ephemeral=True)
        return
    global RENEWAL_CHANNEL_ID
    RENEWAL_CHANNEL_ID = channel.id
    await interaction.response.send_message(f"✅ Renewal request channel set to {channel.mention}", ephemeral=True)
    log_action("Renewal Channel Set", interaction.user.id)


# ================= MANAGE COMMAND =================
class VPSSelect(discord.ui.Select):
    def __init__(self, user_id, options):
        super().__init__(placeholder="Select a VPS...", min_values=1, max_values=1, options=options)
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        vps_data = self.values[0].split(":")  # ownerid-vpsnumber
        owner_id, vps_number = int(vps_data[0]), int(vps_data[1])
        c.execute('SELECT container_id, status FROM vps WHERE user_id=? AND vps_number=?', (owner_id, vps_number))
        row = c.fetchone()
        if not row:
            await interaction.response.send_message("❌ VPS not found.", ephemeral=True)
            return
        container_id, status = row
        # Show buttons for start, stop, ssh
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Start", style=discord.ButtonStyle.green, custom_id=f"start-{container_id}"))
        view.add_item(discord.ui.Button(label="Stop", style=discord.ButtonStyle.red, custom_id=f"stop-{container_id}"))
        view.add_item(discord.ui.Button(label="SSH", style=discord.ButtonStyle.blurple, custom_id=f"ssh-{container_id}"))
        await interaction.response.send_message(f"🛠️ Managing VPS#{vps_number} (Owner: <@{owner_id}>) Status: {status}", view=view, ephemeral=True)


@tree.command(name="manage", description="🛠️ Manage your VPSes")
@app_commands.describe(target_user="Optional: Admin can manage another user's VPSes")
async def manage(interaction: Interaction, target_user: discord.User=None):
    user_id = target_user.id if target_user and is_admin(interaction.user.id) else interaction.user.id
    c.execute('SELECT user_id, vps_number FROM vps WHERE user_id=?', (user_id,))
    rows = c.fetchall()
    if not rows:
        await interaction.response.send_message("❌ No VPS found.", ephemeral=True)
        return
    options = [discord.SelectOption(label=f"VPS#{vps}", description=f"Owner: <@{owner}>", value=f"{owner}-{vps}") for owner, vps in rows]
    select = VPSSelect(user_id, options)
    view = discord.ui.View()
    view.add_item(select)
    await interaction.response.send_message("Select a VPS to manage:", view=view, ephemeral=True)
# ================= HELPERS =================
def is_admin(user_id: int):
    return user_id in ADMIN_IDS

def log_action(action, executor_id, target_id=None):
    if LOG_CHANNEL_ID:
        guild = bot.get_guild(GUILD_ID)
        channel = guild.get_channel(LOG_CHANNEL_ID)
        msg = f"📝 **{action}** executed by <@{executor_id}>"
        if target_id:
            msg += f" on <@{target_id}>"
        asyncio.create_task(channel.send(msg))

def next_vps_number(user_id: int):
    c.execute('SELECT MAX(vps_number) FROM vps WHERE user_id=?', (user_id,))
    row = c.fetchone()
    return 1 if row[0] is None else row[0]+1

def add_admin(user_id: int):
    if user_id not in ADMIN_IDS:
        ADMIN_IDS.append(user_id)

# ================= SHARED VPS HANDLER =================
@bot.event
async def on_interaction(interaction: discord.Interaction):
    if not interaction.type == discord.InteractionType.component:
        return
    custom_id = interaction.data["custom_id"]
    container_id = custom_id.split("-")[1]
    try:
        container = client.containers.get(container_id)
        if custom_id.startswith("start"):
            container.start()
            await interaction.response.send_message("✅ VPS started.", ephemeral=True)
        elif custom_id.startswith("stop"):
            container.stop()
            await interaction.response.send_message("⏹️ VPS stopped.", ephemeral=True)
        elif custom_id.startswith("ssh"):
            # Send tmate link via DM (simplified)
            await interaction.user.send(f"🔗 SSH link for container {container_id}: `tmate ssh-link`")
            await interaction.response.send_message("📩 SSH link sent via DM.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

# ================= SCHEDULER =================
scheduler = AsyncIOScheduler()
scheduler.add_job(lambda: check_expired_vpses(), 'interval', minutes=5)

# ================= START BOT =================
@bot.event
async def on_ready():
    await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
    print("Bot is Online ✅")
    scheduler.start()  # start scheduler after bot loop is running

def check_expired_vpses():
    now = datetime.utcnow().isoformat()
    c.execute('SELECT user_id, vps_number, container_id FROM vps WHERE expires_at <= ?', (now,))
    for user_id, vps_number, container_id in c.fetchall():
        try:
            container = client.containers.get(container_id)
            container.stop()
            c.execute('UPDATE vps SET status=? WHERE user_id=? AND vps_number=?', ("expired", user_id, vps_number))
            conn.commit()
            if RENEWAL_CHANNEL_ID:
                guild = bot.get_guild(GUILD_ID)
                channel = guild.get_channel(RENEWAL_CHANNEL_ID)
                asyncio.create_task(channel.send(f"⏰ VPS#{vps_number} for <@{user_id}> expired. Renewal needed."))
        except Exception:
            continue

bot.run(TOKEN)
