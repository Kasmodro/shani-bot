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

# ============================================================
# VOICE CONFIG
# ============================================================
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

# ============================================================
# TWITCH CONFIG (separat, überschreibt VOICE NICHT)
# ============================================================
TWITCH_DEFAULT_POLL_SECONDS = 90

# "Echt beendet" erst wenn der Stream so lange nicht mehr als live gesehen wurde:
# (damit kurze Twitch-Zicker NICHT als Ende zählen -> verhindert mehrere Live-Pings)
TWITCH_OFFLINE_GRACE_SECONDS_DEFAULT = 300  # 5 Minuten

def extract_twitch_channel(value: str) -> str:
    v = value.strip()
    v = v.replace("https://", "").replace("http://", "")
    v = v.replace("www.", "")
    v = v.strip()
    if v.lower().startswith("twitch.tv/"):
        v = v[len("twitch.tv/"):]
    v = v.strip("/")

    if v.startswith("@"):
        v = v[1:]
    if "/" in v:
        v = v.split("/")[0]

    v = re.sub(r"[^a-zA-Z0-9_]", "", v)
    return v.lower()

async def set_twitch_cfg(
    guild_id: int,
    twitch_channel_or_url: str,
    announce_channel_id: int,
    ping_role_id: int | None,
    stable_checks: int,
    poll_seconds: int,
    offline_grace_seconds: int = TWITCH_OFFLINE_GRACE_SECONDS_DEFAULT
) -> None:
    await update_guild_cfg(
        guild_id,
        twitch_enabled=1,
        twitch_channel=extract_twitch_channel(twitch_channel_or_url),
        twitch_announce_channel_id=int(announce_channel_id),
        twitch_ping_role_id=int(ping_role_id) if ping_role_id else None,
        twitch_stable_checks=max(1, int(stable_checks)),
        twitch_poll_seconds=max(30, int(poll_seconds)),
        twitch_offline_grace_seconds=max(0, int(offline_grace_seconds)),
        twitch_last_live_message_id=None,
        twitch_last_check_ts=0.0,
        twitch_last_seen_live_ts=0.0,
        twitch_announced_this_stream=0
    )

async def clear_twitch_cfg(guild_id: int) -> None:
    await clear_guild_cfg_fields(guild_id, [
        "twitch_enabled", "twitch_channel", "twitch_announce_channel_id", "twitch_ping_role_id",
        "twitch_stable_checks", "twitch_poll_seconds", "twitch_offline_grace_seconds",
        "twitch_last_live_message_id", "twitch_last_check_ts", "twitch_last_seen_live_ts",
        "twitch_announced_this_stream"
    ])
    await update_guild_cfg(guild_id, twitch_enabled=0)

async def set_twitch_last_message_id(guild_id: int, message_id: int | None) -> None:
    await update_guild_cfg(guild_id, twitch_last_live_message_id=int(message_id) if message_id else None)

# --- Twitch Runtime State ---
twitch_live_state: dict[int, bool] = {}
twitch_live_hits: dict[int, int] = {}
twitch_off_hits: dict[int, int] = {}
twitch_meta_cache: dict[str, dict] = {}

# ============================================================
# TWITCH: HTML Fetch + Parse (no API)
# ============================================================
async def fetch_twitch_page(session: aiohttp.ClientSession, twitch_channel: str):
    url = f"https://www.twitch.tv/{twitch_channel}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.google.com/"
    }
    try:
        async with session.get(url, headers=headers, timeout=15) as resp:
            if resp.status == 200:
                return await resp.text()
            return None
    except Exception as e:
        logger.error(f"fetch_twitch_page error for {twitch_channel}: {e}")
        return None

