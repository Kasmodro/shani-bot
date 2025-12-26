import os
import json
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(BASE_DIR, "guild_config.json")

# --- ENV ---
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise SystemExit("DISCORD_TOKEN fehlt in der .env")

# --- INTENTS ---
intents = discord.Intents.default()
intents.voice_states = True
intents.members = True  # empfohlen für display_name/member checks
# message_content brauchen wir NICHT (wir nutzen Slash Commands)
bot = commands.Bot(command_prefix="!", intents=intents)

# --- CONFIG HELPERS ---
def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ Config konnte nicht geladen werden: {e}")
        return {}

def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

GUILD_CONFIG = load_config()

def get_guild_cfg(guild_id: int) -> dict | None:
    return GUILD_CONFIG.get(str(guild_id))

def set_guild_cfg(guild_id: int, create_channel_id: int, voice_category_id: int) -> None:
    GUILD_CONFIG[str(guild_id)] = {
        "create_channel_id": int(create_channel_id),
        "voice_category_id": int(voice_category_id),
    }
    save_config(GUILD_CONFIG)

def clear_guild_cfg(guild_id: int) -> None:
    if str(guild_id) in GUILD_CONFIG:
        del GUILD_CONFIG[str(guild_id)]
        save_config(GUILD_CONFIG)

# --- NAMING ---
def squad_channel_name(member: discord.Member) -> str:
    return f"Squad {member.display_name}"

# --- EVENTS ---
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"✅ Slash Commands synced: {len(synced)}")
    except Exception as e:
        print(f"⚠️ Slash Sync failed: {e}")

    print(f"🤖 Shani ist online als {bot.user}")

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    cfg = get_guild_cfg(member.guild.id)
    if not cfg:
        return

    create_id = int(cfg["create_channel_id"])
    category_id = int(cfg["voice_category_id"])

    # JOIN: User betritt den "Create" Channel
    if after.channel and after.channel.id == create_id:
        category = member.guild.get_channel(category_id)
        if not isinstance(category, discord.CategoryChannel):
            print(f"❌ [{member.guild.name}] Ziel-Kategorie fehlt/ungültig (ID={category_id}).")
            return

        # 1) Channel erstellen
        try:
            channel = await member.guild.create_voice_channel(
                name=squad_channel_name(member),
                category=category
            )
            print(f"➕ [{member.guild.name}] Created: {channel.name} (owner={member.display_name})")
        except discord.Forbidden as e:
            print(f"❌ [{member.guild.name}] Forbidden: create_voice_channel (fehlende Rechte 'Kanäle verwalten'?) | {e}")
            return
        except discord.HTTPException as e:
            print(f"❌ [{member.guild.name}] HTTPException: create_voice_channel | {e}")
            return

        # 2) Owner-Rechte auf dem NEUEN Channel setzen
        try:
            await channel.set_permissions(
                member,
                manage_channels=True,  # umbenennen/limit etc. (nur für diesen Channel)
                move_members=True,     # Leute rauswerfen/verschieben
                connect=True,
                speak=True
            )
            print(f"✅ [{member.guild.name}] Permissions set for owner={member.display_name} on {channel.name}")
        except discord.Forbidden as e:
            print(f"❌ [{member.guild.name}] Forbidden: set_permissions (Shani darf Permissions nicht setzen?) | {e}")
        except discord.HTTPException as e:
            print(f"❌ [{member.guild.name}] HTTPException: set_permissions | {e}")

        # 3) User verschieben
        try:
            await member.move_to(channel)
            print(f"➡️ [{member.guild.name}] Moved {member.display_name} -> {channel.name}")
        except discord.Forbidden as e:
            print(f"❌ [{member.guild.name}] Forbidden: move_to (fehlende Rechte 'Mitglieder verschieben'?) | {e}")
        except discord.HTTPException as e:
            print(f"❌ [{member.guild.name}] HTTPException: move_to | {e}")

    # OPTIONAL: Beim Join in Kategorie sicherstellen, dass Name korrekt ist
    if after.channel and after.channel.category and after.channel.category.id == category_id:
        if after.channel.id != create_id:
            desired = squad_channel_name(member)
            current = after.channel.name

            looks_like_old = current == member.display_name or current == f"🎧 {member.display_name}"
            looks_not_squad = not current.lower().startswith("squad ")

            if looks_like_old or looks_not_squad:
                try:
                    await after.channel.edit(name=desired)
                    print(f"✏️ [{member.guild.name}] Renamed channel: '{current}' -> '{desired}'")
                except discord.Forbidden as e:
                    print(f"❌ [{member.guild.name}] Forbidden: rename channel (fehlende Rechte 'Kanäle verwalten'?) | {e}")
                except discord.NotFound:
                    print(f"ℹ️ [{member.guild.name}] Rename skipped: channel already gone (404)")
                except discord.HTTPException as e:
                    print(f"❌ [{member.guild.name}] HTTPException: rename channel | {e}")

    # LEAVE: Wenn Channel leer -> löschen
    if before.channel:
        ch = before.channel

        # Niemals den Create-Channel löschen
        if ch.id == create_id:
            return

        # Nur Channels in unserer Ziel-Kategorie löschen
        if ch.category and ch.category.id == category_id and len(ch.members) == 0:
            try:
                name = ch.name
                await ch.delete()
                print(f"🗑️ [{member.guild.name}] Deleted empty channel: {name}")
            except discord.NotFound:
                print(f"ℹ️ [{member.guild.name}] Delete skipped: channel already gone (404)")
            except discord.Forbidden as e:
                print(f"❌ [{member.guild.name}] Forbidden: delete channel (fehlende Rechte 'Kanäle verwalten'?) | {e}")
            except discord.HTTPException as e:
                print(f"❌ [{member.guild.name}] HTTPException: delete channel | {e}")

