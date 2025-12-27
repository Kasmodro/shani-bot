# modules/setcards.py
import os
import json
import re
import asyncio
import sqlite3
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord import app_commands

# ============================================================
# PATHS / DB
# ============================================================
BASE_DIR = os.path.dirname(os.path.dirname(__file__))  # project root
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "setcards.db")

os.makedirs(DATA_DIR, exist_ok=True)

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _ensure_db_sync() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                setcard_channel_id INTEGER
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS setcards (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                embark_id TEXT NOT NULL,
                orientation_json TEXT NOT NULL,
                experience TEXT,
                platform TEXT,
                network TEXT,
                age_group TEXT,
                voice TEXT,
                note TEXT,
                setcard_message_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );
        """)
        conn.commit()
    finally:
        conn.close()

_ensure_db_sync()
_db_lock = asyncio.Lock()

async def _db_run(func, *args):
    return await asyncio.to_thread(func, *args)

def _db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

# ============================================================
# SAFE TIMEOUT WRAPPER (Discord Calls)
# ============================================================
async def _with_timeout(coro, sec: int = 6):
    return await asyncio.wait_for(coro, timeout=sec)

# ============================================================
# OPTIONS / VALIDATION
# ============================================================
ORIENTATION_OPTIONS = ["PvE", "PvP", "Quest", "Loot"]
EXPERIENCE_OPTIONS = ["Anfänger", "Fortgeschritten", "Alter Hase"]
PLATFORM_OPTIONS = ["PC", "Konsole"]
NETWORK_BY_PLATFORM = {
    "PC": ["Steam", "Epic", "Other"],
    "Konsole": ["PSN", "Xbox"],
}
AGE_GROUP_OPTIONS = ["18+", "25+", "30+", "40+", "50+"]
VOICE_OPTIONS = ["Ja", "Nein", "Optional"]

# Dummy values (for disabled selects that still require options)
DUMMY_WAIT_VALUE = "__wait__"

EMBARK_RE = re.compile(r"^[^\s#]{2,32}#[0-9]{2,8}$")

def validate_embark_id(value: str) -> tuple[bool, str]:
    v = (value or "").strip()
    if not v:
        return False, "Embark ID fehlt."
    if not EMBARK_RE.match(v):
        return False, "Embark ID muss so aussehen: `Kasmodro_DE#6916` (Name#Nummer)."
    return True, ""

def get_display_raider_name(member: discord.Member) -> str:
    return member.display_name

def _json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)

def _json_loads(s: str):
    try:
        return json.loads(s) if s else None
    except Exception:
        return None

# ============================================================
# DB: GUILD SETTINGS
# ============================================================
def _set_setcard_channel_sync(guild_id: int, channel_id: int) -> None:
    conn = _db_connect()
    try:
        conn.execute(
            "INSERT INTO guild_settings (guild_id, setcard_channel_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET setcard_channel_id=excluded.setcard_channel_id",
            (int(guild_id), int(channel_id)),
        )
        conn.commit()
    finally:
        conn.close()

async def set_setcard_channel(guild_id: int, channel_id: int) -> None:
    async with _db_lock:
        await _db_run(_set_setcard_channel_sync, guild_id, channel_id)

def _get_setcard_channel_id_sync(guild_id: int) -> int | None:
    conn = _db_connect()
    try:
        row = conn.execute(
            "SELECT setcard_channel_id FROM guild_settings WHERE guild_id=?",
            (int(guild_id),),
        ).fetchone()
        if not row:
            return None
        val = row["setcard_channel_id"]
        return int(val) if val is not None else None
    finally:
        conn.close()

async def get_setcard_channel_id(guild_id: int) -> int | None:
    async with _db_lock:
        return await _db_run(_get_setcard_channel_id_sync, guild_id)

# ============================================================
# DB: SETCARDS
# ============================================================
def _row_to_card(row: sqlite3.Row) -> dict:
    return {
        "guild_id": int(row["guild_id"]),
        "user_id": int(row["user_id"]),
        "embark_id": row["embark_id"],
        "orientation": _json_loads(row["orientation_json"]) or [],
        "experience": row["experience"] or "",
        "platform": row["platform"] or "",
        "network": row["network"] or "",
        "age_group": row["age_group"] or "",
        "voice": row["voice"] or "",
        "note": row["note"] or "",
        "setcard_message_id": row["setcard_message_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }

def _get_card_sync(guild_id: int, user_id: int) -> dict | None:
    conn = _db_connect()
    try:
        row = conn.execute(
            "SELECT * FROM setcards WHERE guild_id=? AND user_id=?",
            (int(guild_id), int(user_id)),
        ).fetchone()
        return _row_to_card(row) if row else None
    finally:
        conn.close()

async def get_card(guild_id: int, user_id: int) -> dict | None:
    async with _db_lock:
        return await _db_run(_get_card_sync, guild_id, user_id)

def _upsert_card_sync(guild_id: int, user_id: int, card: dict) -> None:
    conn = _db_connect()
    try:
        existing = conn.execute(
            "SELECT created_at FROM setcards WHERE guild_id=? AND user_id=?",
            (int(guild_id), int(user_id)),
        ).fetchone()
        created_at = existing["created_at"] if existing else (card.get("created_at") or _iso_now())
        updated_at = card.get("updated_at") or _iso_now()

        conn.execute(
            """
            INSERT INTO setcards (
                guild_id, user_id, embark_id, orientation_json, experience, platform, network,
                age_group, voice, note, setcard_message_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                embark_id=excluded.embark_id,
                orientation_json=excluded.orientation_json,
                experience=excluded.experience,
                platform=excluded.platform,
                network=excluded.network,
                age_group=excluded.age_group,
                voice=excluded.voice,
                note=excluded.note,
                setcard_message_id=excluded.setcard_message_id,
                updated_at=excluded.updated_at
            """,
            (
                int(guild_id),
                int(user_id),
                (card.get("embark_id") or "").strip(),
                _json_dumps(card.get("orientation") or []),
                (card.get("experience") or "").strip(),
                (card.get("platform") or "").strip(),
                (card.get("network") or "").strip(),
                (card.get("age_group") or "").strip(),
                (card.get("voice") or "").strip(),
                (card.get("note") or "").strip(),
                int(card["setcard_message_id"]) if card.get("setcard_message_id") else None,
                created_at,
                updated_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()

async def upsert_card(guild_id: int, user_id: int, card: dict) -> None:
    async with _db_lock:
        await _db_run(_upsert_card_sync, guild_id, user_id, card)

def _delete_card_sync(guild_id: int, user_id: int) -> dict | None:
    conn = _db_connect()
    try:
        row = conn.execute(
            "SELECT * FROM setcards WHERE guild_id=? AND user_id=?",
            (int(guild_id), int(user_id)),
        ).fetchone()
        if not row:
            return None
        card = _row_to_card(row)
        conn.execute(
            "DELETE FROM setcards WHERE guild_id=? AND user_id=?",
            (int(guild_id), int(user_id)),
        )
        conn.commit()
        return card
    finally:
        conn.close()

async def delete_card(guild_id: int, user_id: int) -> dict | None:
    async with _db_lock:
        return await _db_run(_delete_card_sync, guild_id, user_id)

def _list_cards_in_guild_sync(guild_id: int) -> list[dict]:
    conn = _db_connect()
    try:
        rows = conn.execute(
            "SELECT * FROM setcards WHERE guild_id=? ORDER BY updated_at DESC",
            (int(guild_id),),
        ).fetchall()
        return [_row_to_card(r) for r in rows]
    finally:
        conn.close()

async def list_cards_in_guild(guild_id: int) -> list[dict]:
    async with _db_lock:
        return await _db_run(_list_cards_in_guild_sync, guild_id)

# ============================================================
# EMBEDS + CHANNEL POSTING
# ============================================================
def build_setcard_embed(member: discord.Member, card: dict) -> discord.Embed:
    raider = get_display_raider_name(member)
    embark_id = card.get("embark_id") or "—"

    orientation = card.get("orientation") or []
    orientation_str = " · ".join(orientation) if orientation else "—"

    experience = card.get("experience") or "—"

    platform = card.get("platform") or "—"
    network = card.get("network") or "—"
    platform_str = (
        f"{platform} ({network})"
        if platform != "—" and network != "—" and platform and network
        else (platform if platform else "—")
    )

    age_group = card.get("age_group") or "—"
    voice = card.get("voice") or "—"
    note = (card.get("note") or "").strip()

    e = discord.Embed(
        title="🧭 RAIDER SETCARD",
        description=f"**Raider:** {raider}\n**Embark ID:** `{embark_id}`",
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    )
    e.add_field(name="🎮 Orientierung", value=orientation_str, inline=False)
    e.add_field(name="🎓 Erfahrung", value=experience, inline=True)
    e.add_field(name="🖥️ Plattform", value=platform_str, inline=True)
    e.add_field(name="🎂 Alter", value=age_group, inline=True)
    e.add_field(name="🎧 Voice", value=voice, inline=True)

    if note:
        if len(note) > 200:
            note = note[:200] + "…"
        e.add_field(name="📝 Kurzinfo", value=note, inline=False)

    updated_at = card.get("updated_at") or "—"
    e.set_footer(text=f"Raiders Cache • Shani Bot • Updated: {updated_at}")
    return e

async def ensure_setcard_post(guild: discord.Guild, member: discord.Member, card: dict) -> None:
    channel_id = await get_setcard_channel_id(guild.id)
    if not channel_id:
        return

    ch = guild.get_channel(int(channel_id))
    if not isinstance(ch, discord.TextChannel):
        return

    embed = build_setcard_embed(member, card)
    msg_id = card.get("setcard_message_id")

    if msg_id:
        try:
            msg = await _with_timeout(ch.fetch_message(int(msg_id)), sec=6)
            await _with_timeout(msg.edit(content=member.mention, embed=embed, view=None), sec=6)
            return
        except Exception as e:
            print(f"⚠️ ensure_setcard_post: edit failed -> will post new. ({type(e).__name__}: {e})")

    try:
        msg = await _with_timeout(ch.send(content=member.mention, embed=embed), sec=6)
        card["setcard_message_id"] = msg.id
        card["updated_at"] = _iso_now()
        if not card.get("created_at"):
            card["created_at"] = _iso_now()
        await upsert_card(guild.id, member.id, card)
    except Exception as e:
        print(f"⚠️ ensure_setcard_post: send failed. ({type(e).__name__}: {e})")
        return

async def delete_setcard_post(guild: discord.Guild, card: dict) -> None:
    channel_id = await get_setcard_channel_id(guild.id)
    if not channel_id:
        return

    ch = guild.get_channel(int(channel_id))
    if not isinstance(ch, discord.TextChannel):
        return

    msg_id = card.get("setcard_message_id")
    if not msg_id:
        return

    try:
        msg = await _with_timeout(ch.fetch_message(int(msg_id)), sec=6)
        await _with_timeout(msg.delete(), sec=6)
    except Exception as e:
        print(f"⚠️ delete_setcard_post failed. ({type(e).__name__}: {e})")

# ============================================================
# UI: Modals
# ============================================================
class EmbarkIdModal(discord.ui.Modal, title="Embark ID setzen"):
    embark_id = discord.ui.TextInput(
        label="Embark ID (Name#Nummer)",
        placeholder="Kasmodro_DE#6916",
        required=True,
        max_length=64,
    )

    def __init__(self, view: "BaseSetcardView"):
        super().__init__()
        self.view_ref = view

    async def on_submit(self, interaction: discord.Interaction):
        ok, msg = validate_embark_id(str(self.embark_id.value))
        if not ok:
            await interaction.response.send_message(f"❌ {msg}", ephemeral=True)
            return

        self.view_ref.card["embark_id"] = str(self.embark_id.value).strip()
        self.view_ref.touch()
        await interaction.response.send_message("✅ Embark ID gespeichert.", ephemeral=True)
        await self.view_ref.refresh_message(interaction)

class NoteModal(discord.ui.Modal, title="Kurzinfo setzen"):
    note = discord.ui.TextInput(
        label="Kurzinfo (optional, max 200 Zeichen)",
        placeholder="z.B. entspannt, teamplay, kein rage",
        required=False,
        max_length=200,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, view: "BaseSetcardView"):
        super().__init__()
        self.view_ref = view

    async def on_submit(self, interaction: discord.Interaction):
        self.view_ref.card["note"] = (str(self.note.value) or "").strip()
        self.view_ref.touch()
        await interaction.response.send_message("✅ Kurzinfo gespeichert.", ephemeral=True)
        await self.view_ref.refresh_message(interaction)

# ============================================================
# UI: Base View + Page 1/2
# ============================================================
class BaseSetcardView(discord.ui.View):
    def __init__(self, member: discord.Member, existing: dict | None, page: int):
        super().__init__(timeout=10 * 60)
        self.member = member
        self.guild_id = member.guild.id
        self.user_id = member.id
        self.page = page  # 1 or 2

        self.card = (existing or {}).copy()
        self.card.setdefault("embark_id", "")
        self.card.setdefault("orientation", [])
        self.card.setdefault("experience", "")
        self.card.setdefault("platform", "")
        self.card.setdefault("network", "")
        self.card.setdefault("age_group", "")
        self.card.setdefault("voice", "")
        self.card.setdefault("note", "")
        self.card.setdefault("setcard_message_id", self.card.get("setcard_message_id"))
        self.card.setdefault("created_at", self.card.get("created_at") or _iso_now())
        self.card.setdefault("updated_at", self.card.get("updated_at") or _iso_now())

        self.message: discord.Message | None = None

    def touch(self):
        self.card["updated_at"] = _iso_now()

    def _status_lines(self) -> str:
        embark = self.card.get("embark_id") or "—"
        ori = self.card.get("orientation") or []
        ori_str = " · ".join(ori) if ori else "—"
        exp = self.card.get("experience") or "—"
        plat = self.card.get("platform") or "—"
        net = self.card.get("network") or "—"
        plat_str = f"{plat} ({net})" if plat and net else plat
        age = self.card.get("age_group") or "—"
        voice = self.card.get("voice") or "—"
        note = (self.card.get("note") or "").strip() or "—"

        return (
            f"**Raider (Discord):** {get_display_raider_name(self.member)}\n"
            f"**Embark ID:** `{embark}`\n"
            f"**Orientierung:** {ori_str}\n"
            f"**Erfahrung:** {exp}\n"
            f"**Plattform:** {plat_str}\n"
            f"**Alter:** {age}\n"
            f"**Voice:** {voice}\n"
            f"**Kurzinfo:** {note}"
        )

    def _header(self) -> str:
        if self.page == 1:
            return "🛠️ **Setcard bearbeiten (Seite 1/2 – Basics)**\nWähle aus und gehe mit ➡️ weiter."
        return "🛠️ **Setcard bearbeiten (Seite 2/2 – Optional & Save)**\nWähle optionales und speichere mit ✅."

    async def refresh_message(self, interaction: discord.Interaction):
        content = self._header() + "\n\n" + self._status_lines()
        if interaction.response.is_done():
            if self.message:
                await self.message.edit(content=content, view=self)
        else:
            await interaction.response.edit_message(content=content, view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Das ist nicht deine Setcard.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(content="⌛ Setcard-Editor abgelaufen. Starte neu mit `/setcard edit`.", view=None)
            except Exception:
                pass

    # Row 0: top buttons (width 1 each)
    @discord.ui.button(label="Embark ID", style=discord.ButtonStyle.primary, row=0)
    async def btn_embark(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EmbarkIdModal(self))

    @discord.ui.button(label="Kurzinfo", style=discord.ButtonStyle.secondary, row=0)
    async def btn_note(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(NoteModal(self))

# ----------------------------
# PAGE 1
# ----------------------------
class SetcardEditViewPage1(BaseSetcardView):
    def __init__(self, member: discord.Member, existing: dict | None):
        super().__init__(member, existing, page=1)

        self.orientation_select.options = [discord.SelectOption(label=o, value=o) for o in ORIENTATION_OPTIONS]
        self.experience_select.options = [discord.SelectOption(label=o, value=o) for o in EXPERIENCE_OPTIONS]
        self.platform_select.options = [discord.SelectOption(label=o, value=o) for o in PLATFORM_OPTIONS]

        self._sync_defaults()

    def _ensure_network_dummy(self):
        # Discord requires options even if select is disabled.
        self.network_select.options = [
            discord.SelectOption(label="(erst Plattform wählen)", value=DUMMY_WAIT_VALUE, default=True)
        ]
        self.network_select.disabled = True

    def _sync_network_options(self):
        plat = (self.card.get("platform") or "").strip()
        self.network_select.options = []

        if plat in NETWORK_BY_PLATFORM:
            self.network_select.disabled = False
            nets = NETWORK_BY_PLATFORM[plat]
            current_net = (self.card.get("network") or "").strip() or None
            self.network_select.options = [
                discord.SelectOption(label=n, value=n, default=(n == current_net)) for n in nets
            ]
            if not self.network_select.options:
                # should never happen, but keep safe
                self._ensure_network_dummy()
        else:
            self.card["network"] = ""
            self._ensure_network_dummy()

    def _sync_defaults(self):
        # orientation defaults
        current_ori = set(self.card.get("orientation") or [])
        for opt in getattr(self.orientation_select, "options", []):
            opt.default = opt.value in current_ori

        # experience defaults
        exp = (self.card.get("experience") or "").strip() or None
        for opt in getattr(self.experience_select, "options", []):
            opt.default = (opt.value == exp)

        # platform defaults
        plat = (self.card.get("platform") or "").strip() or None
        for opt in getattr(self.platform_select, "options", []):
            opt.default = (opt.value == plat)

        # network depends on platform
        self._sync_network_options()

    # Row 1 (Select width 5)
    @discord.ui.select(placeholder="🎮 Orientierung (mehrfach)", min_values=0, max_values=4, row=1)
    async def orientation_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.card["orientation"] = list(select.values)
        self.touch()
        await self.refresh_message(interaction)

    # Row 2
    @discord.ui.select(placeholder="🎓 Erfahrung", min_values=0, max_values=1, row=2)
    async def experience_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.card["experience"] = select.values[0] if select.values else ""
        self.touch()
        await self.refresh_message(interaction)

    # Row 3
    @discord.ui.select(placeholder="🖥️ Plattform", min_values=0, max_values=1, row=3)
    async def platform_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.card["platform"] = select.values[0] if select.values else ""
        self.card["network"] = ""
        self.touch()
        self._sync_network_options()
        await self.refresh_message(interaction)

    # Row 4
    @discord.ui.select(placeholder="🔗 Netzwerk (Steam/PSN/...)", min_values=0, max_values=1, row=4)
    async def network_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        val = select.values[0] if select.values else ""
        if val == DUMMY_WAIT_VALUE:
            # ignore dummy
            return
        self.card["network"] = val
        self.touch()
        await self.refresh_message(interaction)

    # Row 0: Next button (fits as button width 1)
    @discord.ui.button(label="➡️ Weiter", style=discord.ButtonStyle.success, row=0)
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        next_view = SetcardEditViewPage2(self.member, self.card)
        content = next_view._header() + "\n\n" + next_view._status_lines()
        await interaction.response.edit_message(content=content, view=next_view)
        try:
            next_view.message = await interaction.original_response()
        except Exception:
            pass

# ----------------------------
# PAGE 2
# ----------------------------
class SetcardEditViewPage2(BaseSetcardView):
    def __init__(self, member: discord.Member, existing: dict | None):
        super().__init__(member, existing, page=2)
        self.age_select.options = [discord.SelectOption(label=o, value=o) for o in AGE_GROUP_OPTIONS]
        self.voice_select.options = [discord.SelectOption(label=o, value=o) for o in VOICE_OPTIONS]
        self._sync_defaults()

    def _sync_defaults(self):
        age = (self.card.get("age_group") or "").strip() or None
        for opt in getattr(self.age_select, "options", []):
            opt.default = (opt.value == age)

        voice = (self.card.get("voice") or "").strip() or None
        for opt in getattr(self.voice_select, "options", []):
            opt.default = (opt.value == voice)

    # Row 1
    @discord.ui.select(placeholder="🎂 Altersgruppe (optional)", min_values=0, max_values=1, row=1)
    async def age_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.card["age_group"] = select.values[0] if select.values else ""
        self.touch()
        await self.refresh_message(interaction)

    # Row 2
    @discord.ui.select(placeholder="🎧 Voice (optional)", min_values=0, max_values=1, row=2)
    async def voice_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.card["voice"] = select.values[0] if select.values else ""
        self.touch()
        await self.refresh_message(interaction)

    # Row 0: back
    @discord.ui.button(label="⬅️ Zurück", style=discord.ButtonStyle.secondary, row=0)
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        prev_view = SetcardEditViewPage1(self.member, self.card)
        content = prev_view._header() + "\n\n" + prev_view._status_lines()
        await interaction.response.edit_message(content=content, view=prev_view)
        try:
            prev_view.message = await interaction.original_response()
        except Exception:
            pass

    # Row 3: Save/Delete (buttons)
    @discord.ui.button(label="✅ Speichern", style=discord.ButtonStyle.success, row=3)
    async def btn_save(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        ok, msg = validate_embark_id(self.card.get("embark_id", ""))
        if not ok:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
            return

        plat = (self.card.get("platform") or "").strip()
        net = (self.card.get("network") or "").strip()

        if plat:
            if plat not in NETWORK_BY_PLATFORM:
                await interaction.followup.send("❌ Plattform ungültig.", ephemeral=True)
                return
            if not net:
                await interaction.followup.send("❌ Bitte wähle auch das Netzwerk (Steam/PSN/...).", ephemeral=True)
                return
            if net not in NETWORK_BY_PLATFORM[plat]:
                await interaction.followup.send("❌ Netzwerk passt nicht zur Plattform.", ephemeral=True)
                return
        else:
            self.card["network"] = ""

        self.touch()
        await upsert_card(self.guild_id, self.user_id, self.card)

        try:
            await ensure_setcard_post(interaction.guild, self.member, self.card)
        except Exception as e:
            print(f"⚠️ btn_save: ensure_setcard_post failed ({type(e).__name__}: {e})")

        embed = build_setcard_embed(self.member, self.card)
        await interaction.followup.send("✅ Setcard gespeichert!", embed=embed, ephemeral=True)

    @discord.ui.button(label="🗑️ Löschen", style=discord.ButtonStyle.danger, row=3)
    async def btn_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        deleted = await delete_card(self.guild_id, self.user_id)
        if deleted:
            try:
                await delete_setcard_post(interaction.guild, deleted)
            except Exception as e:
                print(f"⚠️ btn_delete: delete_setcard_post failed ({type(e).__name__}: {e})")

            await interaction.followup.send("🗑️ Setcard gelöscht.", ephemeral=True)

            if self.message:
                try:
                    await self.message.edit(content="🗑️ Setcard gelöscht. Editor geschlossen.", view=None)
                except Exception:
                    pass
        else:
            await interaction.followup.send("ℹ️ Du hast noch keine Setcard.", ephemeral=True)

# ============================================================
# FIND HELPERS
# ============================================================
def _match_card(card: dict,
                ori: list[str] | None,
                experience: str | None,
                platform: str | None,
                network: str | None,
                age_group: str | None,
                voice: str | None) -> bool:
    if ori:
        have = set(card.get("orientation") or [])
        want = set(ori)
        if not want.issubset(have):
            return False
    if experience and (card.get("experience") or "") != experience:
        return False
    if platform and (card.get("platform") or "") != platform:
        return False
    if network and (card.get("network") or "") != network:
        return False
    if age_group and (card.get("age_group") or "") != age_group:
        return False
    if voice and (card.get("voice") or "") != voice:
        return False
    return True

# ============================================================
# GROUP COG: /setcard ...
# ============================================================
class SetcardCog(commands.GroupCog, name="setcard"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    # ---------- CONFIG ----------
    @app_commands.command(name="set_channel", description="Setzt den Kanal, in dem Setcards gepostet werden.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send("❌ Nur auf einem Server nutzbar.", ephemeral=True)
            return

        await set_setcard_channel(interaction.guild.id, channel.id)
        await interaction.followup.send(
            f"✅ Setcard-Kanal gesetzt: {channel.mention}\nAb jetzt wird beim Speichern jede Setcard dort gepostet/aktualisiert.",
            ephemeral=True
        )

    # ---------- USER ----------
    @app_commands.command(name="edit", description="Bearbeite deine Raider-Setcard.")
    async def edit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.followup.send("❌ Nur auf einem Server nutzbar.", ephemeral=True)
            return

        existing = await get_card(interaction.guild.id, interaction.user.id)

        view = SetcardEditViewPage1(interaction.user, existing)
        content = view._header() + "\n\n" + view._status_lines()

        try:
            msg = await interaction.followup.send(content, ephemeral=True, view=view, wait=True)
            view.message = msg
        except Exception as e:
            print(f"⚠️ setcard edit: followup send failed ({type(e).__name__}: {e})")

    @app_commands.command(name="me", description="Zeigt deine Raider-Setcard an.")
    async def me(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.followup.send("❌ Nur auf einem Server nutzbar.", ephemeral=True)
            return

        card = await get_card(interaction.guild.id, interaction.user.id)
        if not card:
            await interaction.followup.send("ℹ️ Du hast noch keine Setcard. Starte mit `/setcard edit`.", ephemeral=True)
            return

        await interaction.followup.send(embed=build_setcard_embed(interaction.user, card), ephemeral=True)

    @app_commands.command(name="view", description="Zeigt die Raider-Setcard eines Users an.")
    async def view(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send("❌ Nur auf einem Server nutzbar.", ephemeral=True)
            return

        card = await get_card(interaction.guild.id, user.id)
        if not card:
            await interaction.followup.send("ℹ️ Diese Person hat noch keine Setcard.", ephemeral=True)
            return

        await interaction.followup.send(embed=build_setcard_embed(user, card), ephemeral=True)

    # ---------- MOD ----------
    @app_commands.command(name="mod_delete", description="(Mod) Löscht die Setcard eines Users.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def mod_delete(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send("❌ Nur auf einem Server nutzbar.", ephemeral=True)
            return

        deleted = await delete_card(interaction.guild.id, user.id)
        if not deleted:
            await interaction.followup.send("ℹ️ User hat keine Setcard.", ephemeral=True)
            return

        try:
            await delete_setcard_post(interaction.guild, deleted)
        except Exception as e:
            print(f"⚠️ mod_delete: delete_setcard_post failed ({type(e).__name__}: {e})")

        await interaction.followup.send(f"🗑️ Setcard von {user.mention} gelöscht.", ephemeral=True)

    # ---------- FIND ----------
    @app_commands.command(name="find", description="Findet Raider nach Filtern (Squad-Matching).")
    @app_commands.describe(
        orientation="Mehrfachauswahl (z.B. PvE,PvP) – Komma-getrennt",
        experience="Anfänger/Fortgeschritten/Alter Hase",
        platform="PC/Konsole",
        network="Steam/Epic/Other/PSN/Xbox",
        age_group="18+/25+/30+/40+/50+",
        voice="Ja/Nein/Optional"
    )
    async def find(
        self,
        interaction: discord.Interaction,
        orientation: str | None = None,
        experience: str | None = None,
        platform: str | None = None,
        network: str | None = None,
        age_group: str | None = None,
        voice: str | None = None
    ):
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send("❌ Nur auf einem Server nutzbar.", ephemeral=True)
            return

        ori_list: list[str] | None = None
        if orientation:
            raw = [x.strip() for x in orientation.split(",") if x.strip()]
            raw = [x for x in raw if x in ORIENTATION_OPTIONS]
            ori_list = raw if raw else None

        if experience and experience not in EXPERIENCE_OPTIONS:
            await interaction.followup.send(f"❌ experience ungültig. Erlaubt: {', '.join(EXPERIENCE_OPTIONS)}", ephemeral=True)
            return
        if platform and platform not in PLATFORM_OPTIONS:
            await interaction.followup.send(f"❌ platform ungültig. Erlaubt: {', '.join(PLATFORM_OPTIONS)}", ephemeral=True)
            return
        if age_group and age_group not in AGE_GROUP_OPTIONS:
            await interaction.followup.send(f"❌ age_group ungültig. Erlaubt: {', '.join(AGE_GROUP_OPTIONS)}", ephemeral=True)
            return
        if voice and voice not in VOICE_OPTIONS:
            await interaction.followup.send(f"❌ voice ungültig. Erlaubt: {', '.join(VOICE_OPTIONS)}", ephemeral=True)
            return

        all_networks = set(sum(NETWORK_BY_PLATFORM.values(), []))
        if network and network not in all_networks:
            await interaction.followup.send(f"❌ network ungültig. Erlaubt: {', '.join(sorted(all_networks))}", ephemeral=True)
            return

        if platform and network:
            allowed = set(NETWORK_BY_PLATFORM.get(platform, []))
            if network not in allowed:
                await interaction.followup.send("❌ network passt nicht zur platform.", ephemeral=True)
                return

        cards = await list_cards_in_guild(interaction.guild.id)

        results: list[dict] = []
        for card in cards:
            if _match_card(card, ori_list, experience, platform, network, age_group, voice):
                results.append(card)

        if not results:
            await interaction.followup.send("ℹ️ Keine Treffer.", ephemeral=True)
            return

        results = results[:25]

        lines = []
        for card in results:
            uid = int(card["user_id"])
            member = interaction.guild.get_member(uid)
            name = member.mention if member else f"<@{uid}>"
            emb = card.get("embark_id") or "—"
            ori = card.get("orientation") or []
            ori_str = "·".join(ori) if ori else "—"
            exp = card.get("experience") or "—"
            plat = card.get("platform") or "—"
            net = card.get("network") or "—"
            plat_str = f"{plat}/{net}" if plat and net else (plat or "—")
            lines.append(f"{name} — `{emb}` — {ori_str} — {exp} — {plat_str}")

        e = discord.Embed(
            title="🔎 Setcard Find",
            description="\n".join(lines),
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )

        filt = []
        if ori_list: filt.append("Orientierung: " + ", ".join(ori_list))
        if experience: filt.append("Erfahrung: " + experience)
        if platform: filt.append("Plattform: " + platform)
        if network: filt.append("Network: " + network)
        if age_group: filt.append("Alter: " + age_group)
        if voice: filt.append("Voice: " + voice)
        e.set_footer(text=(" | ".join(filt)) if filt else "Filter: (keine)")

        await interaction.followup.send(embed=e, ephemeral=True)

# ============================================================
# EXTENSION SETUP
# ============================================================
async def setup(bot: commands.Bot):
    await bot.add_cog(SetcardCog(bot))
