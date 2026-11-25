## ATLAS 1.4.4

**The Sports panel keeps itself up to date, and calendar questions use the same Google sign-in as your email.**

- **Scores refresh on their own.** While one of your teams is playing, the Sports panel asks again about every 15 seconds. When it is only showing a final or an upcoming game, it waits about 5 minutes. You don't have to restart ATLAS or press refresh.
- **Finals and later games stay visible.** A weekend result, or a game a few days out, no longer disappears just because it isn't yesterday or today. The panel shows the live score during a game, and the latest final or the next game otherwise.
- **A dead score service doesn't blank the panel.** If ESPN doesn't answer, the last scores stay on screen with a small **stale** mark. Only the sports and teams you still follow are requested, and nothing is invented.
- **Calendar uses the Google sign-in that already reads your email.** Ask what's on today, what's tomorrow, or what's your next meeting. ATLAS reads Google Calendar with the token Hermes saved for Gmail. If that sign-in doesn't include the calendar, say **connect Google** once. It opens in your usual browser, and the permission stays on this computer. An .ics file or a secret calendar link still works.

## ATLAS 1.4.3

**Fixes: panels no longer disappear after a long answer, calendar questions use ATLAS's own calendar, and there is always a way to reset the screen.**

- **Panels stay on screen.** A reply containing one very long web address (such as a Google sign-in link) made the middle of the screen as wide as the link, pushing the right-hand panels, the orb and the bottom bar off screen - and it came back after a reload because the last answer is shown again. The layout now keeps its shape whatever the reply, and long links are shown shortened (the full link is still behind it in the chat).
- **Calendar questions use ATLAS's calendar.** "Check last week's calendar", "what meetings did I have yesterday", "am I busy on Friday", "what's on my schedule next week" are answered straight from the calendar in **Settings -> Calendar** (past ranges such as yesterday, last week, last weekend, last month and "last Tuesday" now work). "Reconnect my calendar" explains that it is already connected - no Google sign-in. Other calendar questions go to your AI together with the events from ATLAS, with instructions not to use its own Google connectors and never to search your files for passwords or keys. If no calendar is set up, ATLAS points you to Settings -> Calendar.
- **Reset, always within reach.** A **Reset** button next to the ATLAS title (also **Ctrl+Shift+R**, or say "reset ATLAS" / "reload the screen" - works even if Hermes is down) offers **Reload screen** and **Reset layout to default** (your calendar, profile and skin are kept). **Settings -> Reset** has the same plus **Restart ATLAS services** (the bridge and Hermes).
- **A broken part can't take down the whole screen.** Each part of the screen now starts separately; if one fails, the rest still loads and a banner offers **Reload**. Screen errors are written to `bridge.log` so they can be diagnosed.
- **Safer settings saves.** The settings file is now written in one step (never half-written) and changes are merged, so two saves at once can't wipe each other out. An unreadable settings file is kept aside instead of being overwritten.

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
