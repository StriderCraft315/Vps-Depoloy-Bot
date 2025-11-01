#!/usr/bin/env python3
# Patched VPS Deploy Bot - single-file bot.py
# Features: fixed interactions, scheduler in on_ready, create/manage/suspend/unsuspend/remove,
# manage uses owner+vps number, RAM/Disk in GB, Docker handling with timestamp names,
# tmate attempt, SQLite persistence, command sync/clear to avoid signature mismatches.

import os
import json
import sqlite3
import time
import traceback
import asyncio
from datetime import datetime, timedelta

import discord
from discord import app_commands, Interaction
from discord.ext import commands
from discord.ui import View, Select, Button

import docker
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ---------------- CONFIG LOADING ----------------
CONFIG_FILE = "config.json"
# You can either supply config.json or input on first run.
cfg = {}
if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

TOKEN = cfg.get("token") or os.getenv("BOT_TOKEN")
GUILD_ID = cfg.get("guild_id") or (int(os.getenv("GUILD_ID")) if os.getenv("GUILD_ID") else None)

# If interactive (manual run), prompt for missing items
if not TOKEN:
    TOKEN = input("Enter Discord bot token: ").strip()
if not GUILD_ID:
    gid = input("Enter Guild ID (leave blank to use global commands - not recommended): ").strip()
    GUILD_ID = int(gid) if gid else None

# ---------------- BOT / DOCKER / DB SETUP ----------------
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Docker client (must be available on host)
client = docker.from_env()

# SQLite DB
DB_FILE = "vps.db"
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
c = conn.cursor()
c.execute('''
CREATE TABLE IF NOT EXISTS vps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    vps_number INTEGER,
    container_id TEXT,
    status TEXT,
    expires_at TEXT,
    ram_gb INTEGER,
    disk_gb INTEGER,
    cpu REAL,
    port INTEGER
)
''')
c.execute('''
CREATE TABLE IF NOT EXISTS shared_vps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER,
    shared_user_id INTEGER,
    vps_number INTEGER
)
''')
c.execute('''
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT
)
''')
conn.commit()

# Globals
ADMIN_IDS = cfg.get("admins", [1421860082894766183])  # default; can be modified with /admin-add
VPS_ROLE_ID = cfg.get("vps_role_id") or 1434000306357928047
LOG_CHANNEL_ID = None
RENEWAL_CHANNEL_ID = None

# Load saved config keys if present
try:
    c.execute("SELECT value FROM config WHERE key='log_channel'")
    row = c.fetchone()
    if row:
        LOG_CHANNEL_ID = int(row[0])
    c.execute("SELECT value FROM config WHERE key='renewal_channel'")
    row = c.fetchone()
    if row:
        RENEWAL_CHANNEL_ID = int(row[0])
except Exception:
    pass

# Scheduler (created, started in on_ready)
scheduler = AsyncIOScheduler()

# ---------------- HELPERS ----------------
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def save_config_kv(key: str, value: str):
    c.execute('INSERT OR REPLACE INTO config(key,value) VALUES (?,?)', (key, str(value)))
    conn.commit()

def send_console(*args, **kwargs):
    print(*args, **kwargs)

async def send_reply(interaction: Interaction, content: str, ephemeral: bool = True):
    """
    Send a reply to an interaction safely:
    - If not yet acknowledged, use response.send_message
    - If already deferred/acknowledged, use followup.send
    """
    try:
        # If interaction.response.is_done is available, use it; else fallback to try/except
        if hasattr(interaction.response, "is_done") and interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=ephemeral)
            return
        # Try direct send (if not acknowledged)
        await interaction.response.send_message(content, ephemeral=ephemeral)
    except Exception:
        # Already acknowledged or other error -> try followup
        try:
            await interaction.followup.send(content, ephemeral=ephemeral)
        except Exception:
            # Last resort: DM the user if possible
            try:
                await interaction.user.send(content)
            except Exception:
                send_console("Failed to send interaction reply:", content)

def next_vps_number(user_id: int) -> int:
    c.execute('SELECT MAX(vps_number) FROM vps WHERE user_id=?', (user_id,))
    row = c.fetchone()
    return 1 if (not row or row[0] is None) else (row[0] + 1)

def format_vps_row(row):
    # row: id,user_id,vps_number,container_id,status,expires_at,ram_gb,disk_gb,cpu,port
    return {
        "id": row[0],
        "user_id": row[1],
        "vps_number": row[2],
        "container_id": row[3],
        "status": row[4],
        "expires_at": row[5],
        "ram_gb": row[6],
        "disk_gb": row[7],
        "cpu": row[8],
        "port": row[9]
    }

