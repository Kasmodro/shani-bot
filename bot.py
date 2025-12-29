# ============================================================
# SHANI DISCORD BOT
# Entwickelt von: Kasmodro
# Zweck: Raider-Setcards, Auto-Voice & Twitch-Alerts
# Repository: https://github.com/Kasmodro/shani-bot
# ============================================================

import os
import re
import time
import html
import asyncio
import logging
import sqlite3
import traceback
import aiohttp
import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
from datetime import datetime, timezone

# --- LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("shani-bot")

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "setcards.db")

# --- ENV ---
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise SystemExit("DISCORD_TOKEN fehlt in der .env")

# --- INTENTS ---
intents = discord.Intents.default()
intents.voice_states = True
intents.members = True
intents.message_content = True  # Erlaubt dem Bot Nachrichten zu lesen (für ! commands)
bot = commands.Bot(command_prefix="!", intents=intents)

# Global Session
bot.http_session = None

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    logger.error(f"APP_COMMAND_ERROR: {error}", exc_info=error)
    try:
        if interaction.response.is_done():
            await interaction.followup.send("❌ Fehler im Command (siehe Server-Log).", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Fehler im Command (siehe Server-Log).", ephemeral=True)
    except Exception:
        pass

# ============================================================
# DATABASE HELPERS
# ============================================================
async def _db_run(func, *args):
    return await asyncio.to_thread(func, *args)

def _db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

async def get_guild_cfg(guild_id: int) -> dict:
    def _get():
        with _db_connect() as conn:
            row = conn.execute("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)).fetchone()
            return dict(row) if row else {}
    return await _db_run(_get)

async def update_guild_cfg(guild_id: int, **kwargs) -> None:
    def _update():
        with _db_connect() as conn:
            # Check if exists
            exists = conn.execute("SELECT 1 FROM guild_settings WHERE guild_id = ?", (guild_id,)).fetchone()
            if not exists:
                conn.execute("INSERT INTO guild_settings (guild_id) VALUES (?)", (guild_id,))
            
            if not kwargs:
                return

            keys = list(kwargs.keys())
            values = list(kwargs.values())
            set_clause = ", ".join([f"{k} = ?" for k in keys])
            conn.execute(f"UPDATE guild_settings SET {set_clause} WHERE guild_id = ?", values + [guild_id])
            conn.commit()
    await _db_run(_update)

async def clear_guild_cfg_fields(guild_id: int, fields: list) -> None:
    def _clear():
        with _db_connect() as conn:
            set_clause = ", ".join([f"{f} = NULL" for f in fields])
            conn.execute(f"UPDATE guild_settings SET {set_clause} WHERE guild_id = ?", (guild_id,))
            conn.commit()
    await _db_run(_clear)






# ============================================================
# MODULE LOADING
# ============================================================
async def load_modules():
    # Setcard-Modul
    await bot.load_extension("modules.setcards")
    # Twitch-Modul
    await bot.load_extension("modules.twitch")
    # YouTube-Modul
    await bot.load_extension("modules.youtube")

# ============================================================
# VOICE CONFIG
# ============================================================
async def _create_squad_channel(member: discord.Member, target_limit: int):
    """Interne Hilfsfunktion zur Erstellung eines Squad-Channels (für Event & Buttons)"""
    cfg = await get_guild_cfg(member.guild.id)
    if not cfg: return None
    
    category_id = cfg.get("voice_category_id")
    if not category_id: return None
    
    category = member.guild.get_channel(int(category_id))
    if not isinstance(category, discord.CategoryChannel):
        logger.error(f"[{member.guild.name}] Ziel-Kategorie fehlt/ungültig (ID={category_id}).")
        return None

    try:
        channel = await member.guild.create_voice_channel(
            name=squad_channel_name(member, target_limit),
            category=category,
            user_limit=target_limit
        )
        logger.info(f"➕ [{member.guild.name}] Created {target_limit if target_limit > 0 else 'Open'}: {channel.name} (owner={member.display_name})")
        
        # Berechtigungen vorbereiten
        perms_kwargs = {
            "connect": True,
            "speak": True,
            "move_members": True,
            "manage_channels": False
        }
        if hasattr(discord.PermissionOverwrite(), "set_voice_channel_status"):
            perms_kwargs["set_voice_channel_status"] = True

        await channel.set_permissions(member, **perms_kwargs)
        
        # User verschieben falls er in einem Voice ist
        if member.voice:
            await member.move_to(channel)

        # --- Setcard-Info im Channel-Textchat ---
        from modules.setcards import get_card, build_setcard_embed
        card = await get_card(member.guild.id, member.id)
        if card:
            embed = build_setcard_embed(member, card)
            embed.title = f"Besitzer von {channel.name}"
            try:
                await channel.send(embed=embed)
            except:
                pass
        
        # --- Verzögerter Cleanup (2 Minuten) ---
        # Falls nach 2 Minuten niemand drin ist, wird der Kanal gelöscht.
        async def delayed_cleanup(chan_id: int):
            await asyncio.sleep(120)
            chan = bot.get_channel(chan_id)
            if chan and isinstance(chan, discord.VoiceChannel):
                if len(chan.members) == 0:
                    try:
                        await chan.delete()
                        logger.info(f"🗑️ [Delayed Cleanup] Deleted unused squad channel {chan.name}")
                    except:
                        pass
        
        asyncio.create_task(delayed_cleanup(channel.id))

        return channel
    except Exception as e:
        logger.error(f"[{member.guild.name}] Error in _create_squad_channel: {e}")
        return None

async def set_guild_voice_cfg(guild_id: int, create_channel_id: int, create_channel_3_id: int, create_channel_open_id: int, voice_category_id: int) -> None:
    await update_guild_cfg(
        guild_id,
        create_channel_id=int(create_channel_id),
        create_channel_3_id=int(create_channel_3_id),
        create_channel_open_id=int(create_channel_open_id),
        voice_category_id=int(voice_category_id)
    )

async def clear_guild_voice_cfg(guild_id: int) -> None:
    await clear_guild_cfg_fields(guild_id, ["create_channel_id", "create_channel_3_id", "create_channel_open_id", "voice_category_id"])

def squad_channel_name(member: discord.Member, limit: int) -> str:
    if limit == 0:
        return f"Squad {member.display_name} (Open)"
    return f"Squad {member.display_name} ({limit}er)"

async def cleanup_empty_squads(guild: discord.Guild, category_id: int):
    category = guild.get_channel(category_id)
    if not isinstance(category, discord.CategoryChannel):
        return
    
    for channel in category.voice_channels:
        if channel.name.startswith("Squad ") and len(channel.members) == 0:
            # Check if it's one of the join channels (don't delete those!)
            cfg = await get_guild_cfg(guild.id)
            join_ids = [
                int(cfg.get("create_channel_id") or 0),
                int(cfg.get("create_channel_3_id") or 0),
                int(cfg.get("create_channel_open_id") or 0)
            ]
            if channel.id not in join_ids:
                try:
                    await channel.delete()
                    logger.info(f"🗑️ [{guild.name}] Cleanup: Deleted empty squad channel {channel.name}")
                except discord.NotFound:
                    pass
                except Exception as e:
                    logger.error(f"[{guild.name}] Cleanup failed for {channel.name}: {e}")

# ============================================================
# EVENTS
# ============================================================
@bot.event
async def on_ready():
    # Session anlegen
    if not bot.http_session:
        bot.http_session = aiohttp.ClientSession()

    # Module laden, bevor wir Commands syncen (damit /setcard dabei ist)
    try:
        if not getattr(bot, "_setcards_loaded", False):
            await load_modules()
            bot._setcards_loaded = True
            logger.info("Module geladen")
    except Exception as e:
        logger.error(f"Module konnten nicht geladen werden: {e}")

    # ---- SYNC: Global-Commands in jede Guild kopieren + sofort guild-sync ----
    # (Wir löschen globale Registrierungen, um Dopplungen zu vermeiden)
    try:
        # 1. Globale Ebene leeren (damit dort nichts hängen bleibt)
        # bot.tree.clear_commands(guild=None) 
        # await bot.tree.sync() # Nur nötig wenn man globale Commands hart entfernen will

        total = 0
        for g in bot.guilds:
            # Wir registrieren ALLES auf Guild-Ebene für sofortige Verfügbarkeit
            bot.tree.copy_global_to(guild=g)
            synced = await bot.tree.sync(guild=g)
            total += len(synced)
            logger.info(f"Slash Commands synced for {g.name}: {len(synced)}")

        logger.info(f"Slash Commands synced total (sum guilds): {total}")
    except Exception as e:
        logger.error(f"Slash Sync failed: {e}")

    logger.info(f"Shani ist online als {bot.user}")

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.display_name == after.display_name:
        return
    
    # Check if user owns a squad channel
    if after.voice and after.voice.channel:
        ch = after.voice.channel
        # Simple heuristic: starts with "Squad " and member has manage_channels permissions
        if ch.name.startswith("Squad ") and ch.permissions_for(after).manage_channels:
            # We don't know the limit easily here, but we can check if it matches the current name pattern
            # and just update the name part.
            limit = ch.user_limit
            new_name = squad_channel_name(after, limit)
            if ch.name != new_name:
                try:
                    await ch.edit(name=new_name)
                    logger.info(f"Renamed channel to {new_name} because of display name change of {after}")
                except:
                    pass

# ============================================================
# VOICE EVENT
# ============================================================
@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    cfg = await get_guild_cfg(member.guild.id)
    if not cfg:
        return

    category_id = cfg.get("voice_category_id")
    if not category_id:
        return

    create_id_2 = cfg.get("create_channel_id")
    create_id_3 = cfg.get("create_channel_3_id")
    create_id_open = cfg.get("create_channel_open_id")

    # --- Erstellen ---
    target_limit = -1
    if after.channel:
        if create_id_2 and after.channel.id == int(create_id_2):
            target_limit = 2
        elif create_id_3 and after.channel.id == int(create_id_3):
            target_limit = 3
        elif create_id_open and after.channel.id == int(create_id_open):
            target_limit = 0

    if target_limit != -1:
        await _create_squad_channel(member, target_limit)
        
        # Nach dem Erstellen kurz warten und aufräumen
        await asyncio.sleep(1.5)
        await cleanup_empty_squads(member.guild, int(category_id))

    # --- Globaler Cleanup bei jedem State-Wechsel ---
    if before.channel or after.channel:
        await cleanup_empty_squads(member.guild, int(category_id))

    if after.channel and after.channel.category and after.channel.category.id == int(category_id):
        is_join_2 = create_id_2 and after.channel.id == int(create_id_2)
        is_join_3 = create_id_3 and after.channel.id == int(create_id_3)
        is_join_open = create_id_open and after.channel.id == int(create_id_open)
        
        if not is_join_2 and not is_join_3 and not is_join_open:
            limit = after.channel.user_limit
            desired = squad_channel_name(member, limit)
            current = after.channel.name

            looks_like_old = current == member.display_name or current == f"🎧 {member.display_name}"
            looks_not_squad = not current.lower().startswith("squad ")

            if looks_like_old or looks_not_squad:
                try:
                    await after.channel.edit(name=desired)
                    logger.info(f"✏️ [{member.guild.name}] Renamed channel: '{current}' -> '{desired}'")
                except discord.Forbidden:
                    pass
                except discord.NotFound:
                    pass
                except discord.HTTPException as e:
                    logger.error(f"[{member.guild.name}] HTTPException: rename channel | {e}")

# ============================================================
# SLASH COMMANDS: VOICE (1:1)
# ============================================================
@bot.tree.command(name="setup_autovoice", description="Richtet Auto-Voice ein: Join-Channels + Ziel-Kategorie.")
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(
    create_channel_2="Voice-Channel für 2er Squads",
    create_channel_3="Voice-Channel für 3er Squads",
    create_channel_open="Voice-Channel für Open Squads (kein Limit)",
    target_category="Kategorie, in der die erstellten Squad-Channels landen sollen"
)
async def setup_autovoice(
    interaction: discord.Interaction,
    create_channel_2: discord.VoiceChannel,
    create_channel_3: discord.VoiceChannel,
    create_channel_open: discord.VoiceChannel,
    target_category: discord.CategoryChannel
):
    await set_guild_voice_cfg(interaction.guild_id, create_channel_2.id, create_channel_3.id, create_channel_open.id, target_category.id)
    await interaction.response.send_message(
        f"✅ Auto-Voice aktiviert.\n"
        f"👥 2er Join-Channel: **{create_channel_2.name}**\n"
        f"👥 3er Join-Channel: **{create_channel_3.name}**\n"
        f"🔓 Open Join-Channel: **{create_channel_open.name}**\n"
        f"📁 Ziel-Kategorie: **{target_category.name}**\n\n"
        f"Ergebnis: **Squad <Username> (...)**",
        ephemeral=True
    )

@bot.tree.command(name="autovoice_status", description="Zeigt die aktuelle Auto-Voice Konfiguration an.")
@app_commands.default_permissions(manage_guild=True)
async def autovoice_status(interaction: discord.Interaction):
    cfg = await get_guild_cfg(interaction.guild_id)
    if not cfg or not cfg.get("voice_category_id"):
        await interaction.response.send_message("ℹ️ Auto-Voice ist auf diesem Server noch nicht eingerichtet.", ephemeral=True)
        return

    ch2 = interaction.guild.get_channel(int(cfg.get("create_channel_id", 0))) if cfg.get("create_channel_id") else None
    ch3 = interaction.guild.get_channel(int(cfg.get("create_channel_3_id", 0))) if cfg.get("create_channel_3_id") else None
    chO = interaction.guild.get_channel(int(cfg.get("create_channel_open_id", 0))) if cfg.get("create_channel_open_id") else None
    cat = interaction.guild.get_channel(int(cfg.get("voice_category_id", 0))) if cfg.get("voice_category_id") else None

    await interaction.response.send_message(
        "✅ Auto-Voice Status:\n"
        f"👥 2er Join-Channel: **{ch2.name if ch2 else '❌'}**\n"
        f"👥 3er Join-Channel: **{ch3.name if ch3 else '❌'}**\n"
        f"🔓 Open Join-Channel: **{chO.name if chO else '❌'}**\n"
        f"📁 Ziel-Kategorie: **{cat.name if cat else 'FEHLT'}**",
        ephemeral=True
    )

@bot.tree.command(name="autovoice_disable", description="Deaktiviert Auto-Voice auf diesem Server.")
@app_commands.checks.has_permissions(manage_guild=True)
async def autovoice_disable(interaction: discord.Interaction):
    await clear_guild_voice_cfg(interaction.guild_id)
    await interaction.response.send_message("🛑 Auto-Voice wurde deaktiviert.", ephemeral=True)

# ============================================================
# SQUAD MENU & COMMANDS
# ============================================================
class SquadMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="2er Squad", style=discord.ButtonStyle.primary, emoji="👥")
    async def btn_2er(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._create(interaction, 2)

    @discord.ui.button(label="3er Squad", style=discord.ButtonStyle.primary, emoji="👪")
    async def btn_3er(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._create(interaction, 3)

    @discord.ui.button(label="Open Squad", style=discord.ButtonStyle.secondary, emoji="🔓")
    async def btn_open(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._create(interaction, 0)

    async def _create(self, interaction: discord.Interaction, limit: int):
        await interaction.response.defer(ephemeral=True)
        channel = await _create_squad_channel(interaction.user, limit)
        if channel:
            msg = f"✅ Squad-Channel **{channel.mention}** wurde erstellt."
            if not interaction.user.voice:
                msg += f"\n\nKlicke oben auf den Link, um deinem neuen Channel beizutreten!"
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.followup.send("❌ Fehler: Auto-Voice ist nicht konfiguriert oder die Kategorie fehlt.", ephemeral=True)

@bot.tree.command(name="squad", description="Öffnet das Menü zum Erstellen eines Squad-Channels.")
async def squad_cmd(interaction: discord.Interaction):
    view = SquadMenuView()
    embed = discord.Embed(
        title="🎮 Squad erstellen",
        description="Wähle die Größe deines Squads. Der Channel wird automatisch erstellt.",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# ============================================================
# GLOBAL STATUS & MENU COMMANDS
# ============================================================
class BotNameModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Bot-Anzeigenamen ändern")
        self.new_name = discord.ui.TextInput(
            label="Neuer Name",
            placeholder="z.B. Raider-Bot (Standard: Shani)",
            min_length=2,
            max_length=32,
            required=True
        )
        self.add_item(self.new_name)

    async def on_submit(self, interaction: discord.Interaction):
        await update_guild_cfg(interaction.guild_id, bot_custom_name=str(self.new_name.value))
        await interaction.response.send_message(f"✅ Der Bot-Anzeigename wurde auf **{self.new_name.value}** geändert.", ephemeral=True)

class ShaniMenuView(discord.ui.View):
    def __init__(self, member: discord.Member, cfg: dict):
        super().__init__(timeout=60)
        self.member = member
        self.cfg = cfg

        # Berechtigungen prüfen
        is_admin = member.guild_permissions.manage_guild or (cfg.get("role_admin_id") and member.get_role(int(cfg["role_admin_id"])))
        is_mod = is_admin or (cfg.get("role_mod_id") and member.get_role(int(cfg["role_mod_id"])))
        
        # Jeder darf Setcards (wenn nicht anders eingeschränkt)
        can_setcard = True
        if cfg.get("role_setcard_id"):
             can_setcard = member.get_role(int(cfg["role_setcard_id"])) or is_mod

        # Buttons hinzufügen
        if can_setcard:
            btn_sc = discord.ui.Button(label="Meine Setcard", style=discord.ButtonStyle.primary, custom_id="shani_menu_sc")
            self.add_item(btn_sc)
            
            btn_squad = discord.ui.Button(label="🎮 Squad erstellen", style=discord.ButtonStyle.success, custom_id="shani_menu_squad")
            self.add_item(btn_squad)

            btn_find = discord.ui.Button(label="Raider suchen", style=discord.ButtonStyle.secondary, custom_id="shani_menu_find")
            self.add_item(btn_find)

            # Neuer Button für Raider-Liste
            btn_list = discord.ui.Button(label="Alle Raider anzeigen", style=discord.ButtonStyle.secondary, custom_id="shani_menu_list")
            self.add_item(btn_list)

        if is_mod:
            btn_status = discord.ui.Button(label="Bot Status", style=discord.ButtonStyle.success, custom_id="shani_menu_status")
            self.add_item(btn_status)
        
        if is_admin:
             btn_admin = discord.ui.Button(label="Admin Setup", style=discord.ButtonStyle.danger, custom_id="shani_menu_admin")
             self.add_item(btn_admin)

    def _get_bot_name(self):
        return self.cfg.get("bot_custom_name") or "Shani"

@bot.tree.command(name="shani", description="Öffnet das Shani-Hauptmenü.")
async def shani(interaction: discord.Interaction):
    cfg = await get_guild_cfg(interaction.guild_id)
    view = ShaniMenuView(interaction.user, cfg)
    
    bot_name = cfg.get("bot_custom_name") or "Shani"
    
    embed = discord.Embed(
        title=f"🤖 {bot_name} Hauptmenü",
        description="Wähle eine Option aus dem Menü unten.",
        color=discord.Color.blue()
    )
    embed.set_footer(text=f"Raiders Cache • {bot_name}")
    
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.listen("on_interaction")
async def shani_menu_listener(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.component:
        return
    
    cid = interaction.data.get("custom_id")
    if not cid or not cid.startswith("shani_menu_"):
        return

    # Slash commands triggern (simuliert)
    if cid == "shani_menu_sc":
        # Direkt die Edit-View aufrufen
        from modules.setcards import get_card, SetcardEditViewPage1
        card = await get_card(interaction.guild_id, interaction.user.id)
        view = SetcardEditViewPage1(interaction.user, card)
        content = view._header() + "\n\n" + view._status_lines()
        await interaction.response.send_message(content=content, view=view, ephemeral=True)
        view.message = await interaction.original_response()
    elif cid == "shani_menu_squad":
        view = SquadMenuView()
        embed = discord.Embed(
            title="🎮 Squad erstellen",
            description="Wähle die Größe deines Squads. Der Channel wird automatisch erstellt und du wirst (falls möglich) verschoben.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    elif cid == "shani_menu_find":
        # Interaktive Suche öffnen
        from modules.setcards import ORIENTATION_OPTIONS, EXPERIENCE_OPTIONS, PLATFORM_OPTIONS
        view = RaiderSearchView()
        # Optionen laden
        view.orientation_select.options = [discord.SelectOption(label=o, value=o) for o in ORIENTATION_OPTIONS]
        view.experience_select.options = [discord.SelectOption(label=o, value=o) for o in EXPERIENCE_OPTIONS]
        view.platform_select.options = [discord.SelectOption(label=o, value=o) for o in PLATFORM_OPTIONS]
        
        embed = discord.Embed(
            title="🔍 Raider suchen",
            description="Wähle deine Filter aus, um passende Mitspieler zu finden.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    elif cid == "shani_menu_list":
        # Zeigt einfach alle Raider an
        from modules.setcards import get_setcard_channel_id
        sc_channel_id = await get_setcard_channel_id(interaction.guild_id)
        if sc_channel_id:
            channel = interaction.guild.get_channel(sc_channel_id)
            if channel:
                await interaction.response.send_message(f"Schau mal in {channel.mention} vorbei, dort findest du alle Setcards!", ephemeral=True)
            else:
                await interaction.response.send_message("Der Setcard-Kanal wurde nicht gefunden.", ephemeral=True)
        else:
            await interaction.response.send_message("Es ist noch kein Setcard-Kanal konfiguriert.", ephemeral=True)
    elif cid == "shani_menu_status":
        await shani_status.callback(interaction)
    elif cid == "shani_menu_admin":
        view = ShaniSetupView()
        bot_name = interaction.message.embeds[0].footer.text.split(" • ")[-1] if interaction.message and interaction.message.embeds and interaction.message.embeds[0].footer else "Shani"
        
        embed = discord.Embed(
            title=f"🛠️ {bot_name} Admin Setup",
            description="Hier kannst du alle wichtigen Funktionen des Bots konfigurieren.",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class RaiderSearchView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.filters = {"orientation": None, "experience": None, "platform": None}

    @discord.ui.select(placeholder="🎮 Orientierung (Mehrfachauswahl)", min_values=0, max_values=4, row=0)
    async def orientation_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.filters["orientation"] = select.values if select.values else None
        await interaction.response.defer()

    @discord.ui.select(placeholder="🎓 Erfahrung", min_values=0, max_values=1, row=1)
    async def experience_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.filters["experience"] = select.values[0] if select.values else None
        await interaction.response.defer()

    @discord.ui.select(placeholder="🖥️ Plattform", min_values=0, max_values=1, row=2)
    async def platform_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.filters["platform"] = select.values[0] if select.values else None
        await interaction.response.defer()

    @discord.ui.button(label="🔍 Suchen", style=discord.ButtonStyle.success, row=3)
    async def btn_search(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.setcards import list_cards_in_guild, build_setcard_embed, _match_card
        
        try:
            logger.info(f"🔍 [RaiderSearch] Start | Filter: {self.filters}")
            cards = await list_cards_in_guild(interaction.guild_id)
            
            # Filtern
            matches = []
            for c in cards:
                if _match_card(c, 
                               self.filters["orientation"], 
                               self.filters["experience"],
                               self.filters["platform"],
                               None, None, None):
                    matches.append(c)

            logger.info(f"🔍 [RaiderSearch] Treffer: {len(matches)}")

            if not matches:
                await interaction.response.send_message("❌ Keine passenden Raider gefunden mit diesen Filtern.", ephemeral=True)
                return

            # Zeige Ergebnisse wie im Slash-Command als Liste, falls es viele sind
            if len(matches) > 3:
                lines = []
                for m in matches[:20]:
                    member = interaction.guild.get_member(m["user_id"])
                    name = member.mention if member else f"<@{m['user_id']}>"
                    ori = "·".join(m.get("orientation") or [])
                    lines.append(f"{name} — {ori} — {m.get('experience')} — {m.get('platform')}")
                
                embed = discord.Embed(
                    title="🔎 Suchergebnisse",
                    description="\n".join(lines),
                    color=discord.Color.green()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                # Bei wenigen Treffern Einzel-Embeds
                await interaction.response.send_message(f"✅ Treffer gefunden:", ephemeral=True)
                for m in matches:
                    member = interaction.guild.get_member(m["user_id"])
                    if not member:
                        try: member = await interaction.guild.fetch_member(m["user_id"])
                        except: pass
                    if member:
                        await interaction.followup.send(embed=build_setcard_embed(member, m), ephemeral=True)
        except Exception as e:
            logger.error(f"❌ Fehler bei RaiderSearch: {e}", exc_info=True)
            await interaction.response.send_message(f"❌ Ein interner Fehler ist aufgetreten: {e}", ephemeral=True)

class ShaniSetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Rollen festlegen", style=discord.ButtonStyle.primary, row=0)
    async def btn_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = RoleSetupView()
        embed = discord.Embed(
            title="👑 Rollen-Setup",
            description="Wähle die Rollen für die verschiedenen Zugriffsebenen aus.",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Setcard-Kanal", style=discord.ButtonStyle.primary, row=0)
    async def btn_sc_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = SetcardChannelSetupView()
        embed = discord.Embed(
            title="🛠️ Setcard-Kanal",
            description="Wähle den Kanal aus, in dem die Setcards der Raider gepostet werden sollen.",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Auto-Voice", style=discord.ButtonStyle.secondary, row=1)
    async def btn_voice(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = AutoVoiceSetupView()
        embed = discord.Embed(
            title="🔊 Auto-Voice Setup",
            description="Wähle die Join-Channels und die Ziel-Kategorie aus.",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Twitch-Live", style=discord.ButtonStyle.secondary, row=1)
    async def btn_twitch(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.twitch import TwitchSetupView
        view = TwitchSetupView()
        embed = await view.build_setup_embed(interaction.guild)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="YouTube-Live", style=discord.ButtonStyle.secondary, row=2)
    async def btn_youtube(self, interaction: discord.Interaction, button: discord.ui.Button):
        from modules.youtube import YoutubeSetupView
        view = YoutubeSetupView()
        embed = await view.build_setup_embed(interaction.guild)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Bot-Name ändern", style=discord.ButtonStyle.secondary, row=2)
    async def btn_bot_name(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BotNameModal())

    @discord.ui.button(label="Aktueller Status", style=discord.ButtonStyle.success, row=2)
    async def btn_check(self, interaction: discord.Interaction, button: discord.ui.Button):
        await shani_status.callback(interaction)

class RoleSetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="👑 Admin-Rolle wählen", row=0)
    async def select_admin(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        role = select.values[0]
        await update_guild_cfg(interaction.guild_id, role_admin_id=role.id)
        await interaction.response.send_message(f"✅ Admin-Rolle auf {role.mention} gesetzt.", ephemeral=True)

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="🛡️ Mod-Rolle wählen", row=1)
    async def select_mod(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        role = select.values[0]
        await update_guild_cfg(interaction.guild_id, role_mod_id=role.id)
        await interaction.response.send_message(f"✅ Mod-Rolle auf {role.mention} gesetzt.", ephemeral=True)

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="📝 Setcard-Rolle (optional)", row=2)
    async def select_setcard(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        role = select.values[0]
        await update_guild_cfg(interaction.guild_id, role_setcard_id=role.id)
        await interaction.response.send_message(f"✅ Setcard-Rolle auf {role.mention} gesetzt.", ephemeral=True)

class SetcardChannelSetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="📁 Setcard-Kanal wählen")
    async def select_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel = select.values[0]
        from modules.setcards import set_setcard_channel
        await set_setcard_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(f"✅ Setcard-Kanal auf {channel.mention} gesetzt.", ephemeral=True)

class AutoVoiceSetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.voice], placeholder="👥 2er Join-Channel wählen", row=0)
    async def select_2(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        await update_guild_cfg(interaction.guild_id, create_channel_id=select.values[0].id)
        await interaction.response.send_message(f"✅ 2er Join-Channel auf {select.values[0].mention} gesetzt.", ephemeral=True)

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.voice], placeholder="👥 3er Join-Channel wählen", row=1)
    async def select_3(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        await update_guild_cfg(interaction.guild_id, create_channel_3_id=select.values[0].id)
        await interaction.response.send_message(f"✅ 3er Join-Channel auf {select.values[0].mention} gesetzt.", ephemeral=True)

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.voice], placeholder="🔓 Open Join-Channel wählen", row=2)
    async def select_open(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        await update_guild_cfg(interaction.guild_id, create_channel_open_id=select.values[0].id)
        await interaction.response.send_message(f"✅ Open Join-Channel auf {select.values[0].mention} gesetzt.", ephemeral=True)

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.category], placeholder="📁 Ziel-Kategorie wählen", row=3)
    async def select_cat(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        await update_guild_cfg(interaction.guild_id, voice_category_id=select.values[0].id)
        await interaction.response.send_message(f"✅ Ziel-Kategorie auf **{select.values[0].name}** gesetzt.", ephemeral=True)

@bot.tree.command(name="shani_setup_roles", description="Legt Admin-, Mod- und Setcard-Rollen fest.")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    admin_role="Rolle für Bot-Administratoren (voller Zugriff)",
    mod_role="Rolle für Moderatoren (Status & Mod-Delete)",
    setcard_role="Optional: Rolle, die Setcards nutzen darf (leer lassen für alle)"
)
async def shani_setup_roles(
    interaction: discord.Interaction,
    admin_role: discord.Role,
    mod_role: discord.Role,
    setcard_role: discord.Role | None = None
):
    await update_guild_cfg(
        interaction.guild_id,
        role_admin_id=admin_role.id,
        role_mod_id=mod_role.id,
        role_setcard_id=setcard_role.id if setcard_role else None
    )
    await interaction.response.send_message(
        f"✅ Rollen konfiguriert:\n"
        f"👑 Admin: {admin_role.mention}\n"
        f"🛡️ Mod: {mod_role.mention}\n"
        f"📝 Setcard: {setcard_role.mention if setcard_role else 'Alle User'}",
        ephemeral=True
    )

@bot.tree.command(name="shani_status", description="Zeigt die gesamte Konfiguration des Bots für diesen Server.")
@app_commands.checks.has_permissions(manage_guild=True)
async def shani_status(interaction: discord.Interaction):
    cfg = await get_guild_cfg(interaction.guild_id)
    if not cfg:
        await interaction.response.send_message("ℹ️ Noch keine Konfiguration für diesen Server vorhanden.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"⚙️ Konfiguration für {interaction.guild.name}",
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc)
    )

    # 🛠️ Setcards
    sc_channel = interaction.guild.get_channel(int(cfg.get("setcard_channel_id", 0))) if cfg.get("setcard_channel_id") else None
    embed.add_field(
        name="🛠️ Setcards",
        value=f"Kanal: {sc_channel.mention if sc_channel else '❌ Nicht gesetzt'}",
        inline=False
    )

    # 🔊 Auto-Voice
    ch2 = interaction.guild.get_channel(int(cfg.get("create_channel_id", 0))) if cfg.get("create_channel_id") else None
    ch3 = interaction.guild.get_channel(int(cfg.get("create_channel_3_id", 0))) if cfg.get("create_channel_3_id") else None
    chO = interaction.guild.get_channel(int(cfg.get("create_channel_open_id", 0))) if cfg.get("create_channel_open_id") else None
    cat = interaction.guild.get_channel(int(cfg.get("voice_category_id", 0))) if cfg.get("voice_category_id") else None
    
    voice_val = "❌ Nicht eingerichtet"
    if ch2 or ch3 or chO or cat:
        voice_val = (
            f"• 2er Join: {ch2.mention if ch2 else '❌'}\n"
            f"• 3er Join: {ch3.mention if ch3 else '❌'}\n"
            f"• Open Join: {chO.mention if chO else '❌'}\n"
            f"• Kategorie: {cat.name if cat else '❌'}"
        )
    embed.add_field(name="🔊 Auto-Voice", value=voice_val, inline=False)

    # 🟣 Twitch
    if cfg.get("twitch_enabled"):
        tw_ch = interaction.guild.get_channel(int(cfg.get("twitch_announce_channel_id", 0))) if cfg.get("twitch_announce_channel_id") else None
        role = interaction.guild.get_role(int(cfg.get("twitch_ping_role_id", 0))) if cfg.get("twitch_ping_role_id") else None
        stable = cfg.get("twitch_stable_checks", 2)
        poll = cfg.get("twitch_poll_seconds", 90)
        grace = int(cfg.get("twitch_offline_grace_seconds", 300)) // 60
        tw_val = (
            f"• Kanal: **{cfg.get('twitch_channel')}**\n"
            f"• Announce: {tw_ch.mention if tw_ch else '❌'}\n"
            f"• Ping: {role.mention if role else '—'}\n"
            f"• Stable: **{stable}** | Poll: **{poll}s** | Grace: **{grace}m**"
        )
    else:
        tw_val = "❌ Deaktiviert"
    embed.add_field(name="🟣 Twitch Live-Alerts", value=tw_val, inline=True)

    # 🔴 YouTube
    if cfg.get("youtube_enabled"):
        yt_ch = interaction.guild.get_channel(int(cfg.get("youtube_announce_channel_id", 0))) if cfg.get("youtube_announce_channel_id") else None
        yrole = interaction.guild.get_role(int(cfg.get("youtube_ping_role_id", 0))) if cfg.get("youtube_ping_role_id") else None
        ystable = cfg.get("youtube_stable_checks", 2)
        ypoll = cfg.get("youtube_poll_seconds", 300)
        ygrace = int(cfg.get("youtube_offline_grace_seconds", 600)) // 60
        yt_val = (
            f"• Kanal: **{cfg.get('youtube_channel')}**\n"
            f"• Announce: {yt_ch.mention if yt_ch else '❌'}\n"
            f"• Ping: {yrole.mention if yrole else '—'}\n"
            f"• Stable: **{ystable}** | Poll: **{ypoll}s** | Grace: **{ygrace}m**"
        )
    else:
        yt_val = "❌ Deaktiviert"
    embed.add_field(name="🔴 YouTube Live-Alerts", value=yt_val, inline=True)

    embed.set_footer(text="Shani Bot Status")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- ERROR HANDLING ---
@setup_autovoice.error
@autovoice_status.error
@autovoice_disable.error
@shani_setup_roles.error
@shani_status.error
async def perms_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "❌ Dafür brauchst du **Server verwalten**."
    else:
        msg = f"⚠️ Fehler: {error}"

    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)

@bot.listen("on_interaction")
async def _dbg_interaction(interaction: discord.Interaction):
    try:
        if interaction.type != discord.InteractionType.application_command:
            return

        data = interaction.data or {}
        root = data.get("name")
        sub = None
        sub2 = None

        opts = data.get("options") or []
        if opts and isinstance(opts, list) and isinstance(opts[0], dict):
            sub = opts[0].get("name")
            sub_opts = opts[0].get("options") or []
            if sub_opts and isinstance(sub_opts, list) and isinstance(sub_opts[0], dict):
                sub2 = sub_opts[0].get("name")

        logger.info(
            f"CMD: root={root} sub={sub} sub2={sub2} "
            f"guild={interaction.guild_id} user={getattr(interaction.user,'id',None)}"
        )
    except Exception as e:
        logger.error(f"INTERACTION DBG failed: {e}")

# ============================================================
# START (Extension-sicher)
# ============================================================
async def main():
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        if bot.http_session:
            asyncio.run(bot.http_session.close())
