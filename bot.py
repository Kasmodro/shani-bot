import os
import json
import re
import time
import html
import discord
import aiohttp
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
from datetime import datetime, timezone

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
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ============================================================
# CONFIG HELPERS (wichtig: NICHT mehr überschreiben/clear alles)
# ============================================================
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

def ensure_guild_cfg(guild_id: int) -> dict:
    gid = str(guild_id)
    if gid not in GUILD_CONFIG or not isinstance(GUILD_CONFIG.get(gid), dict):
        GUILD_CONFIG[gid] = {}
    return GUILD_CONFIG[gid]

def get_guild_cfg(guild_id: int) -> dict | None:
    return GUILD_CONFIG.get(str(guild_id))

# ============================================================
# VOICE CONFIG (EXAKT wie dein Script)
# ============================================================
def set_guild_voice_cfg(guild_id: int, create_channel_id: int, voice_category_id: int) -> None:
    cfg = ensure_guild_cfg(guild_id)
    cfg["create_channel_id"] = int(create_channel_id)
    cfg["voice_category_id"] = int(voice_category_id)
    save_config(GUILD_CONFIG)

def clear_guild_voice_cfg(guild_id: int) -> None:
    cfg = ensure_guild_cfg(guild_id)
    cfg.pop("create_channel_id", None)
    cfg.pop("voice_category_id", None)
    save_config(GUILD_CONFIG)

def squad_channel_name(member: discord.Member) -> str:
    return f"Squad {member.display_name}"

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

def set_twitch_cfg(
    guild_id: int,
    twitch_channel_or_url: str,
    announce_channel_id: int,
    ping_role_id: int | None,
    stable_checks: int,
    poll_seconds: int,
    offline_grace_seconds: int = TWITCH_OFFLINE_GRACE_SECONDS_DEFAULT
) -> None:
    cfg = ensure_guild_cfg(guild_id)
    cfg["twitch_enabled"] = True
    cfg["twitch_channel"] = extract_twitch_channel(twitch_channel_or_url)
    cfg["twitch_announce_channel_id"] = int(announce_channel_id)

    if ping_role_id:
        cfg["twitch_ping_role_id"] = int(ping_role_id)
    else:
        cfg.pop("twitch_ping_role_id", None)

    cfg["twitch_stable_checks"] = max(1, int(stable_checks))
    cfg["twitch_poll_seconds"] = max(30, int(poll_seconds))  # Schutz
    cfg["twitch_offline_grace_seconds"] = max(0, int(offline_grace_seconds))

    # persistente States, damit Restart sauber bleibt
    cfg.setdefault("twitch_last_live_message_id", None)
    cfg.setdefault("twitch_last_check_ts", 0.0)
    cfg.setdefault("twitch_last_seen_live_ts", 0.0)
    cfg.setdefault("twitch_announced_this_stream", False)

    save_config(GUILD_CONFIG)

def clear_twitch_cfg(guild_id: int) -> None:
    cfg = ensure_guild_cfg(guild_id)
    # NUR Twitch-Keys löschen, NICHT Voice!
    for k in [
        "twitch_enabled", "twitch_channel", "twitch_announce_channel_id", "twitch_ping_role_id",
        "twitch_stable_checks", "twitch_poll_seconds", "twitch_offline_grace_seconds",
        "twitch_last_live_message_id", "twitch_last_check_ts", "twitch_last_seen_live_ts",
        "twitch_announced_this_stream"
    ]:
        cfg.pop(k, None)
    save_config(GUILD_CONFIG)

def set_twitch_last_message_id(guild_id: int, message_id: int | None) -> None:
    cfg = ensure_guild_cfg(guild_id)
    cfg["twitch_last_live_message_id"] = int(message_id) if message_id else None
    save_config(GUILD_CONFIG)

# --- Twitch Runtime State ---
twitch_live_state: dict[int, bool] = {}
twitch_live_hits: dict[int, int] = {}
twitch_off_hits: dict[int, int] = {}
twitch_meta_cache: dict[str, dict] = {}