# ---------------- CORE COMMANDS ----------------

@tree.command(name="admin-add", description="👑 Add an admin (admin only)")
@app_commands.describe(user="User to make admin")
async def admin_add(interaction: Interaction, user: discord.User):
    # signature must match Discord
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    if user.id not in ADMIN_IDS:
        ADMIN_IDS.append(user.id)
    await interaction.response.send_message(f"✅ {user.mention} added as admin.", ephemeral=True)

@tree.command(name="create", description="🟢 Create a VPS for a user (admin only). RAM & Disk in GB")
@app_commands.describe(user="User to assign VPS", os_type="ubuntu or debian", ram="RAM (GB)", cpu="CPU cores (float)", disk="Disk (GB)")
async def create(interaction: Interaction, user: discord.User, os_type: str, ram: int, cpu: float, disk: int):
    if not is_admin(interaction.user.id):
        await send_reply(interaction, "❌ Admin only.", ephemeral=True)
        return

    # Defer early to avoid timeout
    try:
        await interaction.response.defer(ephemeral=True)
        deferred = True
    except Exception:
        deferred = False

    # Validate inputs
    if ram <= 0 or disk <= 0 or cpu <= 0:
        await send_reply(interaction, "❌ Invalid resource sizes; must be > 0.", ephemeral=True)
        return

    vps_number = next_vps_number(user.id)
    ts = int(time.time())
    container_name = f"vps-{user.id}-{vps_number}-{ts}"

    image = "ubuntu:22.04" if os_type.lower().startswith("u") else "debian:12"
    mem_limit_mb = ram * 1024

    try:
        # Create container (timestamped name avoids name conflicts)
        container = client.containers.run(
            image,
            name=container_name,
            detach=True,
            tty=True,
            stdin_open=True,
            mem_limit=f"{mem_limit_mb}m",
            cpu_quota=int(cpu * 100000),
            command="/bin/bash"
        )
    except docker.errors.APIError as e:
        err = getattr(e, "explanation", str(e))
        await send_reply(interaction, f"❌ Docker API error creating container: {err}", ephemeral=True)
        return
    except Exception as e:
        await send_reply(interaction, f"❌ Error creating container: {e}", ephemeral=True)
        return

    # Try to install essentials, but don't block if it fails (best-effort)
    try:
        container.exec_run("apt-get update && apt-get install -y sudo systemd neofetch tmate", user="root", demux=True)
    except Exception:
        pass

    expires_at = (datetime.utcnow() + timedelta(days=14)).isoformat()
    c.execute('INSERT INTO vps(user_id, vps_number, container_id, status, expires_at, ram_gb, disk_gb, cpu) VALUES (?,?,?,?,?,?,?,?)',
              (user.id, vps_number, container.id, "running", expires_at, ram, disk, cpu))
    conn.commit()

    # Give role if configured
    try:
        if VPS_ROLE_ID and GUILD_ID:
            guild = bot.get_guild(GUILD_ID)
            if guild:
                member = guild.get_member(user.id)
                role = guild.get_role(VPS_ROLE_ID)
                if member and role and role not in member.roles:
                    await member.add_roles(role)
    except Exception:
        pass

    await send_reply(interaction, f"✅ VPS#{vps_number} created for {user.mention}. Container `{container.name}`", ephemeral=True)
    log_action = lambda a,b,t=None: send_console(f"[LOG] {a} by {b} target {t}")
    log_public = lambda text: send_console("[PUBLIC LOG]", text)
    # Public log message
    log_public(f"VPS#{vps_number} created for {user.id} by {interaction.user.id}")

@tree.command(name="suspend-vps", description="⛔ Suspend a VPS (admin only)")
@app_commands.describe(owner="VPS owner", vps_number="VPS number")
async def suspend_vps(interaction: Interaction, owner: discord.User, vps_number: int):
    if not is_admin(interaction.user.id):
        await send_reply(interaction, "❌ Admin only.", ephemeral=True); return
    try:
        await interaction.response.defer(ephemeral=True)
    except Exception:
        pass

    c.execute('SELECT container_id,status FROM vps WHERE user_id=? AND vps_number=?', (owner.id, vps_number))
    row = c.fetchone()
    if not row:
        await send_reply(interaction, "❌ VPS not found.", ephemeral=True); return
    container_id, status = row
    try:
        cont = client.containers.get(container_id)
        cont.stop()
    except docker.errors.NotFound:
        pass
    c.execute('UPDATE vps SET status=? WHERE user_id=? AND vps_number=?', ("suspended", owner.id, vps_number))
    conn.commit()
    await send_reply(interaction, f"✅ VPS#{vps_number} suspended.", ephemeral=True)
    log_public = lambda text: send_console("[PUBLIC LOG]", text)
    log_public(f"VPS#{vps_number} suspended for {owner.id} by {interaction.user.id}")

