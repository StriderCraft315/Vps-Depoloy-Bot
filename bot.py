import discord
from discord.ext import commands, tasks
from discord import app_commands
import docker
import json
import os
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("1048279571386081451"))
client = docker.from_env()

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

VPS_DATA_FILE = "vps_data.json"
ADMIN_FILE = "admins.json"

def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=4)

vps_data = load_json(VPS_DATA_FILE, {})
admins = load_json(ADMIN_FILE, [])

def is_admin(user_id):
    return user_id in admins

@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ Logged in as {bot.user}")
    print("🌐 Commands synced with Discord.")

@tree.command(name="admin-add", description="Add a new bot admin")
@app_commands.describe(user="User to add as admin")
async def admin_add(interaction: discord.Interaction, user: discord.User):
    if not is_admin(interaction.user.id):
        await interaction.response.send_message("❌ You are not an admin.", ephemeral=True)
        return
    if user.id not in admins:
        admins.append(user.id)
        save_json(ADMIN_FILE, admins)
        await interaction.response.send_message(f"✅ Added {user.mention} as an admin.", ephemeral=True)
    else:
        await interaction.response.send_message(f"{user.mention} is already an admin.", ephemeral=True)

@tree.command(name="create", description="Create a new VPS container")
@app_commands.describe(ram_gb="RAM (GB)", disk_gb="Disk (GB)")
async def create(interaction: discord.Interaction, ram_gb: int = 1, disk_gb: int = 5):
    await interaction.response.defer(ephemeral=True)

    user = interaction.user
    user_id = str(user.id)
    vps_list = vps_data.get(user_id, [])
    vps_number = len(vps_list) + 1
    container_name = f"vps-{user_id}-{vps_number}"

    try:
        container = client.containers.run(
            "ubuntu:latest",
            name=container_name,
            detach=True,
            tty=True,
            mem_limit=f"{ram_gb}g",
            storage_opt={"size": f"{disk_gb}G"},
            command="sleep infinity",
        )
        vps_list.append({
            "name": container_name,
            "id": container.short_id,
            "ram_gb": ram_gb,
            "disk_gb": disk_gb,
            "owner": user.name
        })
        vps_data[user_id] = vps_list
        save_json(VPS_DATA_FILE, vps_data)
        await interaction.followup.send(f"✅ VPS#{vps_number} created for {user.mention} ({ram_gb}GB RAM / {disk_gb}GB Disk).", ephemeral=True)
    except docker.errors.APIError as e:
        await interaction.followup.send(f"❌ Docker error: {str(e)}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"⚠️ Unexpected error: {str(e)}", ephemeral=True)

@tree.command(name="manage", description="View all VPSes you or admins own")
async def manage(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    user_id = str(interaction.user.id)
    if is_admin(interaction.user.id):
        all_vps = []
        for owner_id, vps_list in vps_data.items():
            for vps in vps_list:
                all_vps.append(f"👤 Owner: <@{owner_id}> | VPS: `{vps['name']}` ({vps['ram_gb']}GB RAM / {vps['disk_gb']}GB Disk)")
        if not all_vps:
            await interaction.followup.send("❌ No VPS found.", ephemeral=True)
        else:
            await interaction.followup.send("\n".join(all_vps), ephemeral=True)
    else:
        user_vps = vps_data.get(user_id, [])
        if not user_vps:
            await interaction.followup.send("❌ You don’t own any VPS.", ephemeral=True)
        else:
            vps_list = "\n".join(
                [f"🖥️ VPS#{i+1}: `{v['name']}` ({v['ram_gb']}GB RAM / {v['disk_gb']}GB Disk)" for i, v in enumerate(user_vps)]
            )
            await interaction.followup.send(vps_list, ephemeral=True)

@tree.command(name="delete", description="Delete your VPS (admin can delete any)")
@app_commands.describe(number="VPS number to delete", owner_id="Optional owner ID (for admins)")
async def delete(interaction: discord.Interaction, number: int, owner_id: str = None):
    await interaction.response.defer(ephemeral=True)

    if is_admin(interaction.user.id) and owner_id:
        target_id = owner_id
    else:
        target_id = str(interaction.user.id)

    vps_list = vps_data.get(target_id, [])
    if 0 < number <= len(vps_list):
        vps = vps_list.pop(number - 1)
        try:
            container = client.containers.get(vps["id"])
            container.stop()
            container.remove()
        except Exception:
            pass
        vps_data[target_id] = vps_list
        save_json(VPS_DATA_FILE, vps_data)
        await interaction.followup.send(f"🗑️ Deleted VPS#{number} for <@{target_id}>", ephemeral=True)
    else:
        await interaction.followup.send("❌ Invalid VPS number.", ephemeral=True)

scheduler = AsyncIOScheduler()

async def main():
    scheduler.start()
    await bot.start(TOKEN)

asyncio.run(main())
