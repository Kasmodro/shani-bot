# Changelog – Shani Bot
Alle relevanten Änderungen am Shani-Bot | All relevant changes to Shani Bot

---

## [1.1.0] – 2025-12-29
### 🇩🇪 Deutsch
✨ **YouTube & Stabilität**
- **YouTube Live-Alerts:** Neues Modul zur Erkennung von YouTube-Live-Streams ohne API-Key. Unterstützt Handles (z. B. `@@kasmodrocorvus7248`) und Channel-IDs.
- **Angleichung an Twitch:** YouTube-Alerts bieten nun dieselben Konfigurationsmöglichkeiten (Stable-Checks, Polling-Rate, Offline-Grace) wie das Twitch-Modul.
- **Verbesserte Status-Anzeige:** Die Refreshrate, Stable-Checks und Offline-Verzögerung werden nun sowohl in `/shani_status` als auch direkt in den Setup-Menüs für Twitch und YouTube angezeigt.
- **Zweisprachiges Changelog:** Dokumentation nun konsistent in Deutsch und Englisch.

✨ **Konsolen-Support & Anpassbarkeit**
- **Anpassbarer Bot-Name:** Admins können den Anzeigenamen des Bots im Menü und in den Embeds nun ändern.
- **Interaktive Feineinstellungen:** Neue Buttons in den Setup-Menüs für Twitch und YouTube ermöglichen das direkte Einstellen von Stable-Checks, Polling-Rate und Offline-Grace via Modal-Dialog.
- **Slash-Command Dokumentation:** Anleitung zur manuellen Änderung des Hauptbefehls `/shani` in der README ergänzt.
- **Button-basierte Squad-Erstellung:** Neuer Befehl `/squad` und Button im `/shani` Menü für Konsolenspieler.
- **Automatischer 2-Minuten-Cleanup:** Leere Kanäle werden nach 2 Minuten gelöscht.
- **Modulare Struktur:** Twitch-Logik in `modules/twitch.py` ausgelagert.

🛠️ **Fehlerbehebungen**
- **Datenbank-Migration:** Automatische Migration für `bot_custom_name` und YouTube-Spalten (inkl. neuer Konfigurationsoptionen) hinzugefügt.

### 🇺🇸 English
✨ **YouTube & Stability**
- **YouTube Live Alerts:** New module for detecting YouTube live streams without an API key. Supports handles (e.g., `@@kasmodrocorvus7248`) and channel IDs.
- **Consistency with Twitch:** YouTube alerts now offer the same configuration options (stable checks, polling rate, offline grace) as the Twitch module.
- **Improved Status Display:** Polling rate, stable checks, and offline grace are now displayed in `/shani_status` as well as directly within the Twitch and YouTube setup menus.
- **Bilingual Changelog:** Documentation now consistently provided in German and English.

✨ **Console Support & Customization**
- **Customizable Bot Name:** Admins can now change the bot's display name in menus and embeds.
- **Interactive Fine-tuning:** New buttons in Twitch and YouTube setup menus allow direct configuration of stable checks, polling rate, and offline grace via modal dialogs.
- **Slash Command Documentation:** Added instructions to README for renaming the `/shani` command.
- **Button-based Squad Creation:** New `/squad` command and button in `/shani` menu for console players.
- **Automatic 2-Minute Cleanup:** Unused channels are deleted after 2 minutes.
- **Modular Structure:** Moved Twitch logic to `modules/twitch.py`.

🛠️ **Bug Fixes**
- **Database Migration:** Added automatic migration for `bot_custom_name` and YouTube columns (including new configuration options).

---

## [1.0.0] – 2025-12-29
### 🇩🇪 Deutsch
✨ **Das interaktive UI-Update**
- **Hauptmenü:** `/shani` als zentrale Schaltstelle.
- **Admin-Setup:** Komplette Einrichtung über Buttons & Menüs.
- **Raider-Suche:** Neue Filter (Plattform, Erfahrung, Orientierung).
- **Auto-Voice:** Setcard-Post im Channel-Textchat.

### 🇺🇸 English
✨ **Interactive UI Update**
- **Main Menu:** `/shani` as the central hub.
- **Admin Setup:** Full configuration via buttons & menus.
- **Raider Search:** New filters (Platform, Experience, Orientation).
- **Auto-Voice:** Post setcards in channel text chat.

---

[0.9.2] – 2025-12-27 (Aktuelles Update)
✨ Berechtigungs-System & Shani-Menü

• **Rollenbasiertes System:** Einführung von Admin-, Mod- und Setcard-Rollen zur feingranularen Zugriffskontrolle (`/shani_setup_roles`).
• **Shani Hauptmenü:** Neuer zentraler Befehl `/shani` mit dynamischen Buttons, die sich der Benutzerrolle anpassen.
• **Kanal-Status:** Squad-Ersteller können jetzt den Voice-Status (z.B. "Suche Loot") setzen, ohne das User-Limit ändern zu können.
• **Sichtbarkeit:** Administrative Befehle werden für normale User in der Discord-Befehlsliste jetzt automatisch ausgeblendet.

🛠️ Voice- & Cleanup-Fixes
• **Aggressives Cleanup:** Neuer Scan-Mechanismus für die Voice-Kategorie, der "Leichen" (leere Kanäle) zuverlässig entfernt.
• **Kompatibilitäts-Fix:** Behebung von `Invalid permissions` Fehlern bei älteren discord.py Versionen (betreffend `set_voice_channel_status`).
• **Stabilität:** Behebung von Datenbank-Fehlern (`Missing Column`) durch automatische Tabellen-Migration.

