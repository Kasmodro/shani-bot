import re
import logging
import asyncio
import aiohttp
import time
import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timezone

logger = logging.getLogger("shani-bot")

# ============================================================
# YOUTUBE CONFIG
# ============================================================
YT_DEFAULT_POLL_SECONDS = 300  # Etwas seltener als Twitch, da YouTube restriktiver sein kann
YT_OFFLINE_GRACE_SECONDS_DEFAULT = 600  # 10 Minuten

# --- YouTube Runtime State ---
yt_live_state: dict[int, bool] = {}
yt_live_hits: dict[int, int] = {}
yt_off_hits: dict[int, int] = {}

def extract_yt_channel(value: str) -> str:
    v = value.strip()
    v = v.replace("https://", "").replace("http://", "")
    v = v.replace("www.", "")
    v = v.replace("youtube.com/", "")
    v = v.replace("c/", "").replace("channel/", "").replace("user/", "")
    v = v.strip("/")
    if "/" in v:
        v = v.split("/")[0]
    return v

async def fetch_yt_page(session: aiohttp.ClientSession, yt_channel: str):
    if yt_channel.startswith("@"):
        url = f"https://www.youtube.com/{yt_channel}/live"
    else:
        url = f"https://www.youtube.com/channel/{yt_channel}/live"
        
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache"
    }
    try:
        async with session.get(url, headers=headers, timeout=15) as resp:
            if resp.status == 200:
                return await resp.text()
            return None
    except Exception as e:
        logger.error(f"fetch_yt_page error for {yt_channel}: {e}")
        return None

def parse_yt_meta(html_text: str) -> dict:
    meta = {"is_live": False, "title": None, "avatar": None}
    
    # Live Indikator
    if '"isLive":true' in html_text:
        meta["is_live"] = True
    
    # Titel
    t_match = re.search(r'<meta name="title" content="([^"]+)">', html_text)
    if t_match:
        meta["title"] = t_match.group(1)
    
    # Avatar (etwas tricky bei YT)
    a_match = re.search(r'"avatar":\{"thumbnails":\[\{"url":"([^"]+)"', html_text)
    if a_match:
        meta["avatar"] = a_match.group(1).replace("\\/", "/")
        
    return meta

def build_yt_live_embed(yt_channel: str, meta: dict) -> discord.Embed:
    url = f"https://www.youtube.com/{yt_channel}/live" if yt_channel.startswith("@") else f"https://www.youtube.com/channel/{yt_channel}/live"
    e = discord.Embed(
        title=f"🔴 {yt_channel} ist jetzt LIVE auf YouTube!",
        description=f"**Jetzt zuschauen:** {url}",
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc)
    )
    if meta.get("title"):
        e.add_field(name="Stream-Titel", value=meta["title"], inline=False)
    if meta.get("avatar"):
        e.set_thumbnail(url=meta["avatar"])
    e.set_footer(text="Raiders Cache • YouTube Alert")
    return e

def build_yt_offline_embed(yt_channel: str, meta: dict) -> discord.Embed:
    e = discord.Embed(
        title=f"⚫ {yt_channel} ist jetzt OFFLINE",
        description="Der YouTube-Stream ist beendet.",
        color=discord.Color.dark_grey(),
        timestamp=datetime.now(timezone.utc)
    )
    if meta.get("avatar"):
        e.set_thumbnail(url=meta["avatar"])
    e.set_footer(text="Raiders Cache • YouTube Alert")
    return e