@tree.command(name="unsuspend-vps", description="▶ Unsuspend a VPS (admin only)")
@app_commands.describe(owner="VPS owner", vps_number="VPS number")
async def unsuspend_vps(interaction: Interaction, owner: discord.User, vps_number: int):
    if not is_admin(interaction.user.id):
        await send_reply(interaction, "❌ Admin only.", ephemeral=True); return
    try:
        await interaction.response.defer(ephemeral=True)
    except Exception:
        pass

    c.execute('SELECT container_id,status FROM vps WHERE user_id=? AND vps_number=?', (owner.id, vps_number))
    row = c.fetchone()
    if not row:
        await send_reply(interaction, "❌ VPS not found.", ephemeral=True); return
    container_id, status = row
    try:
        cont = client.containers.get(container_id)
        cont.start()
    except docker.errors.NotFound:
        await send_reply(interaction, "❌ Container not found.", ephemeral=True); return
    c.execute('UPDATE vps SET status=? WHERE user_id=? AND vps_number=?', ("running", owner.id, vps_number))
    conn.commit()
    await send_reply(interaction, f"✅ VPS#{vps_number} unsuspended.", ephemeral=True)
    send_console(f"unsuspend: owner={owner.id} by {interaction.user.id}")

@tree.command(name="remove", description="🗑 Remove a VPS (admin only)")
@app_commands.describe(owner="VPS owner", vps_number="VPS number")
async def remove(interaction: Interaction, owner: discord.User, vps_number: int):
    if not is_admin(interaction.user.id):
        await send_reply(interaction, "❌ Admin only.", ephemeral=True); return
    try:
        await interaction.response.defer(ephemeral=True)
    except Exception:
        pass

    c.execute('SELECT container_id FROM vps WHERE user_id=? AND vps_number=?', (owner.id, vps_number))
    row = c.fetchone()
    if not row:
        await send_reply(interaction, "❌ VPS not found.", ephemeral=True); return
    container_id = row[0]
    try:
        cont = client.containers.get(container_id)
        cont.remove(force=True)
    except docker.errors.NotFound:
        pass
    c.execute('DELETE FROM vps WHERE user_id=? AND vps_number=?', (owner.id, vps_number))
    c.execute('DELETE FROM shared_vps WHERE owner_id=? AND vps_number=?', (owner.id, vps_number))
    conn.commit()
    await send_reply(interaction, f"✅ VPS#{vps_number} removed.", ephemeral=True)
    send_console(f"remove: owner={owner.id} by {interaction.user.id}")

@tree.command(name="port-give", description="🔌 Assign a port to a VPS (admin only)")
@app_commands.describe(owner="VPS owner", vps_number="VPS number", port="Port number")
async def port_give(interaction: Interaction, owner: discord.User, vps_number: int, port: int):
    if not is_admin(interaction.user.id):
        await send_reply(interaction, "❌ Admin only.", ephemeral=True); return
    try:
        await interaction.response.defer(ephemeral=True)
    except Exception:
        pass
    c.execute('UPDATE vps SET port=? WHERE user_id=? AND vps_number=?', (port, owner.id, vps_number))
    conn.commit()
    await send_reply(interaction, f"✅ Port {port} assigned to VPS#{vps_number} ({owner.mention}).", ephemeral=True)
    send_console(f"port_give: owner={owner.id} port={port} by {interaction.user.id}")

@tree.command(name="share-user", description="🔗 Share an owner VPS with another user")
@app_commands.describe(owner="VPS owner", vps_number="VPS number", target="User to share with")
async def share_user(interaction: Interaction, owner: discord.User, vps_number: int, target: discord.User):
    try:
        await interaction.response.defer(ephemeral=True)
    except Exception:
        pass
    c.execute('INSERT INTO shared_vps(owner_id, shared_user_id, vps_number) VALUES (?,?,?)', (owner.id, target.id, vps_number))
    conn.commit()
    await send_reply(interaction, f"✅ VPS#{vps_number} shared with {target.mention}", ephemeral=True)
    send_console(f"share-user: owner={owner.id} target={target.id} vps={vps_number} by {interaction.user.id}")