def parse_twitch_meta(html_text: str) -> dict:
    """
    Streng:
    - LIVE nur wenn explizit true
    - OFFLINE wenn explizit false oder wenn gar nichts gefunden wurde
    """
    meta = {"is_live": False, "avatar": None, "game": None, "title": None}

    # explizit false -> offline
    if re.search(r'"isLiveBroadcast"\s*:\s*false', html_text, re.IGNORECASE) or re.search(r'"isLive"\s*:\s*false', html_text, re.IGNORECASE):
        meta["is_live"] = False
    # explizit true -> live
    elif re.search(r'"isLiveBroadcast"\s*:\s*true', html_text, re.IGNORECASE) or re.search(r'"isLive"\s*:\s*true', html_text, re.IGNORECASE):
        meta["is_live"] = True
    else:
        meta["is_live"] = False

    m = re.search(r'"profileImageURL"\s*:\s*"([^"]+)"', html_text, re.IGNORECASE)
    if m:
        meta["avatar"] = m.group(1).replace("\\/", "/")

    t = re.search(r'"title"\s*:\s*"([^"]+)"', html_text, re.IGNORECASE)
    if t:
        meta["title"] = html.unescape(t.group(1))

    g = re.search(r'"gameName"\s*:\s*"([^"]+)"', html_text, re.IGNORECASE)
    if g:
        meta["game"] = html.unescape(g.group(1))

    return meta

async def get_twitch_meta(session: aiohttp.ClientSession, twitch_channel: str) -> dict:
    status, page = await fetch_twitch_page(session, twitch_channel)
    if status != 200:
        cached = twitch_meta_cache.get(twitch_channel, {}).copy()
        cached.setdefault("is_live", False)
        cached["is_live"] = False
        return cached

    meta = parse_twitch_meta(page)
    twitch_meta_cache[twitch_channel] = meta
    return meta

# ============================================================
# TWITCH: Embeds + Button
# ============================================================
def build_watch_view(twitch_channel: str) -> discord.ui.View:
    url = f"https://www.twitch.tv/{twitch_channel}"
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Jetzt ansehen", style=discord.ButtonStyle.link, url=url))
    return view

def build_live_embed(twitch_channel: str, meta: dict) -> discord.Embed:
    e = discord.Embed(
        title=f"🔴 {twitch_channel.upper()} ist jetzt LIVE!",
        description=f"**Kommt rein:** https://twitch.tv/{twitch_channel}",
        timestamp=datetime.now(timezone.utc)
    )
    if meta.get("title"):
        e.add_field(name="Titel", value=meta["title"], inline=False)
    if meta.get("game"):
        e.add_field(name="Spielt gerade", value=meta["game"], inline=True)
    if meta.get("avatar"):
        e.set_thumbnail(url=meta["avatar"])
    e.set_footer(text="Raiders Cache • Twitch Alert")
    return e

def build_offline_embed(twitch_channel: str, meta: dict) -> discord.Embed:
    e = discord.Embed(
        title=f"⚫ {twitch_channel.upper()} ist jetzt OFFLINE",
        description="Stream ist beendet. Danke fürs Zuschauen ❤️",
        timestamp=datetime.now(timezone.utc)
    )
    if meta.get("avatar"):
        e.set_thumbnail(url=meta["avatar"])
    e.set_footer(text="Raiders Cache • Twitch Alert")
    return e

async def resolve_announce_channel(guild: discord.Guild, cfg: dict) -> discord.TextChannel | None:
    ch_id = int(cfg.get("twitch_announce_channel_id", 0))
    ch = guild.get_channel(ch_id)
    return ch if isinstance(ch, discord.TextChannel) else None

async def post_live(guild: discord.Guild, cfg: dict, meta: dict) -> None:
    text_channel = await resolve_announce_channel(guild, cfg)
    if not text_channel:
        print(f"⚠️ [{guild.name}] Twitch announce channel fehlt/ungültig.")
        return

    twitch_channel = cfg["twitch_channel"]
    role_id = cfg.get("twitch_ping_role_id")

    mention = None
    if role_id:
        role = guild.get_role(int(role_id))
        if role:
            mention = role.mention

    msg = await text_channel.send(
        content=mention,
        embed=build_live_embed(twitch_channel, meta),
        view=build_watch_view(twitch_channel)
    )
    set_twitch_last_message_id(guild.id, msg.id)

