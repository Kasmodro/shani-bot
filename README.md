# Shani Bot

Ein leistungsstarker Discord-Bot für die Verwaltung von Raider-Setcards, dynamische Auto-Voice Channels (2er, 3er, Open) und Twitch-Live-Alerts ohne API-Key. Nun mit vollständig interaktivem UI über Slash-Commands und Buttons.

## 🚀 Features

### ✨ Interaktive Benutzeroberfläche
*   **Zentrales Menü:** Der Befehl `/shani` ist der einzige Einstiegspunkt, den User und Admins brauchen. Alles lässt sich über Buttons und Menüs steuern.
*   **Geführtes Setup:** Admins können den Bot über das "Admin Setup" Menü konfigurieren (Rollen, Kanäle, Twitch) – kein Auswendiglernen von Befehlen nötig.

### 🛠️ Raider-Setcards
*   **Individuelle Profile:** User können ihre Gaming-Infos (Embark ID, Plattform, Erfahrung, Spielstil) hinterlegen.
*   **Interaktive Suche:** Finde Mitspieler direkt über das `/shani` Menü mit Filtern wie Spielstil, Plattform oder Erfahrung.
*   **Intelligentes Matching:** Die Suche versteht Teilbegriffe und erlaubt Mehrfachauswahl bei den Interessen.
*   **Automatische Posts:** Setcards werden in einem konfigurierten Kanal gepostet und bei Änderungen automatisch aktualisiert.

### 🔊 Auto-Voice 2.0 (Squad Channels)
*   **Drei Modi:** Dedizierte Join-Channels für **2er Squads**, **3er Squads** und **Open Squads** (unbegrenzt).
*   **Setcard-Integration:** Der Bot postet automatisch die Setcard des Squad-Leiters in den Textchat des Voice-Channels, damit beigetretene Spieler sofort wissen, mit wem sie spielen.
*   **Eingeschränkte Rechte:** User können das Squad-Limit nicht mehr manipulieren, behalten aber Moderationsrechte (Kicken/Moven) und können den **Voice-Status** setzen.
*   **Intelligenter Cleanup:** Aktiver Scan der Voice-Kategorie sorgt dafür, dass leere Kanäle sofort und zuverlässig gelöscht werden.

### 🟣 Twitch Live-Alerts (No-API)
*   **Einfaches Setup:** Keine Registrierung bei der Twitch-API nötig. Konfiguration bequem über das Admin-Menü.
*   **Automatisches Editieren:** Live-Nachrichten werden bei Stream-Ende automatisch in Offline-Meldungen umgewandelt.

### 🔐 Rollen- & Berechtigungssystem
*   **Hauptmenü:** Zentraler Einstiegspunkt über `/shani` mit rollenbasierter Button-Anzeige.
*   **Admin- & Mod-Rollen:** Konfigurierbare Rollen für erweiterten Zugriff auf Bot-Funktionen.
*   **Sichtbarkeit:** Administrative Befehle sind für normale User in Discord unsichtbar.

## 📋 Voraussetzungen
*   Python 3.12+
*   `discord.py`
*   `aiohttp`
*   `python-dotenv`
*   `PyNaCl` (für Voice Support)

## ⚙️ Installation

1.  **Repository klonen:**
    ```bash
    git clone https://github.com/Kasmodro/shani-bot-beta.git
    cd shani-bot
    ```

2.  **Abhängigkeiten installieren:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Konfiguration (.env Datei):**
    Der Bot benötigt einen Discord-Token, um online zu gehen. Diesen speicherst du sicher in einer Datei namens `.env`.
    
    *   Erstelle im Hauptverzeichnis des Bots eine neue Datei mit dem Namen `.env`:
        ```bash
        touch .env
        ```
    *   Öffne die Datei (z. B. mit `nano .env`) und füge deinen Bot-Token ein:
        ```env
        DISCORD_TOKEN=DEIN_BOT_TOKEN_HIER_EINSETZEN
        ```
    *   *Hinweis:* Die `.env` Datei wird von Git ignoriert, damit dein Token nicht öffentlich auf GitHub landet.

4.  **Bot starten:**
    ```bash
    python3 bot.py
    ```

## 🛠️ Bedienung

### Für Admins
Nutze `/shani` und klicke auf **"Admin Setup"**. Dort kannst du schrittweise Rollen, Kanäle und Twitch konfigurieren.

### Für User
Nutze `/shani`, um deine **Setcard zu bearbeiten** oder nach **Raidern zu suchen**.

## 🧹 Fehlerbehebung (Doppelte Commands)
Falls Slash-Commands doppelt angezeigt werden, führe einmalig das Bereinigungs-Skript aus:
```bash
python3 cleanup_commands.py
```
Danach den Bot neu starten und Discord (Strg+R) aktualisieren.

## 📄 Lizenz
Dieses Projekt ist für den privaten Gebrauch auf Discord-Servern bestimmt.

---
### 🆘 Support
Bei Fragen oder Problemen kannst du gerne dem Discord-Server beitreten:
[https://discord.gg/UhhJtFteun](https://discord.gg/UhhJtFteun)