@tree.command(name="share-ruser", description="❌ Unshare an owner VPS from a user")
@app_commands.describe(owner="VPS owner", vps_number="VPS number", target="User to remove")
async def share_ruser(interaction: Interaction, owner: discord.User, vps_number: int, target: discord.User):
    try:
        await interaction.response.defer(ephemeral=True)
    except Exception:
        pass
    c.execute('DELETE FROM shared_vps WHERE owner_id=? AND shared_user_id=? AND vps_number=?', (owner.id, target.id, vps_number))
    conn.commit()
    await send_reply(interaction, f"✅ Removed {target.mention} from VPS#{vps_number}", ephemeral=True)
    send_console(f"share-ruser: owner={owner.id} target={target.id} vps={vps_number}")

@tree.command(name="manage-shared", description="🛠 Manage VPSes shared with you")
async def manage_shared(interaction: Interaction):
    try:
        await interaction.response.defer(ephemeral=True)
    except Exception:
        pass
    uid = interaction.user.id
    c.execute('SELECT owner_id, vps_number FROM shared_vps WHERE shared_user_id=?', (uid,))
    rows = c.fetchall()
    if not rows:
        await send_reply(interaction, "❌ No shared VPSes found.", ephemeral=True); return
    lines = [f"VPS#{vps} — owner <@{owner}>" for owner, vps in rows]
    await send_reply(interaction, "Shared VPSes:\n" + "\n".join(lines), ephemeral=True)

# ---------------- Manage command with dropdown and action buttons ----------------
class ManageSelect(Select):
    def __init__(self, options):
        super().__init__(placeholder="Select a VPS to manage", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: Interaction):
        # value = "owner-vps"
        val = self.values[0]
        try:
            owner_str, vps_str = val.split("-", 1)
            owner_id = int(owner_str); vps_number = int(vps_str)
        except Exception:
            await send_reply(interaction, "❌ Invalid selection.", ephemeral=True)
            return

        c.execute('SELECT container_id, status FROM vps WHERE user_id=? AND vps_number=?', (owner_id, vps_number))
        row = c.fetchone()
        if not row:
            await send_reply(interaction, "❌ VPS not found.", ephemeral=True)
            return
        container_id, status = row

        # create buttons with callbacks
        view = View()

        async def start_cb(btn_inter: Interaction):
            try:
                cont = client.containers.get(container_id)
                cont.start()
                c.execute('UPDATE vps SET status=? WHERE user_id=? AND vps_number=?', ("running", owner_id, vps_number))
                conn.commit()
                await send_reply(btn_inter, f"✅ VPS#{vps_number} started.", ephemeral=True)
            except Exception as e:
                await send_reply(btn_inter, f"❌ {e}", ephemeral=True)

        async def stop_cb(btn_inter: Interaction):
            try:
                cont = client.containers.get(container_id)
                cont.stop()
                c.execute('UPDATE vps SET status=? WHERE user_id=? AND vps_number=?', ("stopped", owner_id, vps_number))
                conn.commit()
                await send_reply(btn_inter, f"⏹️ VPS#{vps_number} stopped.", ephemeral=True)
            except Exception as e:
                await send_reply(btn_inter, f"❌ {e}", ephemeral=True)

        async def restart_cb(btn_inter: Interaction):
            try:
                cont = client.containers.get(container_id)
                cont.restart()
                c.execute('UPDATE vps SET status=? WHERE user_id=? AND vps_number=?', ("running", owner_id, vps_number))
                conn.commit()
                await send_reply(btn_inter, f"🔄 VPS#{vps_number} restarted.", ephemeral=True)
            except Exception as e:
                await send_reply(btn_inter, f"❌ {e}", ephemeral=True)

        async def ssh_cb(btn_inter: Interaction):
            try:
                cont = client.containers.get(container_id)
                # Start tmate session and get ssh link (best-effort)
                try:
                    cont.exec_run("rm -f /tmp/tmate.sock || true")
                    cont.exec_run("tmate -S /tmp/tmate.sock new-session -d")
                    res = cont.exec_run("tmate -S /tmp/tmate.sock display -p '#{tmate_ssh}'", demux=True)
                    out = (res.output or b"").decode().strip() if hasattr(res, "output") else ""
                except Exception:
                    out = ""
                if not out:
                    await send_reply(btn_inter, "❌ Could not generate tmate SSH (tmate might be missing).", ephemeral=True)
                    return
                # try DM the user
                try:
                    await btn_inter.user.send(f"🔗 SSH for VPS#{vps_number} (owner <@{owner_id}>):\n`{out}`")
                except Exception:
                    pass
                await send_reply(btn_inter, "📩 SSH link sent via DM.", ephemeral=True)
            except Exception as e:
                await send_reply(btn_inter, f"❌ {e}", ephemeral=True)

        view.add_item(Button(label="Start", style=discord.ButtonStyle.green, callback=start_cb))
        view.add_item(Button(label="Stop", style=discord.ButtonStyle.red, callback=stop_cb))
        view.add_item(Button(label="Restart", style=discord.ButtonStyle.blurple, callback=restart_cb))
        view.add_item(Button(label="SSH (tmate)", style=discord.ButtonStyle.gray, callback=ssh_cb))

        embed = discord.Embed(title=f"Manage VPS#{vps_number}", description=f"Owner: <@{owner_id}>\nStatus: {status}", color=0x00FF00)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@tree.command(name="manage", description="🛠 Manage your VPSes (admins can pass target_user)")