async def edit_to_offline(guild: discord.Guild, cfg: dict, meta: dict) -> None:
    text_channel = await resolve_announce_channel(guild, cfg)
    if not text_channel:
        return

    last_id = cfg.get("twitch_last_live_message_id")
    if not last_id:
        return

    try:
        msg = await text_channel.fetch_message(int(last_id))
        await msg.edit(content=None, embed=build_offline_embed(cfg["twitch_channel"], meta), view=None)
    except Exception as e:
        print(f"⚠️ [{guild.name}] OFFLINE edit failed: {e}")

# ============================================================
# TWITCH LOOP (global tick, per-guild rate aus config!)
# ============================================================
@tasks.loop(seconds=30)
async def twitch_loop():
    if not bot.http_session:
        bot.http_session = aiohttp.ClientSession()

    for guild in bot.guilds:
        cfg = await get_guild_cfg(guild.id)
        if not cfg.get("twitch_enabled"):
            continue
        if "twitch_channel" not in cfg or "twitch_announce_channel_id" not in cfg:
            continue

        poll_seconds = int(cfg.get("twitch_poll_seconds", TWITCH_DEFAULT_POLL_SECONDS))
        last_check = float(cfg.get("twitch_last_check_ts", 0.0))
        now = time.time()
        if (now - last_check) < poll_seconds:
            continue
        
        await update_guild_cfg(guild.id, twitch_last_check_ts=now)

        stable = int(cfg.get("twitch_stable_checks", 2))
        offline_grace = int(cfg.get("twitch_offline_grace_seconds", TWITCH_OFFLINE_GRACE_SECONDS_DEFAULT))

        twitch_channel = cfg["twitch_channel"]

        try:
            html_text = await fetch_twitch_page(bot.http_session, twitch_channel)
            if html_text is None:
                continue
            meta = parse_twitch_meta(html_text)
        except Exception as e:
            logger.error(f"[{guild.name}] Twitch fetch error: {e}")
            continue

        live_now = bool(meta.get("live", False))
        prev_live = twitch_live_state.get(guild.id, False)

        # hits zählen (Stabil)
        if live_now:
            twitch_live_hits[guild.id] = twitch_live_hits.get(guild.id, 0) + 1
            twitch_off_hits[guild.id] = 0
        else:
            twitch_off_hits[guild.id] = twitch_off_hits.get(guild.id, 0) + 1
            twitch_live_hits[guild.id] = 0

        # last seen live timestamp pflegen (für "echtes Ende")
        if live_now:
            await update_guild_cfg(guild.id, twitch_last_seen_live_ts=now)

        announced = bool(cfg.get("twitch_announced_this_stream", False))
        last_seen_live_ts = float(cfg.get("twitch_last_seen_live_ts", 0.0))

        # ====================================================
        # OFFLINE -> LIVE (NUR EINMAL PRO STREAM POSTEN)
        # ====================================================
        if (not announced) and (not prev_live) and live_now and twitch_live_hits[guild.id] >= stable:
            twitch_live_state[guild.id] = True
            await post_live(guild, cfg, meta)
            await update_guild_cfg(guild.id, twitch_announced_this_stream=1)
            continue

        # wenn schon announced, setzen wir live_state einfach korrekt,
        # aber posten NICHT nochmal
        if announced and live_now:
            twitch_live_state[guild.id] = True

        # ====================================================
        # LIVE -> OFFLINE (Stream gilt nur als "beendet", wenn
        # offline stabil UND seit "last_seen_live" genug Zeit rum ist)
        # ====================================================
        # Damit verhindern wir Mehrfach-LIVE bei kurzen Aussetzern.
        if announced and prev_live and (not live_now) and twitch_off_hits[guild.id] >= stable:
            offline_duration = now - last_seen_live_ts
            if offline_duration >= offline_grace:
                twitch_live_state[guild.id] = False
                await edit_to_offline(guild, cfg, meta)

                # RESET -> nächster Stream darf wieder EIN Live-Ping schicken
                await update_guild_cfg(guild.id, twitch_announced_this_stream=0)
            else:
                # noch nicht "echt beendet" -> ignorieren
                pass

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
            logger.info("Setcards Modul geladen")
    except Exception as e:
        logger.error(f"Setcards Modul konnte nicht geladen werden: {e}")

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

    for g in bot.guilds:
        twitch_live_state.setdefault(g.id, False)
        twitch_live_hits.setdefault(g.id, 0)
        twitch_off_hits.setdefault(g.id, 0)

    if not twitch_loop.is_running():
        twitch_loop.start()
        logger.info("Twitch loop running (tick=30s, per-guild poll from config)")

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
        category = member.guild.get_channel(int(category_id))
        if not isinstance(category, discord.CategoryChannel):
            logger.error(f"[{member.guild.name}] Ziel-Kategorie fehlt/ungültig (ID={category_id}).")
            return

        try:
            channel = await member.guild.create_voice_channel(
                name=squad_channel_name(member, target_limit),
                category=category,
                user_limit=target_limit
            )
            logger.info(f"➕ [{member.guild.name}] Created {target_limit if target_limit > 0 else 'Open'}: {channel.name} (owner={member.display_name})")
        except Exception as e:
            logger.error(f"[{member.guild.name}] Error creating voice channel: {e}")
            return

        try:
            await channel.set_permissions(
                member,
                manage_channels=False, # User darf das Limit/Namen NICHT selbst ändern
                set_voice_channel_status=True, # User darf aber den Status (z.B. "Looten") setzen
                move_members=True,     # User darf weiterhin Leute mufen/kicken
                connect=True,
                speak=True
            )
            await member.move_to(channel)
        except Exception as e:
            logger.error(f"[{member.guild.name}] Error moving member to new channel: {e}")

    # --- Löschen ---
    if before.channel:
        # Check if it was a join channel
        is_join_2 = create_id_2 and before.channel.id == int(create_id_2)
        is_join_3 = create_id_3 and before.channel.id == int(create_id_3)
        is_join_open = create_id_open and before.channel.id == int(create_id_open)
        
        if not is_join_2 and not is_join_3 and not is_join_open:
            if before.channel.category_id == int(category_id):
                if before.channel.name.startswith("Squad ") and len(before.channel.members) == 0:
                    try:
                        await before.channel.delete()
                        logger.info(f"🗑️ [{member.guild.name}] Deleted empty squad channel: {before.channel.name}")
                    except discord.NotFound:
                        pass
                    except Exception as e:
                        logger.error(f"[{member.guild.name}] Error deleting voice channel: {e}")

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
@app_commands.checks.has_permissions(manage_guild=True)
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
@app_commands.checks.has_permissions(manage_guild=True)
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
# GLOBAL STATUS COMMAND
# ============================================================
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
        tw_val = (
            f"• Kanal: **{cfg.get('twitch_channel')}**\n"
            f"• Announce: {tw_ch.mention if tw_ch else '❌'}\n"
            f"• Ping: {role.mention if role else '—'}"
        )
    else:
        tw_val = "❌ Deaktiviert"
    embed.add_field(name="🟣 Twitch Live-Alerts", value=tw_val, inline=False)

    embed.set_footer(text="Shani Bot Status")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ============================================================
