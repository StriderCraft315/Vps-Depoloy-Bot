import os, discord, docker, sqlite3
from discord import app_commands, Embed, Interaction
from discord.ui import View, Select, Button
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta

# =================== CONFIG ===================
TOKEN = "YOUR_BOT_TOKEN"  # Replace with your token or use input()
GUILD_ID = 0  # Optional: restrict commands to a guild
VPS_USER_ROLE_ID = 1434000306357928047
DEFAULT_ADMIN_ID = 1421860082894766183
LOG_CHANNEL_ID = None  # Set later via command
RENEWAL_CHANNEL_ID = None  # Set later via command

intents = discord.Intents.default()
intents.members = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)
scheduler = AsyncIOScheduler()
client = docker.from_env()

# =================== DATABASE ===================
conn = sqlite3.connect('vps.db')
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS vps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    container_id TEXT,
    os TEXT,
    cpu INTEGER,
    ram INTEGER,
    disk INTEGER,
    expires_at TEXT,
    status TEXT,
    vps_number INTEGER
)''')
c.execute('''CREATE TABLE IF NOT EXISTS vps_shared (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vps_id INTEGER,
    user_id INTEGER
)''')
c.execute('''CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT
)''')
c.execute('''CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY
)''')
conn.commit()

# Add default admin
c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (DEFAULT_ADMIN_ID,))
conn.commit()

# =================== HELPERS ===================
def is_admin(user_id: int) -> bool:
    c.execute('SELECT 1 FROM admins WHERE user_id=?', (user_id,))
    return c.fetchone() is not None

def log_action(action: str, by_id: int, target_id: int):
    if LOG_CHANNEL_ID:
        channel = bot.get_channel(LOG_CHANNEL_ID)
        if channel:
            embed = Embed(title=f"Action: {action}", color=0x00FFFF)
            embed.add_field(name="By", value=f"<@{by_id}>", inline=True)
            embed.add_field(name="Target", value=f"<@{target_id}>", inline=True)
            bot.loop.create_task(channel.send(embed=embed))
# =================== ADMIN COMMANDS ===================

# ---------- /create ----------
@tree.command(name="create", description="🖥️ Create a new VPS for a user (Admin only)")
@app_commands.describe(
    user="👤 User to give VPS",
    os_type="💻 OS type (debian/ubuntu)",
    cpu="⚡ CPU cores",
    ram="💾 RAM in MB",
    disk="📀 Disk in MB"
)
async def create(interaction: Interaction, user: discord.User, os_type: str, cpu: int, ram: int, disk: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ You are not an admin.", ephemeral=True)
        return

    try:
        # Determine next VPS number
        c.execute('SELECT COUNT(*) FROM vps WHERE user_id=?', (user.id,))
        count = c.fetchone()[0]
        vps_number = count + 1

        image = "debian:latest" if os_type.lower() == "debian" else "ubuntu:latest"
        container_name = f"vps-{user.id}-{vps_number}"

        # Create Docker container
        container = client.containers.run(
            image, detach=True, tty=True, name=container_name
        )
        container_id = container.id
        expires_at = (datetime.utcnow() + timedelta(weeks=2)).isoformat()

        c.execute('INSERT INTO vps (user_id, container_id, os, cpu, ram, disk, expires_at, status, vps_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                  (user.id, container_id, os_type, cpu, ram, disk, expires_at, "active", vps_number))
        conn.commit()

        log_action("create_vps", interaction.user.id, user.id)
        await interaction.response.send_message(f"✅ VPS#{vps_number} created for {user.mention}.", ephemeral=True)

        # Assign VPS role if first VPS
        if count == 0:
            role = discord.utils.get(interaction.guild.roles, id=VPS_USER_ROLE_ID)
            if role:
                await user.add_roles(role)

    except Exception as e:
        await interaction.followup.send(f"❌ Error creating VPS: {e}", ephemeral=True)


# ---------- /admin-add ----------
@tree.command(name="admin-add", description="🛡️ Add a new admin")
@app_commands.describe(user="👤 User to make admin")
async def admin_add(interaction: Interaction, user: discord.User):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ You are not an admin.", ephemeral=True)
        return
    c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (user.id,))
    conn.commit()
    await interaction.response.send_message(f"✅ {user.mention} is now an admin.", ephemeral=True)


# ---------- /set-log-channel ----------
@tree.command(name="set-log-channel", description="📜 Set the public log channel for actions")
@app_commands.describe(channel="📌 Channel to log actions")
async def set_log_channel(interaction: Interaction, channel: discord.TextChannel):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ You are not an admin.", ephemeral=True)
        return
    global LOG_CHANNEL_ID
    LOG_CHANNEL_ID = channel.id
    c.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', ("log_channel", str(channel.id)))
    conn.commit()
    await interaction.response.send_message(f"✅ Log channel set to {channel.mention}", ephemeral=True)
# =================== MORE ADMIN COMMANDS ===================

# ---------- /suspend-vps ----------
@tree.command(name="suspend-vps", description="⏸️ Suspend a VPS (Admin only)")
@app_commands.describe(user="👤 Owner of the VPS", vps_number="🔢 VPS number")
async def suspend_vps(interaction: Interaction, user: discord.User, vps_number: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Not allowed.", ephemeral=True)
        return
    c.execute('SELECT id, container_id FROM vps WHERE user_id=? AND vps_number=?', (user.id, vps_number))
    row = c.fetchone()
    if not row:
        await interaction.response.send_message("❌ VPS not found.", ephemeral=True)
        return
    vps_id, container_id = row
    try:
        container = client.containers.get(container_id)
        container.stop()
        c.execute('UPDATE vps SET status="suspended" WHERE id=?', (vps_id,))
        conn.commit()
        log_action("suspend_vps", interaction.user.id, user.id)
        await interaction.response.send_message(f"✅ VPS#{vps_number} suspended.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error suspending VPS: {e}", ephemeral=True)

# ---------- /unsuspend-vps ----------
@tree.command(name="unsuspend-vps", description="▶️ Unsuspend a VPS (Admin only)")
@app_commands.describe(user="👤 Owner of the VPS", vps_number="🔢 VPS number")
async def unsuspend_vps(interaction: Interaction, user: discord.User, vps_number: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Not allowed.", ephemeral=True)
        return
    c.execute('SELECT id, container_id FROM vps WHERE user_id=? AND vps_number=?', (user.id, vps_number))
    row = c.fetchone()
    if not row:
        await interaction.response.send_message("❌ VPS not found.", ephemeral=True)
        return
    vps_id, container_id = row
    try:
        container = client.containers.get(container_id)
        container.start()
        c.execute('UPDATE vps SET status="active" WHERE id=?', (vps_id,))
        conn.commit()
        log_action("unsuspend_vps", interaction.user.id, user.id)
        await interaction.response.send_message(f"✅ VPS#{vps_number} unsuspended.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error unsuspending VPS: {e}", ephemeral=True)

# ---------- /remove ----------
@tree.command(name="remove", description="🗑️ Remove a VPS (Admin only)")
@app_commands.describe(user="👤 Owner of the VPS", vps_number="🔢 VPS number")
async def remove(interaction: Interaction, user: discord.User, vps_number: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Not allowed.", ephemeral=True)
        return
    c.execute('SELECT id, container_id FROM vps WHERE user_id=? AND vps_number=?', (user.id, vps_number))
    row = c.fetchone()
    if not row:
        await interaction.response.send_message("❌ VPS not found.", ephemeral=True)
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
        await interaction.response.send_message(f"✅ VPS#{vps_number} removed.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error removing VPS: {e}", ephemeral=True)

# ---------- /port-give ----------
@tree.command(name="port-give", description="🔌 Assign a port to a user VPS (Admin only)")
@app_commands.describe(user="👤 Owner of the VPS", vps_number="🔢 VPS number", port="📡 Port number to assign")
async def port_give(interaction: Interaction, user: discord.User, vps_number: int, port: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Not allowed.", ephemeral=True)
        return
    # Save port in DB (add column if needed)
    c.execute('ALTER TABLE vps ADD COLUMN port INTEGER')  # Will fail if exists, ignored
    try:
        c.execute('UPDATE vps SET port=? WHERE user_id=? AND vps_number=?', (port, user.id, vps_number))
        conn.commit()
        log_action("port_give", interaction.user.id, user.id)
        await interaction.response.send_message(f"✅ Port {port} assigned to VPS#{vps_number}.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error assigning port: {e}", ephemeral=True)
# =================== USER COMMANDS ===================

# ---------- /manage ----------
@tree.command(name="manage", description="🛠️ Manage your VPSes")
@app_commands.describe(user="👤 Optional: manage another user (Admin only)")
async def manage(interaction: Interaction, user: discord.User = None):
    target = user or interaction.user
    if user and not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ You cannot manage other users' VPSes.", ephemeral=True)
        return

    c.execute('SELECT vps_number, status, container_id FROM vps WHERE user_id=?', (target.id,))
    rows = c.fetchall()
    if not rows:
        await interaction.response.send_message("❌ No VPSes found.", ephemeral=True)
        return

    options = [discord.SelectOption(label=f"VPS#{r[0]}", description=f"Status: {r[1]}", value=str(r[0])) for r in rows]

    select = Select(placeholder="Select a VPS", options=options)

    async def select_callback(select_interaction):
        vnum = int(select.values[0])
        c.execute('SELECT container_id, status FROM vps WHERE user_id=? AND vps_number=?', (target.id, vnum))
        container_id, status = c.fetchone()

        view = View()
        # Buttons for Start/Stop/Restart/SSH
        async def start_callback(btn):
            container = client.containers.get(container_id)
            container.start()
            c.execute('UPDATE vps SET status="active" WHERE user_id=? AND vps_number=?', (target.id, vnum))
            conn.commit()
            log_action("start_vps", interaction.user.id, target.id)
            await btn.response.send_message(f"✅ VPS#{vnum} started.", ephemeral=True)

        async def stop_callback(btn):
            container = client.containers.get(container_id)
            container.stop()
            c.execute('UPDATE vps SET status="stopped" WHERE user_id=? AND vps_number=?', (target.id, vnum))
            conn.commit()
            log_action("stop_vps", interaction.user.id, target.id)
            await btn.response.send_message(f"⏹️ VPS#{vnum} stopped.", ephemeral=True)

        async def restart_callback(btn):
            container = client.containers.get(container_id)
            container.restart()
            c.execute('UPDATE vps SET status="active" WHERE user_id=? AND vps_number=?', (target.id, vnum))
            conn.commit()
            log_action("restart_vps", interaction.user.id, target.id)
            await btn.response.send_message(f"🔄 VPS#{vnum} restarted.", ephemeral=True)

        async def ssh_callback(btn):
            # Run tmate inside container and DM the user
            container = client.containers.get(container_id)
            exec_id = container.exec_run("tmate -S /tmp/tmate.sock new-session -d")
            exec_id2 = container.exec_run("tmate -S /tmp/tmate.sock display -p '#{tmate_ssh}'")
            ssh_link = exec_id2.output.decode().strip()
            try:
                await target.send(f"🔗 SSH link for VPS#{vnum}: `{ssh_link}`")
            except:
                await btn.response.send_message(f"❌ Cannot DM {target.mention}.", ephemeral=True)
            await btn.response.send_message(f"📩 SSH link sent via DM.", ephemeral=True)
            log_action("tmate_ssh", interaction.user.id, target.id)

        view.add_item(Button(label="Start", style=discord.ButtonStyle.green, callback=start_callback))
        view.add_item(Button(label="Stop", style=discord.ButtonStyle.red, callback=stop_callback))
        view.add_item(Button(label="Restart", style=discord.ButtonStyle.blurple, callback=restart_callback))
        view.add_item(Button(label="SSH", style=discord.ButtonStyle.gray, callback=ssh_callback))

        await select_interaction.response.send_message(f"Manage VPS#{vnum}", view=view, ephemeral=True)

    select.callback = select_callback
    view = View()
    view.add_item(select)
    await interaction.response.send_message("Select a VPS to manage:", view=view, ephemeral=True)


# ---------- /share-user ----------
@tree.command(name="share-user", description="🔗 Share your VPS with another user")
@app_commands.describe(user="👤 User to share with", vps_number="🔢 Your VPS number")
async def share_user(interaction: Interaction, user: discord.User, vps_number: int):
    c.execute('SELECT id FROM vps WHERE user_id=? AND vps_number=?', (interaction.user.id, vps_number))
    row = c.fetchone()
    if not row:
        await interaction.response.send_message("❌ VPS not found.", ephemeral=True)
        return
    vps_id = row[0]
    c.execute('INSERT OR IGNORE INTO vps_shared (vps_id, user_id) VALUES (?, ?)', (vps_id, user.id))
    conn.commit()
    log_action("share_vps", interaction.user.id, user.id)
    await interaction.response.send_message(f"✅ VPS#{vps_number} shared with {user.mention}.", ephemeral=True)


# ---------- /share-ruser ----------
@tree.command(name="share-ruser", description="❌ Remove a shared user from your VPS")
@app_commands.describe(user="👤 User to remove", vps_number="🔢 Your VPS number")
async def share_ruser(interaction: Interaction, user: discord.User, vps_number: int):
    c.execute('SELECT id FROM vps WHERE user_id=? AND vps_number=?', (interaction.user.id, vps_number))
    row = c.fetchone()
    if not row:
        await interaction.response.send_message("❌ VPS not found.", ephemeral=True)
        return
    vps_id = row[0]
    c.execute('DELETE FROM vps_shared WHERE vps_id=? AND user_id=?', (vps_id, user.id))
    conn.commit()
    log_action("unshare_vps", interaction.user.id, user.id)
    await interaction.response.send_message(f"✅ User {user.mention} removed from VPS#{vps_number}.", ephemeral=True)
# =================== STARTUP & SCHEDULER ===================

@bot.event
async def on_ready():
    # Clear old commands
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        await tree.sync(guild=guild)
    else:
        await tree.sync()
    
    print("✅ Bot is Online")
    
    # Load config from DB
    global LOG_CHANNEL_ID, RENEWAL_CHANNEL_ID
    c.execute("SELECT value FROM config WHERE key='log_channel'")
    row = c.fetchone()
    if row:
        LOG_CHANNEL_ID = int(row[0])
    c.execute("SELECT value FROM config WHERE key='renewal_channel'")
    row = c.fetchone()
    if row:
        RENEWAL_CHANNEL_ID = int(row[0])

    # Start scheduler
    scheduler.start()
    scheduler.add_job(auto_suspend_vps, 'interval', minutes=10)


# ---------- Auto-Suspend Job ----------
async def auto_suspend_vps():
    now = datetime.utcnow().isoformat()
    c.execute('SELECT id, user_id, vps_number, container_id, status FROM vps WHERE expires_at <= ? AND status="active"', (now,))
    rows = c.fetchall()
    for vps_id, user_id, vnum, container_id, status in rows:
        try:
            container = client.containers.get(container_id)
            container.stop()
            c.execute('UPDATE vps SET status="suspended" WHERE id=?', (vps_id,))
            conn.commit()
            log_action("auto_suspend", 0, user_id)  # 0 = system
            if RENEWAL_CHANNEL_ID:
                channel = bot.get_channel(RENEWAL_CHANNEL_ID)
                if channel:
                    await channel.send(f"⚠️ VPS#{vnum} for <@{user_id}> has been suspended. Renewal needed.")
        except Exception as e:
            print(f"Error auto-suspending VPS#{vnum}: {e}")


# ---------- /port-give (re-added with proper setup) ----------
@tree.command(name="port-give", description="🔌 Assign a port to a user VPS (Admin only)")
@app_commands.describe(user="👤 Owner of the VPS", vps_number="🔢 VPS number", port="📡 Port number")
async def port_give(interaction: Interaction, user: discord.User, vps_number: int, port: int):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Not allowed.", ephemeral=True)
        return
    # Ensure column exists
    try:
        c.execute('ALTER TABLE vps ADD COLUMN port INTEGER')
    except:
        pass
    try:
        c.execute('UPDATE vps SET port=? WHERE user_id=? AND vps_number=?', (port, user.id, vps_number))
        conn.commit()
        log_action("port_give", interaction.user.id, user.id)
        await interaction.response.send_message(f"✅ Port {port} assigned to VPS#{vps_number}.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error assigning port: {e}", ephemeral=True)


# =================== RUN BOT ===================
bot.run(TOKEN)