[0.9.1] – 2025-12-27
🛡️ Sicherheit & Voice-Feinschliff

• **Schutz des Squad-Limits:** User erhalten keine `manage_channels` Rechte mehr in Squad-Channels. Dies verhindert das manuelle Umgehen der 2er/3er Begrenzung.
• **Moderation:** Squad-Besitzer behalten das Recht, andere User zu verschieben oder zu kicken (`move_members`).
• **Auto-Voice Open:** Einführung eines "Open Join"-Channels für Squads ohne Teilnehmerbegrenzung.

[0.9.0] – 2025-12-27
✨ System-Modernisierung & Feature-Erweiterung

• **Migration zu SQLite:** Komplette Umstellung der Server-Konfiguration von JSON auf eine robuste SQLite-Datenbank.
• **Auto-Voice 2.0:** Erweiterung des Squad-Systems auf wählbare Typen (2er, 3er).
• **Zentraler Status-Check:** Neuer Befehl `/shani_status` zeigt die gesamte Bot-Konfiguration auf einen Blick.
• **GitHub Integration:** Professionelle Repository-Struktur mit `README.md`, `.gitignore` und `requirements.txt`.

🛠️ Technische Optimierungen
• **Asynchrone Datenbankzugriffe:** Alle DB-Operationen laufen nun asynchron über Threads, um die Event-Loop nicht zu blockieren.
• **Performance-Schub für Twitch:** Umstellung auf eine persistente `aiohttp.ClientSession` und verbesserte Browser-Header für zuverlässigeres Scraping.
• **Professionelles Logging:** Einführung eines Datei-basierten Loggings (`bot.log`) statt einfacher Print-Ausgaben.
• **Echtzeit-Rename:** Automatische Umbenennung von Squad-Channels bei Namensänderungen der Besitzer.

🛡️ Fixes & Stabilität
• **Command-Cleanup:** Neues Skript `cleanup_commands.py` zur Behebung von doppelten Slash-Commands.
• **Intents:** Aktivierung des `message_content` Intents für bessere Command-Verarbeitung.
• **Voice-Stabilität:** Behebung von 404-Fehlern beim Löschen von Kanälen durch Entzerrung der Event-Logik.
• **Sicherheit:** `.gitignore` schützt nun `.env` und Datenbank-Dateien vor öffentlichem Upload.

---

[0.8.0] – 2025-12-26 Stand 14:00 Uhr
✨ Neues Feature: Raider-Setcard-System

• Einführung eines vollständigen Raider-Setcard-Systems für ARC Raiders
• Spieler können ein persönliches Profil erstellen und bearbeiten
• Fokus auf Squad-Matching ohne Preisgabe sensibler Daten

🛠️ Setcard-Funktionen (User)
• /setcard edit – interaktiver Editor (2-seitig, stabil)
• /setcard me – eigene Setcard anzeigen
• /setcard view – Setcard anderer Raider ansehen
• /setcard find – Raider-Suche mit Filtern (privat)
• Löschen der eigenen Setcard direkt im Editor

🛡️ Admin- & Mod-Funktionen
• /setcard set_channel – Setcard-Zielkanal festlegen
• /setcard mod_delete – Setcards von Usern entfernen
• Rechteprüfung & klare Fehlerausgaben bei fehlenden Channel-Rechten

⚙️ Technische Verbesserungen
• Umstellung auf SQLite mit WAL-Modus (stabil & performant)
• Vollständig überarbeitetes Discord-UI (keine Row-/Width-Crashes)
• Zwei-seitige View-Struktur für bessere Übersicht
• Sichere Interaction-Handling-Logik (kein „Bot denkt nach…“ mehr)
• Robustes Error-Handling & Debug-Logging

🔐 Datenschutz & Sicherheit
• Keine Verifizierung notwendig
• Keine externen Dienste
• Altersangaben nur als Altersgruppen
• Alle Angaben freiwillig und jederzeit änderbar

🐛 Fixes
• Mehrere Discord-UI-Crashes behoben (Row-/Width-/Options-Fehler)
• Slash-Command-Hänger („Anwendung reagiert nicht“) behoben
• Fehlende Channel-Rechte sauber abgefangen (403 Missing Access)

---

[0.7.0] – 2025-12-26
Added
• Konzept für Raider-Setcards (Spielerprofile)
• Planung für standardisierte Spielerinfos

---

[0.6.0] – 2025-12-26
Added
• Konzept „Missionshilfe“ für Anwender
• Fokus auf benutzerfreundliche Bot-Nutzung
• Vorbereitung einer Nutzer-Dokumentation

---

[0.5.0] – 2025-12-26
Changed
• Analyse des Twitch-Live-Systems
• Entfernung des Cooldown-Gedankens
• Neue Zieldefinition: Nur ein Live-Ping pro Stream

---

[0.4.0] – 2025-12-25
Fixed
• Analyse und Lösung von Discord-Permissions-Problemen
• Klärung von 403 Forbidden Fehlern

---

[0.3.0] – 2025-12-25
Added
• Automatische Erstellung von Sprachkanälen (Squads)
• Automatisches Verschieben des Channel-Erstellers

---

[0.2.0] – 2025-12-24
Added
• Öffentliche Bot-Applikation (Public Bot)
• OAuth2 / Invite-Flow geklärt
• Bot-Identität: Shani (Security & Missionshilfe)

---

[0.1.0] – 2025-12-24
Added
• Initialer Discord-Bot erstellt
• Betrieb auf Hetzner-Server
• Python-Virtualenv eingerichtet
