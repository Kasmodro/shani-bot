import re
import html
import time
import logging
import aiohttp
import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timezone

logger = logging.getLogger("shani-bot")

# ============================================================
# TWITCH CONFIG (separat, überschreibt VOICE NICHT)
# ============================================================
TWITCH_DEFAULT_POLL_SECONDS = 90
TWITCH_OFFLINE_GRACE_SECONDS_DEFAULT = 300  # 5 Minuten

# --- Twitch Runtime State ---
twitch_live_state: dict[int, bool] = {}
twitch_live_hits: dict[int, int] = {}
twitch_off_hits: dict[int, int] = {}
twitch_meta_cache: dict[str, dict] = {}

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
    meta = {"is_live": False, "avatar": None, "game": None, "title": None}

    if re.search(r'"isLiveBroadcast"\s*:\s*false', html_text, re.IGNORECASE) or re.search(r'"isLive"\s*:\s*false', html_text, re.IGNORECASE):
        meta["is_live"] = False
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

class TwitchChannelModal(discord.ui.Modal, title="Twitch Kanal festlegen"):
    twitch_input = discord.ui.TextInput(
        label="Twitch Kanal-Name oder URL",
        placeholder="z.B. shordje oder https://twitch.tv/shordje",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        channel = extract_twitch_channel(self.twitch_input.value)
        from bot import update_guild_cfg
        await update_guild_cfg(interaction.guild_id, twitch_channel=channel)
        await interaction.response.send_message(f"✅ Twitch-Kanal auf **{channel}** gesetzt.", ephemeral=True)

class TwitchSetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @staticmethod
    async def build_setup_embed(guild: discord.Guild):
        from bot import get_guild_cfg
        cfg = await get_guild_cfg(guild.id)
        
        embed = discord.Embed(
            title="🟣 Twitch-Live Setup",
            description="Konfiguriere den Twitch-Kanal und die Benachrichtigungen.",
            color=discord.Color.purple()
        )
        
        if cfg.get("twitch_enabled"):
            ch_id = cfg.get("twitch_announce_channel_id")
            ch = guild.get_channel(int(ch_id)) if ch_id else None
            role_id = cfg.get("twitch_ping_role_id")
            role = guild.get_role(int(role_id)) if role_id else None
            
            stable = cfg.get("twitch_stable_checks", 2)
            poll = cfg.get("twitch_poll_seconds", 90)
            grace = int(cfg.get("twitch_offline_grace_seconds", 300)) // 60
            
            status_text = (
                f"✅ **Aktiviert**\n"
                f"• Kanal: **{cfg.get('twitch_channel', '—')}**\n"
                f"• Announce: {ch.mention if ch else '❌'}\n"
                f"• Ping: {role.mention if role else '—'}\n"
                f"• Stable: **{stable}** | Poll: **{poll}s** | Grace: **{grace}m**"
            )
        else:
            status_text = "❌ **Deaktiviert**"
            
        embed.add_field(name="Aktueller Status", value=status_text, inline=False)
        return embed

    async def _update_embed(self, interaction: discord.Interaction):
        embed = await self.build_setup_embed(interaction.guild)
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="Twitch-Kanal setzen", style=discord.ButtonStyle.primary, row=0)
    async def btn_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TwitchChannelModal())

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="📢 Ankündigungs-Kanal wählen", row=1)
    async def select_announce(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        from bot import update_guild_cfg
        await update_guild_cfg(interaction.guild_id, twitch_announce_channel_id=select.values[0].id)
        await interaction.response.send_message(f"✅ Ankündigungs-Kanal auf {select.values[0].mention} gesetzt.", ephemeral=True)
        await self._update_embed(interaction)

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="🔔 Ping-Rolle wählen (optional)", row=2)
    async def select_ping(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        from bot import update_guild_cfg
        role = select.values[0]
        await update_guild_cfg(interaction.guild_id, twitch_ping_role_id=role.id)
        await interaction.response.send_message(f"✅ Ping-Rolle auf {role.mention} gesetzt.", ephemeral=True)
        await self._update_embed(interaction)

    @discord.ui.button(label="Aktivieren", style=discord.ButtonStyle.success, row=3)
    async def btn_enable(self, interaction: discord.Interaction, button: discord.ui.Button):
        from bot import update_guild_cfg
        await update_guild_cfg(interaction.guild_id, twitch_enabled=1)
        await interaction.response.send_message("✅ Twitch-Live Benachrichtigungen wurden aktiviert.", ephemeral=True)
        await self._update_embed(interaction)

    @discord.ui.button(label="Deaktivieren", style=discord.ButtonStyle.danger, row=3)
    async def btn_disable(self, interaction: discord.Interaction, button: discord.ui.Button):
        from bot import update_guild_cfg
        await update_guild_cfg(interaction.guild_id, twitch_enabled=0)
        await interaction.response.send_message("🛑 Twitch-Live Benachrichtigungen wurden deaktiviert.", ephemeral=True)
        await self._update_embed(interaction)

class TwitchCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.twitch_loop.start()

    def cog_unload(self):
        self.twitch_loop.cancel()

    @tasks.loop(seconds=30)
    async def twitch_loop(self):
        if not self.bot.http_session:
            self.bot.http_session = aiohttp.ClientSession()

        from bot import get_guild_cfg, update_guild_cfg

        for guild in self.bot.guilds:
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
                html_text = await fetch_twitch_page(self.bot.http_session, twitch_channel)
                if html_text is None:
                    continue
                meta = parse_twitch_meta(html_text)
            except Exception as e:
                logger.error(f"[{guild.name}] Twitch fetch error: {e}")
                continue

            live_now = bool(meta.get("is_live", False))
            prev_live = twitch_live_state.get(guild.id, False)

            if live_now:
                twitch_live_hits[guild.id] = twitch_live_hits.get(guild.id, 0) + 1
                twitch_off_hits[guild.id] = 0
            else:
                twitch_off_hits[guild.id] = twitch_off_hits.get(guild.id, 0) + 1
                twitch_live_hits[guild.id] = 0

            if live_now:
                await update_guild_cfg(guild.id, twitch_last_seen_live_ts=now)

            announced = bool(cfg.get("twitch_announced_this_stream", False))
            last_seen_live_ts = float(cfg.get("twitch_last_seen_live_ts", 0.0))

            if (not announced) and (not prev_live) and live_now and twitch_live_hits[guild.id] >= stable:
                twitch_live_state[guild.id] = True
                await self.post_live(guild, cfg, meta)
                await update_guild_cfg(guild.id, twitch_announced_this_stream=1)
                continue

            if announced and live_now:
                twitch_live_state[guild.id] = True

            if announced and prev_live and (not live_now) and twitch_off_hits[guild.id] >= stable:
                offline_duration = now - last_seen_live_ts
                if offline_duration >= offline_grace:
                    twitch_live_state[guild.id] = False
                    await self.edit_to_offline(guild, cfg, meta)
                    await update_guild_cfg(guild.id, twitch_announced_this_stream=0)

    async def post_live(self, guild: discord.Guild, cfg: dict, meta: dict) -> None:
        text_channel = await resolve_announce_channel(guild, cfg)
        if not text_channel:
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
        from bot import update_guild_cfg
        await update_guild_cfg(guild.id, twitch_last_live_message_id=msg.id)

    async def edit_to_offline(self, guild: discord.Guild, cfg: dict, meta: dict) -> None:
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
            logger.warning(f"[{guild.name}] OFFLINE edit failed: {e}")

    @app_commands.command(name="setup_twitchlive2", description="Twitch Live Alerts ohne API: genau 1 Live-Ping pro Stream.")
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
        self,
        interaction: discord.Interaction,
        twitch_channel_or_url: str,
        announce_channel: discord.TextChannel,
        ping_role: discord.Role | None = None,
        stable_checks: app_commands.Range[int, 1, 5] = 2,
        poll_seconds: app_commands.Range[int, 30, 600] = 90,
        offline_grace_minutes: app_commands.Range[int, 0, 60] = 5
    ):
        from bot import update_guild_cfg
        await update_guild_cfg(
            interaction.guild_id,
            twitch_enabled=1,
            twitch_channel=extract_twitch_channel(twitch_channel_or_url),
            twitch_announce_channel_id=int(announce_channel.id),
            twitch_ping_role_id=int(ping_role.id) if ping_role else None,
            twitch_stable_checks=max(1, int(stable_checks)),
            twitch_poll_seconds=max(30, int(poll_seconds)),
            twitch_offline_grace_seconds=max(0, int(offline_grace_minutes) * 60),
            twitch_last_live_message_id=None,
            twitch_last_check_ts=0.0,
            twitch_last_seen_live_ts=0.0,
            twitch_announced_this_stream=0
        )

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

    @app_commands.command(name="twitchlive_status", description="Zeigt Twitch-Konfiguration + aktuellen Status.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def twitchlive_status(self, interaction: discord.Interaction):
        from bot import get_guild_cfg
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

    @app_commands.command(name="twitchlive_set_poll", description="Ändert die Abfragerate (Polling) für Twitch.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(poll_seconds="Neue Abfragerate in Sekunden (min 30, empfohlen 90)")
    async def twitchlive_set_poll(self, interaction: discord.Interaction, poll_seconds: app_commands.Range[int, 30, 600] = 90):
        from bot import get_guild_cfg, update_guild_cfg
        cfg = await get_guild_cfg(interaction.guild_id)
        if not cfg.get("twitch_enabled"):
            await interaction.response.send_message("ℹ️ Twitch Live-Alerts sind nicht aktiviert.", ephemeral=True)
            return
        await update_guild_cfg(interaction.guild_id, twitch_poll_seconds=int(poll_seconds))
        await interaction.response.send_message(f"✅ Polling-Rate gesetzt auf **{poll_seconds}s**.", ephemeral=True)

    @app_commands.command(name="twitchlive_test", description="Testet LIVE-Embed (funktioniert immer, auch wenn offline).")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def twitchlive_test(self, interaction: discord.Interaction):
        from bot import get_guild_cfg, update_guild_cfg
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
        await update_guild_cfg(guild.id, twitch_last_live_message_id=msg.id)
        await interaction.response.send_message("🧪 Test gesendet (LIVE-Embed + Button).", ephemeral=True)

    @app_commands.command(name="twitchoffline_test", description="Testet OFFLINE-Edit (editiert den letzten LIVE-Post).")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def twitchoffline_test(self, interaction: discord.Interaction):
        from bot import get_guild_cfg
        cfg = await get_guild_cfg(interaction.guild_id)
        if not cfg.get("twitch_enabled"):
            await interaction.response.send_message("ℹ️ Erst /setup_twitchlive2 ausführen.", ephemeral=True)
            return

        guild = interaction.guild
        meta = {"avatar": (twitch_meta_cache.get(cfg["twitch_channel"], {}) or {}).get("avatar")}
        await self.edit_to_offline(guild, cfg, meta)
        await interaction.response.send_message("🧪 OFFLINE-Edit versucht (siehe #live).", ephemeral=True)

    @app_commands.command(name="twitchlive_disable", description="Deaktiviert Twitch Live-Alerts (Voice bleibt unangetastet!).")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def twitchlive_disable(self, interaction: discord.Interaction):
        from bot import clear_guild_cfg_fields, update_guild_cfg
        await clear_guild_cfg_fields(interaction.guild_id, [
            "twitch_enabled", "twitch_channel", "twitch_announce_channel_id", "twitch_ping_role_id",
            "twitch_stable_checks", "twitch_poll_seconds", "twitch_offline_grace_seconds",
            "twitch_last_live_message_id", "twitch_last_check_ts", "twitch_last_seen_live_ts",
            "twitch_announced_this_stream"
        ])
        await update_guild_cfg(interaction.guild_id, twitch_enabled=0)
        
        gid = int(interaction.guild_id)
        twitch_live_state[gid] = False
        twitch_live_hits[gid] = 0
        twitch_off_hits[gid] = 0
        await interaction.response.send_message("🛑 Twitch Live-Alerts wurden deaktiviert. (Auto-Voice bleibt aktiv)", ephemeral=True)

    @setup_twitchlive2.error
    @twitchlive_status.error
    @twitchlive_set_poll.error
    @twitchlive_test.error
    @twitchoffline_test.error
    @twitchlive_disable.error
    async def perms_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            msg = "❌ Dafür brauchst du **Server verwalten**."
        else:
            msg = f"⚠️ Fehler: {error}"

        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(TwitchCog(bot))
