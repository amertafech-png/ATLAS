# MODI user guide

A full-screen Iron-Man-style interface that talks to **your own Hermes Agent**. It opens as its own
app window (no browser tabs/address bar). Everything stays local: the HUD is served from `127.0.0.1`, speech-to-text
(faster-whisper) and text-to-speech (Piper, or the macOS `say` voice) run on your machine. The only thing that leaves
is whatever Hermes itself sends to its model provider - same as using `hermes` in a terminal.

```
 MODI window (Edge/Chrome app mode)  ->  bridge.py (127.0.0.1:8765)  ->  Hermes API server (127.0.0.1:8642)
  mic / camera / orb / tiles             STT + TTS + feature modules      Hermes: tools, memory, vision, fallbacks
```

## Install - one file per OS
| OS | File | How |
|---|---|---|
| **Windows 10/11** | `MODI-Setup.bat` | Double-click. SmartScreen: *More info -> Run anyway* (unsigned). |
| **macOS 11+ (Intel or Apple Silicon)** | `MODI-Setup.command` | Double-click. Gatekeeper: right-click -> *Open* -> *Open*, or `xattr -d com.apple.quarantine MODI-Setup.command`. |

Both installers: check Python 3.11+ (offer to install), install Hermes with its official installer if missing
(`install.ps1` / `install.sh` from hermes-agent.nousresearch.com), run `hermes setup` (your own AI login), optionally set up
Ollama, enable Hermes' local API with a **fresh random key that is never printed**, restart the gateway, install MODI, download
the speech models, and create the launchers. **Re-running the installer updates MODI in place** (your settings, notes, models and
Hermes config are kept). Run it and pick *Uninstall* (or `--uninstall` / `uninstall`) to remove MODI; Hermes is never touched.

### What gets installed
| | Windows | macOS |
|---|---|---|
| App files + Python env | `%LOCALAPPDATA%\MODI\app` | `~/Library/Application Support/MODI/app` |
| Your data (config.json, state, models, logs, window profile) | `%LOCALAPPDATA%\MODI` | `~/Library/Application Support/MODI` |
| Launcher | Desktop + Start Menu **MODI** shortcut (own icon), *Stop MODI* in Start Menu | `~/Applications/MODI.app` (own icon) |
| Window | Edge `--app` (falls back to Chrome, then default browser) | Chrome / Edge / Brave / Chromium `--app` (falls back to default browser) |
| Optional login item | Startup-folder shortcut (bridge only) | LaunchAgent `com.modi.bridge` (bridge only) |
| Hermes data (read for the API key) | `%LOCALAPPDATA%\hermes` | `~/.hermes` |

Starting MODI starts the bridge hidden if needed, waits for `/api/health`, then opens the window. Closing the window leaves the bridge
running (fast re-open); the power button in the top bar (or *Stop MODI*) stops it. macOS note: the Dock icon while MODI is open is the
browser's icon (Chromium app mode limitation); Safari-only Macs get a normal browser tab (Safari: *File -> Add to Dock* for an app-like window).