# SLASH COMMANDS: TWITCH (NEU: setup_twitchlive2 ohne Cooldown)
# ============================================================
@bot.tree.command(name="setup_twitchlive2", description="Twitch Live Alerts ohne API: genau 1 Live-Ping pro Stream.")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    twitch_channel_or_url="z.B. shordje ODER https://twitch.tv/shordje",
    announce_channel="Textkanal für Live-Meldungen",
    ping_role="Optional: Rolle die beim Live-Start gepingt wird",
    stable_checks="Anti-Zick: wie viele gleiche Ergebnisse nötig (empfohlen 2)",
    poll_seconds="Abfragerate in Sekunden (min 30, empfohlen 90)",
    offline_grace_minutes="Wie lange muss er offline sein, bis es als 'Stream beendet' gilt (Standard 5)"
)
async def setup_twitchlive2(
    interaction: discord.Interaction,
    twitch_channel_or_url: str,
    announce_channel: discord.TextChannel,
    ping_role: discord.Role | None = None,
    stable_checks: app_commands.Range[int, 1, 5] = 2,
    poll_seconds: app_commands.Range[int, 30, 600] = 90,
    offline_grace_minutes: app_commands.Range[int, 0, 60] = 5
):
    await set_twitch_cfg(
        interaction.guild_id,
        twitch_channel_or_url,
        announce_channel.id,
        ping_role.id if ping_role else None,
        stable_checks=int(stable_checks),
        poll_seconds=int(poll_seconds),
        offline_grace_seconds=int(offline_grace_minutes) * 60
    )

    # runtime reset
    gid = int(interaction.guild_id)
    twitch_live_state[gid] = False
    twitch_live_hits[gid] = 0
    twitch_off_hits[gid] = 0

    await interaction.response.send_message(
        "✅ Twitch Live-Alerts aktiviert (1 Live-Ping pro Stream).\n"
        f"🟣 Twitch: **{extract_twitch_channel(twitch_channel_or_url)}**\n"
        f"📢 Kanal: **#{announce_channel.name}**\n"
        f"{'🏷️ Ping: ' + ping_role.mention if ping_role else '🏷️ Ping: (keiner)'}\n"
        f"🔇 Stabil: **{stable_checks}** | ⏲️ Poll: **{poll_seconds}s** | 🧊 Offline-Grace: **{offline_grace_minutes} min**\n"
        f"📌 OFFLINE: **LIVE-Post wird erst nach echtem Ende editiert**",
        ephemeral=True
    )