class YoutubeChannelModal(discord.ui.Modal, title="YouTube Kanal festlegen"):
    yt_input = discord.ui.TextInput(
        label="YouTube Handle oder Channel-ID",
        placeholder="z.B. @tagesschau oder UC...",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        channel = extract_yt_channel(self.yt_input.value)
        from bot import update_guild_cfg
        await update_guild_cfg(interaction.guild_id, youtube_channel=channel)
        await interaction.response.send_message(f"✅ YouTube-Kanal auf **{channel}** gesetzt.", ephemeral=True)

class YoutubeSettingsModal(discord.ui.Modal, title="YouTube Feineinstellungen"):
    stable = discord.ui.TextInput(
        label="Stable Checks (1-5)",
        placeholder="Standard: 2",
        min_length=1,
        max_length=1,
        required=True
    )
    poll = discord.ui.TextInput(
        label="Poll Sekunden (60-1200)",
        placeholder="Standard: 300",
        min_length=2,
        max_length=4,
        required=True
    )
    grace = discord.ui.TextInput(
        label="Offline Grace Minuten (0-120)",
        placeholder="Standard: 10",
        min_length=1,
        max_length=3,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            s = int(self.stable.value)
            p = int(self.poll.value)
            g = int(self.grace.value)
            
            if not (1 <= s <= 5): raise ValueError("Stable muss zwischen 1-5 liegen.")
            if not (60 <= p <= 1200): raise ValueError("Poll muss zwischen 60-1200 liegen.")
            if not (0 <= g <= 120): raise ValueError("Grace muss zwischen 0-120 liegen.")
            
            from bot import update_guild_cfg
            await update_guild_cfg(
                interaction.guild_id, 
                youtube_stable_checks=s,
                youtube_poll_seconds=p,
                youtube_offline_grace_seconds=g * 60
            )
            await interaction.response.send_message(f"✅ Einstellungen gespeichert:\nStable: {s} | Poll: {p}s | Grace: {g}m", ephemeral=True)
        except ValueError as e:
            await interaction.response.send_message(f"❌ Ungültige Eingabe: {e}", ephemeral=True)

class YoutubeSetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @staticmethod
    async def build_setup_embed(guild: discord.Guild):
        from bot import get_guild_cfg
        cfg = await get_guild_cfg(guild.id)
        
        embed = discord.Embed(
            title="🔴 YouTube-Live Setup",
            description="Konfiguriere den YouTube-Kanal und die Benachrichtigungen.",
            color=discord.Color.red()
        )
        
        if cfg.get("youtube_enabled"):
            ch_id = cfg.get("youtube_announce_channel_id")
            ch = guild.get_channel(int(ch_id)) if ch_id else None
            role_id = cfg.get("youtube_ping_role_id")
            role = guild.get_role(int(role_id)) if role_id else None
            
            stable = cfg.get("youtube_stable_checks", 2)
            poll = cfg.get("youtube_poll_seconds", 300)
            grace = int(cfg.get("youtube_offline_grace_seconds", 600)) // 60
            
            status_text = (
                f"✅ **Aktiviert**\n"
                f"• Kanal: **{cfg.get('youtube_channel', '—')}**\n"
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

    @discord.ui.button(label="YouTube-Kanal setzen", style=discord.ButtonStyle.primary, row=0)
    async def btn_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(YoutubeChannelModal())

    @discord.ui.button(label="Feineinstellungen", style=discord.ButtonStyle.secondary, row=0)
    async def btn_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(YoutubeSettingsModal())

    @discord.ui.select(cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="📢 Ankündigungs-Kanal wählen", row=1)
    async def select_announce(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        from bot import update_guild_cfg
        await update_guild_cfg(interaction.guild_id, youtube_announce_channel_id=select.values[0].id)
        await interaction.response.send_message(f"✅ YouTube Ankündigungs-Kanal auf {select.values[0].mention} gesetzt.", ephemeral=True)
        await self._update_embed(interaction)

    @discord.ui.select(cls=discord.ui.RoleSelect, placeholder="🔔 Ping-Rolle wählen (optional)", row=2)
    async def select_ping(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        from bot import update_guild_cfg
        role = select.values[0]
        await update_guild_cfg(interaction.guild_id, youtube_ping_role_id=role.id)
        await interaction.response.send_message(f"✅ YouTube Ping-Rolle auf {role.mention} gesetzt.", ephemeral=True)
        await self._update_embed(interaction)

    @discord.ui.button(label="Aktivieren", style=discord.ButtonStyle.success, row=3)
    async def btn_enable(self, interaction: discord.Interaction, button: discord.ui.Button):
        from bot import update_guild_cfg
        await update_guild_cfg(interaction.guild_id, youtube_enabled=1)
        await interaction.response.send_message("✅ YouTube-Live Benachrichtigungen wurden aktiviert.", ephemeral=True)
        await self._update_embed(interaction)

    @discord.ui.button(label="Deaktivieren", style=discord.ButtonStyle.danger, row=3)
    async def btn_disable(self, interaction: discord.Interaction, button: discord.ui.Button):
        from bot import update_guild_cfg
        await update_guild_cfg(interaction.guild_id, youtube_enabled=0)
        await interaction.response.send_message("🛑 YouTube-Alerts deaktiviert.", ephemeral=True)
        await self._update_embed(interaction)

class YoutubeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.youtube_loop.start()

    def cog_unload(self):
        self.youtube_loop.cancel()

    @tasks.loop(seconds=60)
    async def youtube_loop(self):
        if not self.bot.http_session:
            return

        from bot import get_guild_cfg, update_guild_cfg

        for guild in self.bot.guilds:
            try:
                cfg = await get_guild_cfg(guild.id)
                if not cfg.get("youtube_enabled"):
                    continue
                if not cfg.get("youtube_channel") or not cfg.get("youtube_announce_channel_id"):
                    continue

                now = time.time()
                poll_seconds = int(cfg.get("youtube_poll_seconds", YT_DEFAULT_POLL_SECONDS))
                last_check = float(cfg.get("youtube_last_check_ts", 0.0))
                
                if (now - last_check) < poll_seconds:
                    continue

                await update_guild_cfg(guild.id, youtube_last_check_ts=now)

                yt_channel = cfg["youtube_channel"]
                html_text = await fetch_yt_page(self.bot.http_session, yt_channel)
                if not html_text:
                    continue

                meta = parse_yt_meta(html_text)
                live_now = meta["is_live"]
                prev_live = yt_live_state.get(guild.id, False)

                stable_checks = int(cfg.get("youtube_stable_checks", 2))
                offline_grace = int(cfg.get("youtube_offline_grace_seconds", YT_OFFLINE_GRACE_SECONDS_DEFAULT))

                if live_now:
                    yt_live_hits[guild.id] = yt_live_hits.get(guild.id, 0) + 1
                    yt_off_hits[guild.id] = 0
                    await update_guild_cfg(guild.id, youtube_last_seen_live_ts=now)
                else:
                    yt_off_hits[guild.id] = yt_off_hits.get(guild.id, 0) + 1
                    yt_live_hits[guild.id] = 0

                announced = bool(cfg.get("youtube_announced_this_stream", False))
                last_seen = float(cfg.get("youtube_last_seen_live_ts", 0.0))

                # Live gehen
                if (not announced) and (not prev_live) and live_now and yt_live_hits[guild.id] >= stable_checks:
                    yt_live_state[guild.id] = True
                    await self.post_live(guild, cfg, meta)
                    await update_guild_cfg(guild.id, youtube_announced_this_stream=1)
                    continue

                if announced and live_now:
                    yt_live_state[guild.id] = True

                # Offline gehen
                if announced and prev_live and (not live_now) and yt_off_hits[guild.id] >= stable_checks:
                    if (now - last_seen) >= offline_grace:
                        yt_live_state[guild.id] = False
                        await self.edit_to_offline(guild, cfg, meta)
                        await update_guild_cfg(guild.id, youtube_announced_this_stream=0)

            except Exception as e:
                logger.error(f"Error in youtube_loop for guild {guild.id}: {e}")

    async def post_live(self, guild: discord.Guild, cfg: dict, meta: dict):
        channel = guild.get_channel(int(cfg["youtube_announce_channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            return

        mention = None
        if cfg.get("youtube_ping_role_id"):
            role = guild.get_role(int(cfg["youtube_ping_role_id"]))
            if role: mention = role.mention

        embed = build_yt_live_embed(cfg["youtube_channel"], meta)
        msg = await channel.send(content=mention, embed=embed)
        
        from bot import update_guild_cfg
        await update_guild_cfg(guild.id, youtube_last_live_message_id=msg.id)

    async def edit_to_offline(self, guild: discord.Guild, cfg: dict, meta: dict):
        channel = guild.get_channel(int(cfg["youtube_announce_channel_id"]))
        last_id = cfg.get("youtube_last_live_message_id")
        if not channel or not last_id: return

        try:
            msg = await channel.fetch_message(int(last_id))
            await msg.edit(content=None, embed=build_yt_offline_embed(cfg["youtube_channel"], meta))
        except:
            pass

    @app_commands.command(name="setup_youtubelive", description="YouTube Live Alerts (Scraping-basiert).")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        handle_or_id="z.B. @tagesschau oder UC...",
        announce_channel="Textkanal für Live-Meldungen",
        ping_role="Optional: Rolle die beim Live-Start gepingt wird",
        stable_checks="Anti-Zick: wie viele gleiche Ergebnisse nötig (empfohlen 2)",
        poll_seconds="Abfragerate in Sekunden (min 60, empfohlen 300)",
        offline_grace_minutes="Wie lange muss er offline sein, bis es als 'Stream beendet' gilt (Standard 10)"
    )
    async def setup_youtubelive(
        self,
        interaction: discord.Interaction,
        handle_or_id: str,
        announce_channel: discord.TextChannel,
        ping_role: discord.Role | None = None,
        stable_checks: app_commands.Range[int, 1, 5] = 2,
        poll_seconds: app_commands.Range[int, 60, 1200] = 300,
        offline_grace_minutes: app_commands.Range[int, 0, 120] = 10
    ):
        channel = extract_yt_channel(handle_or_id)
        from bot import update_guild_cfg
        await update_guild_cfg(
            interaction.guild_id,
            youtube_enabled=1,
            youtube_channel=channel,
            youtube_announce_channel_id=announce_channel.id,
            youtube_ping_role_id=ping_role.id if ping_role else None,
            youtube_stable_checks=max(1, int(stable_checks)),
            youtube_poll_seconds=max(60, int(poll_seconds)),
            youtube_offline_grace_seconds=max(0, int(offline_grace_minutes) * 60),
            youtube_announced_this_stream=0,
            youtube_last_check_ts=0.0,
            youtube_last_seen_live_ts=0.0
        )
        await interaction.response.send_message(
            f"✅ YouTube Live-Alerts aktiviert.\n"
            f"📺 YouTube: **{channel}**\n"
            f"📢 Kanal: {announce_channel.mention}\n"
            f"🔇 Stabil: **{stable_checks}** | ⏲️ Poll: **{poll_seconds}s** | 🧊 Offline-Grace: **{offline_grace_minutes} min**",
            ephemeral=True
        )

    @app_commands.command(name="youtubelive_status", description="Zeigt den YouTube-Live Status.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def youtubelive_status(self, interaction: discord.Interaction):
        from bot import get_guild_cfg
        cfg = await get_guild_cfg(interaction.guild_id)
        if not cfg or not cfg.get("youtube_enabled"):
            await interaction.response.send_message("ℹ️ YouTube-Alerts sind deaktiviert.", ephemeral=True)
            return
        
        announce_ch = interaction.guild.get_channel(int(cfg.get("youtube_announce_channel_id", 0)))
        role = interaction.guild.get_role(int(cfg["youtube_ping_role_id"])) if cfg.get("youtube_ping_role_id") else None

        await interaction.response.send_message(
            f"✅ YouTube Status:\n"
            f"📺 Kanal: **{cfg.get('youtube_channel')}**\n"
            f"📢 Kanal: {announce_ch.mention if announce_ch else 'FEHLT'}\n"
            f"🏷️ Ping: **{role.name if role else '—'}**\n"
            f"🔇 Stable: **{cfg.get('youtube_stable_checks', 2)}** | ⏲️ Poll: **{cfg.get('youtube_poll_seconds', 300)}s** | 🧊 Offline-Grace: **{int(cfg.get('youtube_offline_grace_seconds', 600))//60} min**\n"
            f"🔴 Live (intern): **{'LIVE' if yt_live_state.get(interaction.guild_id) else 'OFFLINE'}**",
            ephemeral=True
        )

    @app_commands.command(name="youtubelive_disable", description="Deaktiviert YouTube-Alerts.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def youtubelive_disable(self, interaction: discord.Interaction):
        from bot import update_guild_cfg
        await update_guild_cfg(interaction.guild_id, youtube_enabled=0)
        await interaction.response.send_message("🛑 YouTube-Alerts deaktiviert.", ephemeral=True)

    @app_commands.command(name="youtubelive_test", description="Testet YouTube LIVE-Embed (funktioniert immer).")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def youtubelive_test(self, interaction: discord.Interaction):
        from bot import get_guild_cfg
        cfg = await get_guild_cfg(interaction.guild_id)
        if not cfg.get("youtube_enabled"):
            await interaction.response.send_message("ℹ️ Erst /setup_youtubelive ausführen.", ephemeral=True)
            return

        meta = {
            "title": "Test-Stream (nur Bot-Test)",
            "avatar": None
        }
        await self.post_live(interaction.guild, cfg, meta)
        await interaction.response.send_message("🧪 YouTube-Test gesendet.", ephemeral=True)

    @app_commands.command(name="youtubeoffline_test", description="Testet YouTube OFFLINE-Edit.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def youtubeoffline_test(self, interaction: discord.Interaction):
        from bot import get_guild_cfg
        cfg = await get_guild_cfg(interaction.guild_id)
        if not cfg.get("youtube_enabled"):
            await interaction.response.send_message("ℹ️ Erst /setup_youtubelive ausführen.", ephemeral=True)
            return

        meta = {"avatar": None}
        await self.edit_to_offline(interaction.guild, cfg, meta)
        await interaction.response.send_message("🧪 YouTube OFFLINE-Edit versucht.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(YoutubeCog(bot))
