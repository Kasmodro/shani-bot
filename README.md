# Shani Bot – ARC Raiders Discord Bot

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python: 3.12+](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Release: v1.0.0](https://img.shields.io/badge/Release-v1.0.0-green.svg)](https://github.com/Kasmodro/shani-arc-raiders-bot/releases)

Shani is a modern, feature-rich Discord bot built specifically for **ARC Raiders** communities. It simplifies squad creation, player matching, and stream notifications — all through a fully interactive Discord UI.

This project is open source, beginner-friendly, and designed to be easy to run on your own server.

---

## ✨ What Shani Does
Shani improves your ARC Raiders Discord server by providing:
*   🎧 **Automatic Voice Squads** (2-player, 3-player, or open)
*   🧾 **Raider Setcards** (player profiles & matchmaking)
*   🟣 **Twitch Live Alerts** (no Twitch API key required)
*   🔴 **YouTube Live Alerts** (scraping-based, no API key required)
*   🧭 **Interactive UI** using Slash Commands & Buttons
*   🔒 **No administrator permissions required**

---

## ⚡ Quick Start (Beginner Friendly)
This guide assumes basic Linux knowledge. Works on VPS, root servers, or local machines.

### 1️⃣ Clone the Repository
```bash
git clone https://github.com/Kasmodro/shani-arc-raiders-bot.git
cd shani-arc-raiders-bot
```

### 2️⃣ Create a Virtual Environment (Recommended)
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3️⃣ Install Dependencies
```bash
pip install -r requirements.txt
```

### 4️⃣ Configure the Bot Token
Copy the example config:
```bash
cp .env.example .env
```
Edit the file (e.g., using `nano`):
```bash
nano .env
```
Insert your Discord bot token:
```env
DISCORD_TOKEN=YOUR_DISCORD_BOT_TOKEN_HERE
```
> 🔐 **Important:** The `.env` file is ignored by Git. Never commit your token. Never share it.

### 5️⃣ Start the Bot
```bash
python3 bot.py
```
If everything is correct, Shani will appear online in Discord.

---

## 🔗 Invite Shani to Your Server
Use the official invite link:
[**Invite URL**](https://discord.com/api/oauth2/authorize?client_id=1319253457599041537&permissions=285223056&scope=bot%20applications.commands)

### ⚠️ Permissions Notice
*   **Administrator permission is NOT required.**
*   Shani only requests minimum necessary permissions.
*   No hidden or dynamic permission escalation.

### 🔐 Required Discord Permissions
Shani needs these permissions to function correctly:
*   Manage Channels
*   Move Members
*   Send Messages
*   Embed Links
*   Read Message History
*   Manage Threads
*   Manage Messages *(bot-owned content only)*
*   Connect / Speak *(voice features)*

> 📌 Make sure the bot also has these permissions inside the target categories. For a detailed breakdown, see **[PERMISSIONS.md](PERMISSIONS.md)**.

---

## 🚀 Main Features

### 🎧 Auto Voice Channels
Separate join channels for **2-Player Squads**, **3-Player Squads**, and **Open Squads**.
*   **Button Support (Consoles):** Squads can be created via buttons using `/squad` or the `/shani` menu — perfect for console players who cannot easily use join-channels.
*   **Smart Cleanup:** Channels created but not joined are automatically deleted after **2 minutes** to keep the server organized.
*   Channels are created dynamically.
*   Empty channels are removed automatically.

### 🧾 Raider Setcards
Players create personal profiles including playstyle, platform, and experience.
*   Profiles are searchable via interactive menus.
*   Setcards auto-update when edited.

### 🔊 Voice Channel Integration
*   Squad leader’s setcard is posted automatically in the channel's text chat.
*   Players instantly know who they’re joining.
*   Users cannot manipulate squad limits.
*   Leaders retain moderation controls.

### 🟣 Twitch Live Alerts (No API)
*   No Twitch API registration required.
*   Setup via admin menu.
*   Live messages auto-update when stream ends.

### 🔴 YouTube Live Alerts (No API)
*   Scraping-based detection (supports handles like `@name` or Channel IDs).
*   Setup via admin menu.
*   Automatic live notification and offline status update.

### 🧭 Interactive UI
*   One command: `/shani`
*   Buttons & menus only — no command spam.
*   **Customizable Bot Name:** Server admins can change Shani's display name via the admin menu.
*   Admin features are hidden from regular users.

---

## 🛠️ Admin Usage
1.  Run `/shani`
2.  Click **Admin Setup**
3.  Configure Roles, Channels, Twitch, **YouTube**, or **Change Bot Name**.
Everything is guided — no memorizing commands.

### ⚙️ Changing the Slash Command Name
If you want to rename the `/shani` command to match your bot's name (e.g., `/menu` or `/raider`):
1.  Open `bot.py`.
2.  Find the line starting with `@bot.tree.command(name="shani", ...)` (around line 813).
3.  Change `name="shani"` to your preferred name (lowercase, no spaces).
4.  Restart the bot.
5.  Reload Discord (**CTRL + R**).

## 👤 User Usage
1.  Run `/shani`
2.  Edit your **Raider Setcard**.
3.  Search for other players or join squads via voice channels.

---

## 🧹 Fix: Duplicate Slash Commands
If you ever see duplicate commands:
```bash
python3 cleanup_commands.py
```
Then:
1.  Restart the bot.
2.  Reload Discord (**CTRL + R**).

---

## 🖥️ Run Shani as a System Service (Optional)
Recommended for VPS / 24-7 servers.

### 1. Create Service File
```bash
sudo nano /etc/systemd/system/shani.service
```

### 2. Example Configuration
```ini
[Unit]
Description=Shani Discord Bot
After=network.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER/shani-arc-raiders-bot
ExecStart=/usr/bin/python3 bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

### 3. Enable & Start
```bash
sudo systemctl daemon-reload
sudo systemctl enable shani
sudo systemctl start shani
```

---

## 🤝 Open Source & Forks
This project is intentionally open source. You are free to:
*   Fork the repository.
*   Modify the code.
*   Run your own version.
*   Adapt it for other communities.

✔ No permission required.  
✔ Keep the MIT license & credits.  
If you build something cool — share it 🚀

---

## 🇩🇪 Zusammenfassung (Deutsch)

Shani ist ein moderner Discord-Bot, der speziell für **ARC Raiders** Communities entwickelt wurde. Er automatisiert die Erstellung von Squad-Channels, bietet Spieler-Profile (Setcards) und Twitch-Live-Benachrichtigungen.

### 🎧 Auto-Voice Squads
*   **Button-Support (Konsolen):** Squads können über Buttons in `/squad` oder dem `/shani` Menü erstellt werden – ideal für Konsolenspieler.
*   **Smart Cleanup:** Kanäle, die erstellt, aber nicht betreten wurden, werden nach **2 Minuten** automatisch gelöscht.
*   Dynamische Kanäle für 2er, 3er oder offene Squads.
*   🧾 **Raider Setcards:** Detaillierte Spielerprofile und Suche nach Mitspielern.
*   🟣 **Twitch Live-Alerts:** Benachrichtigungen ohne Twitch-API-Key.
*   🔴 **YouTube Live-Alerts:** Benachrichtigungen via Scraping (ohne API-Key).
*   🧭 **Interaktive UI:** Einfache Bedienung über Buttons und Menüs.
*   🏷️ **Anpassbarer Name:** Der Bot-Name kann über das Admin-Menü geändert werden.
*   🔒 **Sicherheit:** Keine Administrator-Rechte erforderlich.

### ⚡ Schnelleinrichtung
1.  Repository klonen: `git clone https://github.com/Kasmodro/shani-arc-raiders-bot.git`
2.  Virtuelle Umgebung erstellen: `python3 -m venv venv && source venv/bin/activate`
3.  Abhängigkeiten installieren: `pip install -r requirements.txt`
4.  `.env` Datei erstellen (siehe `.env.example`) und Bot-Token eintragen.
5.  Bot starten: `python3 bot.py`

### ⚙️ Slash-Command Namen ändern
Wenn du den `/shani` Befehl umbenennen möchtest (z.B. in `/menu` oder `/start`):
1.  Öffne die Datei `bot.py`.
2.  Suche die Zeile `@bot.tree.command(name="shani", ...)` (ca. Zeile 813).
3.  Ändere `name="shani"` in deinen Wunschnamen (nur Kleinbuchstaben, keine Leerzeichen).
4.  Starte den Bot neu.
5.  Starte Discord neu oder drücke **STRG + R**.

### 🆘 Support
Bei Fragen oder Problemen besuche uns auf unserem [Discord Server](https://discord.gg/UhhJtFteun).

---

## 🛡️ Disclaimer
This software is provided **"as is"**, without warranty. Server owners are responsible for bot configuration, assigned permissions, and usage within their server.
The author is not liable for moderation issues, data loss, Discord ToS violations, or misconfiguration.

---

## 📄 License
Licensed under the **MIT License**. Free to use, modify, and distribute with attribution.

---

## 🆘 Support
*   🐞 **Bug reports & feature requests** → [GitHub Issues](https://github.com/Kasmodro/shani-arc-raiders-bot/issues)
*   💬 **Questions & discussion** → [Discord Server](https://discord.gg/UhhJtFteun)