@app_commands.describe(target_user="Optional: admin can manage another user's VPSes")
async def manage(interaction: Interaction, target_user: discord.User = None):
    # Defer early
    try:
        await interaction.response.defer(ephemeral=True)
    except Exception:
        pass

    requester = interaction.user
    if target_user and is_admin(requester.id):
        uid = target_user.id
    else:
        uid = requester.id

    c.execute('SELECT user_id, vps_number FROM vps WHERE user_id=?', (uid,))
    rows = c.fetchall()
    if not rows:
        await send_reply(interaction, "❌ No VPS found.", ephemeral=True); return

    options = []
    for owner, vpsnum in rows:
        label = f"VPS#{vpsnum} — Owner: <@{owner}>"
        value = f"{owner}-{vpsnum}"
        options.append(discord.SelectOption(label=label, value=value))

    select = ManageSelect(options)
    view = View()
    view.add_item(select)
    await interaction.followup.send("Select a VPS to manage:", view=view, ephemeral=True)

# ---------------- Scheduler job for auto-suspend ----------------
async def auto_suspend_job():
    now = datetime.utcnow().isoformat()
    c.execute('SELECT user_id, vps_number, container_id FROM vps WHERE status="running" AND expires_at <= ?', (now,))
    rows = c.fetchall()
    for user_id, vps_number, container_id in rows:
        try:
            cont = client.containers.get(container_id)
            cont.stop()
        except Exception:
            pass
        c.execute('UPDATE vps SET status=? WHERE user_id=? AND vps_number=?', ("suspended", user_id, vps_number))
        conn.commit()
        if RENEWAL_CHANNEL_ID and GUILD_ID:
            try:
                guild = bot.get_guild(GUILD_ID)
                ch = guild.get_channel(RENEWAL_CHANNEL_ID)
                if ch:
                    await ch.send(f"⚠️ VPS#{vps_number} for <@{user_id}> expired and was suspended.")
            except Exception:
                pass
        send_console(f"auto_suspend: vps#{vps_number} owner {user_id}")

# ---------------- on_ready: clear+sync commands, start scheduler ----------------
@bot.event
async def on_ready():
    send_console(f"Bot ready: {bot.user} ({bot.user.id})")
    # load config values from DB
    try:
        c.execute('SELECT value FROM config WHERE key="log_channel"')
        r = c.fetchone(); 
        if r: 
            global LOG_CHANNEL_ID; LOG_CHANNEL_ID = int(r[0])
        c.execute('SELECT value FROM config WHERE key="renewal_channel"')
        r = c.fetchone(); 
        if r: 
            global RENEWAL_CHANNEL_ID; RENEWAL_CHANNEL_ID = int(r[0])
    except Exception:
        pass

    # Clear and sync to guild to avoid signature mismatch
    try:
        if GUILD_ID:
            await bot.tree.clear_commands(guild=discord.Object(id=GUILD_ID))
            await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
            send_console("Cleared and synced guild commands.")
        else:
            await bot.tree.sync()
            send_console("Synced global commands (may take up to 1 hour to appear).")
    except Exception as e:
        send_console("Error syncing commands:", e)
        try:
            await bot.tree.sync()
        except Exception as e2:
            send_console("Fallback sync failed:", e2)

    # start scheduler in bot loop
    try:
        if not scheduler.running:
            scheduler.add_job(lambda: asyncio.create_task(auto_suspend_job()), 'interval', minutes=60, id="auto_suspend_job")
            scheduler.start()
            send_console("Scheduler started.")
    except Exception as e:
        send_console("Scheduler start failed:", e)

# ---------------- Run ----------------
if __name__ == "__main__":
    bot.run(TOKEN)
