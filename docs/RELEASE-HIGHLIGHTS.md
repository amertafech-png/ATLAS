## ATLAS 1.4.2

**Fixes: no more Microsoft Edge "we are now syncing your browsing data" popup, and no console window behind the installer.**

- **No Edge sign-in/sync popup.** ATLAS's window now opens in your default browser if that is Chrome, Edge or Brave, and otherwise prefers Chrome over Edge. It always uses ATLAS's own private browser profile with browser sign-in and sync switched off, and on Edge it also turns off Edge's automatic Microsoft-account sign-in - the thing that showed "...has signed in on this device, so we've also signed you in to Microsoft Edge". The installer window does the same. ATLAS starts with a fresh private profile (your HUD notes and voice settings are carried over); the microphone permission may be asked once more.
- **No console window behind the installer.** Double-clicking ATLAS-Setup.bat now opens only the setup window (a black window may flash for a moment at the start). If the setup window can't open, setup carries on in the console as before. The background run is logged to `%TEMP%\ATLAS-setup.log`.
- The ATLAS taskbar icon and app ID from 1.4.1 keep working with the new profile.

## ATLAS 1.4.1

**Fixes: Hermes keeps running, an old bridge can no longer get in the way, Settings is always one click (or one key) away, links open in your default browser, and the taskbar shows the ATLAS icon.**

- **Hermes restarts by itself.** If Hermes (the AI engine ATLAS uses) stops - for example because the PowerShell window running `hermes gateway run` was closed - ATLAS starts it again in the background when you next talk to it, then answers. A background check also restarts it every so often (waiting longer between tries if it keeps failing). On Windows the gateway is started with no window, separate from ATLAS, so closing a window no longer stops it.
- **Plain-words errors.** Instead of "Can't reach the Hermes API ... ClientConnectorError", ATLAS says what happened ("Hermes had stopped - I'm starting it again") and offers **Fix it**.
- **The launcher replaces an old bridge.** If an older ATLAS/MODI bridge (for example 1.1.x) is still running on ATLAS's port, the Windows launcher stops it and starts the version you installed, instead of quietly talking to the old one.   <!-- legacy-modi -->
- **Settings is always reachable.** Click **SETTINGS** at the right end of the bottom bar, press **Ctrl+,** (Cmd+, on a Mac), or say "open settings" - this now works even when Hermes is not running. The "Connect Hermes" card has an **Open Settings** button too.
- **Links open in your default browser.** Sign-in pages, "Get a key" links and downloads now open in the browser you chose as your default (Chrome, Firefox, Edge, ...) on Windows and Mac, instead of inside the Edge/Chrome window ATLAS runs in. The ATLAS window itself still uses its own app window.
- **ATLAS icon on the Windows taskbar.** The ATLAS window now shows the ATLAS icon on the taskbar instead of a generic globe, and groups with the ATLAS Start Menu shortcut (run the installer once, or update, so the shortcuts get the matching ID).
- **Troubleshooting docs** for finding Settings, a stopped Hermes gateway, and an old bridge that is still running (see the user guide).

## ATLAS 1.4.0

**Swappable panels, a calendar, live scores, reminders and timers, safe add-on modules, app and game launching, and skins.**

- **Swappable panels.** The six panel slots are no longer fixed. Pick what goes in each one in **Settings -> Panel layout**, or say "replace the scanner with my calendar", "put the weather on the left", "reset my layout". Panels you are not using wait on a shelf. Your layout is saved.
- **Calendar panel.** Add a calendar file (.ics) or a Google/Outlook **secret address** link - no sign-in, nothing leaves your computer except the link you gave. "What's on my calendar today?" is answered out loud. Repeating events, all-day events and time zones work; links are https-only and refused if they point at your own network.
- **Live sports scores.** The Sports panel can show live and recent scores for your teams from ESPN's public scoreboard (only the league and date are sent; no account). Switch it off any time in Settings.
- **Daily brief.** "Give me my brief" reads the weather, today's calendar, your teams' games and your reminders. Optionally it does this by itself the first time you open ATLAS each morning.
- **Spoken reminders and timers.** "Remind me at 3 to call Mike", "remind me in 20 minutes to check the oven", "set a 10 minute timer", "what reminders do I have", "cancel the timer". They are saved, so a restart does not lose them, and they show on a small panel with a live countdown. When one is due ATLAS plays a chime and says it out loud (the chime can be switched off). If ATLAS was closed when one came due, you get it - marked as late - when you open it again.
- **Add-on modules, safely.** Optional extras are now behind one **master switch** (on by default, and it asks before turning off). ATLAS explains in plain words that add-ons run with the same access it has. A new or changed add-on **never runs until you approve it**: ATLAS scans its code for red flags, asks your own AI for a second opinion (clearly labelled as an opinion, not a guarantee), shows you the result, and only then lets you approve it. Approval is tied to the exact file, so a changed file needs approving again. Voice can open the review but never approve.
- **Computer control (add-on).** "Open Steam", "launch Hades", "play Hollow Knight on Steam". ATLAS looks at what is installed (Steam, Epic Games, the Start Menu on Windows, Applications on Mac), forgives small slips, asks "which one?" when two fit, and by default asks "Open X?" before it opens anything. It can only open things from that scan - no commands, no paths, no web addresses - and it cannot close, move or delete anything.
- **Skins and a living centre.** Four skins (cyan, ember, violet, emerald - "change the skin to ember"). The centre orb now reacts to what ATLAS is doing: thinking, searching, weather and music each look different (switch off with "turn off task animations").
- Updating from 1.3.x is automatic (Settings -> Updates). Your data and settings are kept.