@bot.tree.command(name="twitchlive_status", description="Zeigt Twitch-Konfiguration + aktuellen Status.")
@app_commands.checks.has_permissions(manage_guild=True)
async def twitchlive_status(interaction: discord.Interaction):
    cfg = await get_guild_cfg(interaction.guild_id)
    if not cfg or not cfg.get("twitch_enabled"):
        await interaction.response.send_message("ℹ️ Twitch Live-Alerts sind nicht aktiviert.", ephemeral=True)
        return

    announce_ch = interaction.guild.get_channel(int(cfg.get("twitch_announce_channel_id", 0)))
    role = interaction.guild.get_role(int(cfg["twitch_ping_role_id"])) if cfg.get("twitch_ping_role_id") else None

    await interaction.response.send_message(
        "✅ Twitch Status:\n"
        f"🟣 Twitch: **{cfg.get('twitch_channel')}**\n"
        f"📢 Kanal: **{('#' + announce_ch.name) if announce_ch else 'FEHLT (gelöscht?)'}**\n"
        f"🏷️ Ping: **{role.name if role else '—'}**\n"
        f"🔇 Stable: **{cfg.get('twitch_stable_checks', 2)}** | ⏲️ Poll: **{cfg.get('twitch_poll_seconds', 90)}s** | 🧊 Offline-Grace: **{int(cfg.get('twitch_offline_grace_seconds', 300))//60} min**\n"
        f"🧾 Last message id: **{cfg.get('twitch_last_live_message_id') or '—'}**\n"
        f"📣 Announced this stream: **{bool(cfg.get('twitch_announced_this_stream', False))}**\n"
        f"🔴 Live-State (intern): **{'LIVE' if twitch_live_state.get(int(interaction.guild_id), False) else 'OFFLINE'}**",
        ephemeral=True
    )