## Using it
| Action | How |
|---|---|
| **Talk** | Click the orb / mic button, or press **Space**. Stops after ~1.4 s of silence (or Space again). **Esc** cancels. |
| **Hear the answer** | Spoken with Piper (British "alan" by default; macOS falls back to `say` if Piper can't install). Speaker button toggles. |
| **Type** | Bottom bar -> Enter. The **Chat** module shows the whole conversation (voice + typed share one Hermes session). |
| **Show it something** | Camera button -> preview -> optional question -> **Look**. |
| **Settings** | Dock -> **Settings**: switch optional features on/off (see below). |

## Settings - `config.json`
Defaults live in `bridge/config.json`; put your overrides in **`<data dir>/config.json`** (survives updates; same keys, only what you change):
`mode` (`api`/`cli`), `hermes.api_url`/`api_key` (empty = auto-read `API_SERVER_KEY` from Hermes' `.env`), `stt.model|device|language`,
`tts.engine` (`auto`/`piper`/`say`), `tts.voice`, `tts.say_voice`, `tts.say_rate`, `port`. Env overrides: `JARVIS_PORT`, `JARVIS_MODE`,
`HERMES_API_URL`, `HERMES_API_KEY`, `MODI_HOME` (data dir).
GPU speech-to-text (Windows/NVIDIA, optional): the installer offers it; or set `"stt": {"device": "cuda"}` after
`pip install nvidia-cublas-cu12 nvidia-cudnn-cu9` in the app's `.venv`. CPU is fast enough for the `base` model.

## Your profile (assistant name, your name, city, sports)
The installer asks (all skippable); change everything any time in the HUD: dock -> **Settings** -> **Profile** -> **Save profile**.
* **Assistant name** - ships as **MODI**; rename it (1-20 characters: letters, numbers, spaces, `. ' -`). The new name is used in the window title, headers, greetings, captions, chat and the instructions sent to Hermes ("You are <name>"). It is also stored with a wake phrase (`Hey <name>`) for a future wake-word feature. The app, install folders and shortcuts stay named MODI so updates keep working. Leave it empty to go back to MODI.
* **Your name** - used in greetings.
* **City** - resolved with Open-Meteo's free geocoder (no key) into latitude, longitude, timezone and units (Fahrenheit/mph in the US, Celsius/km/h elsewhere); type `City, State/Country` to disambiguate, or pick one of the suggestions. With no city the weather tile is greyed out with "Set your city in Settings".
* **Sports** - pick your sports (Soccer, NFL, NBA, MLB, NHL, College football, F1, or type your own under "Other"), then add favorite teams for each (type or pick a suggestion; Soccer and College football can also take leagues/conferences such as Premier League or Serie A).
  The Sports tile groups your teams by sport with an **Ask Hermes** button for the latest result (no live scores feed yet; MODI never invents scores).

Stored in `config.json`: `assistant` (`name`, `wake_phrase`) and `profile` (`name`, `city`, `label`, `lat`, `lon`, `timezone`, `country`, `units`, `sports: [{sport, leagues, teams}]`).
The status tile reads your model name from Hermes' `config.yaml`; `hermes.display_primary|display_fallback|display_next_job` override or add rows.

## Updates
MODI checks the project's GitHub Releases shortly after it starts and then about every 12 hours (cached; uses GitHub's free,
unauthenticated API and conditional requests so it stays far below rate limits). When a newer version exists an **Update vX** button
appears in the top bar with the release notes. Clicking **Install update** downloads the installer for your OS from the release,
**verifies its SHA-256** (from the release's `SHA256SUMS` file or the release notes - no checksum, no install), runs it silently
(`MODI-Setup.bat /update` / `MODI-Setup.command --update`: your data, settings and Hermes setup are kept) and restarts MODI; the open
window reloads by itself. Offline or no releases yet: it just says "Couldn't check" / "up to date". Turn automatic checks off in
**Settings -> Updates**, or in `config.json`: `"update": {"enabled": false}`. Other keys: `update.check_hours`, `update.repo` (default is
set in `bridge/modi_meta.py`). 

## Wake word
Off by default. Switch it on in **Settings -> Features -> Wake word** (you are asked to confirm because it opens your microphone). Then say **"Hey MODI"** - or **"Hey <name>"** if you renamed the assistant in Settings -> Profile (the phrase follows the name, no training needed). MODI plays a short tone, the orb shows the listening animation, and it carries on exactly like the mic button: you speak, it answers out loud.
* **Local and private:** the HUD window opens the mic and streams only short bursts of speech (silence is never sent) to the bridge on `127.0.0.1`, where a small offline speech model ([Vosk](https://alphacephei.com/vosk/), Apache-2.0) turns it into text that is compared with the phrase and thrown away. Nothing is recorded, saved or uploaded. No account, no API key.
* **First use:** a ~41 MB model (`vosk-model-small-en-us-0.15`, SHA-256 verified) is downloaded once into `<data dir>/features/wake_word/` with a progress bar in Settings and in the **Wake** chip under the orb; the `vosk` pip package is installed from the Install button (or by the installer). After that it works offline.
* **Indicator and mute:** the chip under the orb shows **Hey MODI** with a pulsing green dot while the microphone is armed (your browser also shows its own recording indicator). Click the chip - or Settings -> Mute - to mute: the microphone is released completely. It pauses by itself while MODI is listening, thinking or speaking, so it never answers itself.
* **Accuracy tips:** speak the phrase naturally, then pause for the tone before the command (anything you say before the tone is not heard by the command recording). Names that are real words in the speech model (MODI, Nova, Jarvis, Friday ...) work best; made-up names are matched fuzzily. If a name keeps being misheard, add what it sounds like under **Also accept** (e.g. `moody`).
* **Limits:** English only (small model); it needs the MODI window open (it lives in the HUD, so MODI cannot hear you if the window is closed); a noisy room, a far-away mic or speakers playing loudly lower accuracy; CPU use is near zero in silence and roughly a quarter of one core while someone is talking.
* **Files:** `bridge/modules/wake_word.py` (engine, model download, WebSocket `/api/m/wake_word/ws`, mute/aliases/status endpoints) and `wake_word.js` (microphone capture, speech gate, tone, Settings controls). Tests: `bridge/dev/test_wake.py`.

## Feature modules
`bridge/modi_features.py` loads optional modules from `bridge/modules/<id>.py` (shipped) and `<data dir>/modules/<id>.py` (drop-ins that survive updates).
Each module gets a `ctx` (settings, private data dir, `say`, `notify`, `ask` Hermes, `confirm` Yes/No dialog, `emit` events to the HUD) and may ship
`<id>.js` to add a dock panel via `MODI.register(...)`. Settings toggles are in the HUD; state is saved in `config.json` under `features`.
See `bridge/modules/_example.py`. `wake_word.py` is the reference module with a UI. Other catalogued ids (shown as "coming soon" until a module exists): `daily_brief`, `reminders`,
`computer_control`, `gestures`, `telegram`. Missing pip packages of an enabled feature are installed from the HUD ("Install") or by re-running the installer.

## Security notes
* The bridge listens on **127.0.0.1 only** and rejects any request whose `Host`/`Origin` isn't `127.0.0.1:<port>` / `localhost:<port>`.
  The Hermes API can run commands, so keep `API_SERVER_HOST` at its default `127.0.0.1`.
* Audio is processed in memory/temp files and deleted at once; webcam frames are sent only when you press **Look**.
  Conversation text is kept in `<data dir>/state.json` (delete to clear).
* Features that act on your computer must ask through `ctx.confirm()` for anything destructive; with no HUD open, confirmations are denied.

## Troubleshooting
| Symptom | Fix |
|---|---|
| Nothing opens / "bridge did not start" | Look at `<data dir>/logs/bridge.log` (and `bridge.err.log` on Windows). Re-run the installer. |
| "Hermes unreachable" | API server not enabled / gateway not running. `curl http://127.0.0.1:8642/health`; `hermes gateway status`; re-run the installer step 5. |
| "Hermes rejected the API key" | Key in Hermes' `.env` changed; restart the bridge (power button, then reopen MODI). |
| Mic/camera blocked | Windows: Settings -> Privacy -> Microphone/Camera; macOS: System Settings -> Privacy & Security -> allow your browser (Chrome/Edge). |
| First voice turn slow | Speech models still downloading/loading. |
| Vision says it can't see | Active model must support images, or Hermes needs an `auxiliary.vision` backend (see INSTALL-NOTES.md). |
| `gateway_elevated` (Windows) | Gateway was started as administrator; stop it in an admin window and start it from a normal one. |
| macOS: "MODI.app can't be opened" | Right-click -> Open, or `xattr -dr com.apple.quarantine ~/Applications/MODI.app`. |

## Folder layout (source)
```
index.html                     the HUD
bridge/bridge.py               local bridge (STT, TTS, Hermes calls, features API)
bridge/modi_meta.py            version + GitHub repo (one place)   bridge/modi_update.py   self-updater
bridge/modi_features.py        feature-module framework      bridge/modules/   feature modules (wake_word.py/.js, _example.py)
bridge/config.json, requirements.txt
win/MODI.ps1                     Windows launcher              mac/modi-launch.sh   macOS launcher
assets/modi.ico|png|icns       icons                         tools/make_icons.py, tools/build.py  (build the installers)
bridge/dev/                    test tools only (mock Hermes, test_update.py, test_wake.py, screenshot scripts)
```

## License
MIT (see `LICENSE`).