# --- SLASH COMMANDS ---
@bot.tree.command(name="setup_autovoice", description="Richtet Auto-Voice ein: Join-Channel + Ziel-Kategorie auswählen.")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    create_channel="Voice-Channel, den man joint um einen Squad-Channel zu erstellen",
    target_category="Kategorie, in der die erstellten Squad-Channels landen sollen"
)
async def setup_autovoice(
    interaction: discord.Interaction,
    create_channel: discord.VoiceChannel,
    target_category: discord.CategoryChannel
):
    set_guild_cfg(interaction.guild_id, create_channel.id, target_category.id)
    await interaction.response.send_message(
        f"✅ Auto-Voice aktiviert.\n"
        f"➕ Join-Channel: **{create_channel.name}**\n"
        f"📁 Ziel-Kategorie: **{target_category.name}**\n\n"
        f"Ergebnis: **Squad <Username>**",
        ephemeral=True
    )

@bot.tree.command(name="autovoice_status", description="Zeigt die aktuelle Auto-Voice Konfiguration an.")
@app_commands.checks.has_permissions(manage_guild=True)
async def autovoice_status(interaction: discord.Interaction):
    cfg = get_guild_cfg(interaction.guild_id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Auto-Voice ist auf diesem Server noch nicht eingerichtet.", ephemeral=True)
        return

    create_ch = interaction.guild.get_channel(int(cfg["create_channel_id"]))
    cat = interaction.guild.get_channel(int(cfg["voice_category_id"]))

    await interaction.response.send_message(
        "✅ Auto-Voice Status:\n"
        f"➕ Join-Channel: **{create_ch.name if create_ch else 'FEHLT (gelöscht?)'}**\n"
        f"📁 Ziel-Kategorie: **{cat.name if cat else 'FEHLT (gelöscht?)'}**\n"
        f"🏷️ Naming: **Squad <Username>**",
        ephemeral=True
    )

@bot.tree.command(name="autovoice_disable", description="Deaktiviert Auto-Voice auf diesem Server.")
@app_commands.checks.has_permissions(manage_guild=True)
async def autovoice_disable(interaction: discord.Interaction):
    clear_guild_cfg(interaction.guild_id)
    await interaction.response.send_message("🛑 Auto-Voice wurde deaktiviert.", ephemeral=True)

@setup_autovoice.error
@autovoice_status.error
@autovoice_disable.error
async def perms_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "❌ Dafür brauchst du **Server verwalten**."
    else:
        msg = f"⚠️ Fehler: {error}"

    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)

bot.run(TOKEN)