# ============================================================
# TWITCH: HTML Fetch + Parse (no API)
# ============================================================
async def fetch_twitch_page(session: aiohttp.ClientSession, twitch_channel: str) -> tuple[int, str]:
    url = f"https://www.twitch.tv/{twitch_channel}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ShaniBot/1.0)"}
    async with session.get(url, headers=headers, timeout=20) as resp:
        return resp.status, await resp.text()

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
    async with aiohttp.ClientSession() as session:
        for guild in bot.guilds:
            cfg = get_guild_cfg(guild.id) or {}
            if not cfg.get("twitch_enabled"):
                continue
            if "twitch_channel" not in cfg or "twitch_announce_channel_id" not in cfg:
                continue

            poll_seconds = int(cfg.get("twitch_poll_seconds", TWITCH_DEFAULT_POLL_SECONDS))
            last_check = float(cfg.get("twitch_last_check_ts", 0.0))
            now = time.time()
            if (now - last_check) < poll_seconds:
                continue
            cfg["twitch_last_check_ts"] = now
            save_config(GUILD_CONFIG)

            stable = int(cfg.get("twitch_stable_checks", 2))
            offline_grace = int(cfg.get("twitch_offline_grace_seconds", TWITCH_OFFLINE_GRACE_SECONDS_DEFAULT))

            twitch_channel = cfg["twitch_channel"]

            try:
                meta = await get_twitch_meta(session, twitch_channel)
            except Exception as e:
                print(f"⚠️ [{guild.name}] Twitch fetch error: {e}")
                continue

            live_now = bool(meta.get("is_live", False))
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
                cfg["twitch_last_seen_live_ts"] = now
                save_config(GUILD_CONFIG)

            announced = bool(cfg.get("twitch_announced_this_stream", False))
            last_seen_live_ts = float(cfg.get("twitch_last_seen_live_ts", 0.0))

            # ====================================================
            # OFFLINE -> LIVE (NUR EINMAL PRO STREAM POSTEN)
            # ====================================================
            if (not announced) and (not prev_live) and live_now and twitch_live_hits[guild.id] >= stable:
                twitch_live_state[guild.id] = True
                await post_live(guild, cfg, meta)
                cfg["twitch_announced_this_stream"] = True
                save_config(GUILD_CONFIG)
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
                    cfg["twitch_announced_this_stream"] = False
                    save_config(GUILD_CONFIG)
                else:
                    # noch nicht "echt beendet" -> ignorieren
                    pass

# ============================================================
# EVENTS
# ============================================================
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"✅ Slash Commands synced: {len(synced)}")
    except Exception as e:
        print(f"⚠️ Slash Sync failed: {e}")

    print(f"🤖 Shani ist online als {bot.user}")

    for g in bot.guilds:
        twitch_live_state.setdefault(g.id, False)
        twitch_live_hits.setdefault(g.id, 0)
        twitch_off_hits.setdefault(g.id, 0)

    if not twitch_loop.is_running():
        twitch_loop.start()
        print("🟣 Twitch loop running (tick=30s, per-guild poll from config)")