@bot.tree.command(name="twitchlive_set_poll", description="Ändert die Abfragerate (Polling) für Twitch.")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(poll_seconds="Neue Abfragerate in Sekunden (min 30, empfohlen 90)")
async def twitchlive_set_poll(interaction: discord.Interaction, poll_seconds: app_commands.Range[int, 30, 600] = 90):
    cfg = await get_guild_cfg(interaction.guild_id)
    if not cfg.get("twitch_enabled"):
        await interaction.response.send_message("ℹ️ Twitch Live-Alerts sind nicht aktiviert.", ephemeral=True)
        return
    await update_guild_cfg(interaction.guild_id, twitch_poll_seconds=int(poll_seconds))
    await interaction.response.send_message(f"✅ Polling-Rate gesetzt auf **{poll_seconds}s**.", ephemeral=True)

@bot.tree.command(name="twitchlive_test", description="Testet LIVE-Embed (funktioniert immer, auch wenn offline).")
@app_commands.checks.has_permissions(manage_guild=True)
async def twitchlive_test(interaction: discord.Interaction):
    cfg = await get_guild_cfg(interaction.guild_id)
    if not cfg.get("twitch_enabled"):
        await interaction.response.send_message("ℹ️ Erst /setup_twitchlive2 ausführen.", ephemeral=True)
        return

    guild = interaction.guild
    ch = await resolve_announce_channel(guild, cfg)
    if not ch:
        await interaction.response.send_message("❌ Announce-Channel fehlt/ungültig.", ephemeral=True)
        return

    meta = {
        "title": "Test-Stream (nur Bot-Test)",
        "game": "ARC Raiders",
        "avatar": (twitch_meta_cache.get(cfg["twitch_channel"], {}) or {}).get("avatar")
    }
    msg = await ch.send(embed=build_live_embed(cfg["twitch_channel"], meta), view=build_watch_view(cfg["twitch_channel"]))
    await set_twitch_last_message_id(guild.id, msg.id)

    # Test soll NICHT deine "1 pro Stream"-Logik kaputt machen -> Flag NICHT setzen.
    await interaction.response.send_message("🧪 Test gesendet (LIVE-Embed + Button).", ephemeral=True)

@bot.tree.command(name="twitchoffline_test", description="Testet OFFLINE-Edit (editiert den letzten LIVE-Post).")
@app_commands.checks.has_permissions(manage_guild=True)
async def twitchoffline_test(interaction: discord.Interaction):
    cfg = await get_guild_cfg(interaction.guild_id)
    if not cfg.get("twitch_enabled"):
        await interaction.response.send_message("ℹ️ Erst /setup_twitchlive2 ausführen.", ephemeral=True)
        return

    guild = interaction.guild
    meta = {"avatar": (twitch_meta_cache.get(cfg["twitch_channel"], {}) or {}).get("avatar")}
    await edit_to_offline(guild, cfg, meta)
    await interaction.response.send_message("🧪 OFFLINE-Edit versucht (siehe #live).", ephemeral=True)

@bot.tree.command(name="twitchlive_disable", description="Deaktiviert Twitch Live-Alerts (Voice bleibt unangetastet!).")
@app_commands.checks.has_permissions(manage_guild=True)
async def twitchlive_disable(interaction: discord.Interaction):
    await clear_twitch_cfg(interaction.guild_id)
    gid = int(interaction.guild_id)
    twitch_live_state[gid] = False
    twitch_live_hits[gid] = 0
    twitch_off_hits[gid] = 0
    await interaction.response.send_message("🛑 Twitch Live-Alerts wurden deaktiviert. (Auto-Voice bleibt aktiv)", ephemeral=True)

# --- ERROR HANDLING (Voice + Twitch) ---
@setup_autovoice.error
@autovoice_status.error
@autovoice_disable.error
@setup_twitchlive2.error
@twitchlive_status.error
@twitchlive_set_poll.error
@twitchlive_test.error
@twitchoffline_test.error
@twitchlive_disable.error
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