# ============================================================
# VOICE EVENT (1:1 aus deinem Script)
# ============================================================
@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    cfg = get_guild_cfg(member.guild.id)
    if not cfg:
        return

    if "create_channel_id" not in cfg or "voice_category_id" not in cfg:
        return

    create_id = int(cfg["create_channel_id"])
    category_id = int(cfg["voice_category_id"])

    if after.channel and after.channel.id == create_id:
        category = member.guild.get_channel(category_id)
        if not isinstance(category, discord.CategoryChannel):
            print(f"❌ [{member.guild.name}] Ziel-Kategorie fehlt/ungültig (ID={category_id}).")
            return

        try:
            channel = await member.guild.create_voice_channel(
                name=squad_channel_name(member),
                category=category
            )
            print(f"➕ [{member.guild.name}] Created: {channel.name} (owner={member.display_name})")
        except discord.Forbidden as e:
            print(f"❌ [{member.guild.name}] Forbidden: create_voice_channel | {e}")
            return
        except discord.HTTPException as e:
            print(f"❌ [{member.guild.name}] HTTPException: create_voice_channel | {e}")
            return

        try:
            await channel.set_permissions(
                member,
                manage_channels=True,
                move_members=True,
                connect=True,
                speak=True
            )
            print(f"✅ [{member.guild.name}] Permissions set for owner={member.display_name} on {channel.name}")
        except discord.Forbidden as e:
            print(f"❌ [{member.guild.name}] Forbidden: set_permissions | {e}")
        except discord.HTTPException as e:
            print(f"❌ [{member.guild.name}] HTTPException: set_permissions | {e}")

        try:
            await member.move_to(channel)
            print(f"➡️ [{member.guild.name}] Moved {member.display_name} -> {channel.name}")
        except discord.Forbidden as e:
            print(f"❌ [{member.guild.name}] Forbidden: move_to | {e}")
        except discord.HTTPException as e:
            print(f"❌ [{member.guild.name}] HTTPException: move_to | {e}")

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
                    print(f"❌ [{member.guild.name}] Forbidden: rename channel | {e}")
                except discord.NotFound:
                    print(f"ℹ️ [{member.guild.name}] Rename skipped: channel already gone (404)")
                except discord.HTTPException as e:
                    print(f"❌ [{member.guild.name}] HTTPException: rename channel | {e}")

    if before.channel:
        ch = before.channel

        if ch.id == create_id:
            return

        if ch.category and ch.category.id == category_id and len(ch.members) == 0:
            try:
                name = ch.name
                await ch.delete()
                print(f"🗑️ [{member.guild.name}] Deleted empty channel: {name}")
            except discord.NotFound:
                print(f"ℹ️ [{member.guild.name}] Delete skipped: channel already gone (404)")
            except discord.Forbidden as e:
                print(f"❌ [{member.guild.name}] Forbidden: delete channel | {e}")
            except discord.HTTPException as e:
                print(f"❌ [{member.guild.name}] HTTPException: delete channel | {e}")

# ============================================================
# SLASH COMMANDS: VOICE (1:1)
# ============================================================
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
    set_guild_voice_cfg(interaction.guild_id, create_channel.id, target_category.id)
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
    if not cfg or "create_channel_id" not in cfg or "voice_category_id" not in cfg:
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
    clear_guild_voice_cfg(interaction.guild_id)
    await interaction.response.send_message("🛑 Auto-Voice wurde deaktiviert.", ephemeral=True)

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
    set_twitch_cfg(
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
    cfg = get_guild_cfg(interaction.guild_id) or {}
    if not cfg.get("twitch_enabled"):
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
    cfg = ensure_guild_cfg(interaction.guild_id)
    if not cfg.get("twitch_enabled"):
        await interaction.response.send_message("ℹ️ Twitch Live-Alerts sind nicht aktiviert.", ephemeral=True)
        return
    cfg["twitch_poll_seconds"] = int(poll_seconds)
    save_config(GUILD_CONFIG)
    await interaction.response.send_message(f"✅ Polling-Rate gesetzt auf **{poll_seconds}s**.", ephemeral=True)

@bot.tree.command(name="twitchlive_test", description="Testet LIVE-Embed (funktioniert immer, auch wenn offline).")
@app_commands.checks.has_permissions(manage_guild=True)
async def twitchlive_test(interaction: discord.Interaction):
    cfg = get_guild_cfg(interaction.guild_id) or {}
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
    set_twitch_last_message_id(guild.id, msg.id)

    # Test soll NICHT deine "1 pro Stream"-Logik kaputt machen -> Flag NICHT setzen.
    await interaction.response.send_message("🧪 Test gesendet (LIVE-Embed + Button).", ephemeral=True)

@bot.tree.command(name="twitchoffline_test", description="Testet OFFLINE-Edit (editiert den letzten LIVE-Post).")
@app_commands.checks.has_permissions(manage_guild=True)
async def twitchoffline_test(interaction: discord.Interaction):
    cfg = get_guild_cfg(interaction.guild_id) or {}
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
    clear_twitch_cfg(interaction.guild_id)
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

bot.run(TOKEN)
