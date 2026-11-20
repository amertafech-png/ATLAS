# ATLAS

**Your own Jarvis-style assistant. Download one file, double-click, done.**

No coding, no terminal, no complicated setup. ATLAS installs everything it needs on its own. You just sign in with the AI you already use, like ChatGPT, Claude, or Grok, and start talking.

![ATLAS](docs/screenshot.png)

> ATLAS is an independent community project. It is not made by or affiliated with Nous Research; "Hermes Agent" belongs to its authors.

## Download

Get the installer for your system from the **[latest release](https://github.com/amertafech-png/atlas/releases/latest)**:

| System | File | Direct link |
|---|---|---|
| Windows 10 / 11 | `ATLAS-Setup.bat` | [download](https://github.com/amertafech-png/atlas/releases/latest/download/ATLAS-Setup.bat) |
| macOS 11+ (Intel & Apple Silicon) | `ATLAS-Setup.command` | [download](https://github.com/amertafech-png/atlas/releases/latest/download/ATLAS-Setup.command) |

### Windows
1. Download `ATLAS-Setup.bat` and double-click it.
2. **SmartScreen warning ("Windows protected your PC")?** The file isn't code-signed. Click **More info -> Run anyway**.
3. Follow the prompts (it asks before every big step). When it finishes, open **ATLAS** from the Desktop or Start Menu.

### macOS
1. Download `ATLAS-Setup.command`.
2. **Gatekeeper warning ("can't be opened because it is from an unidentified developer")?** Right-click the file -> **Open** -> **Open**.
   Or in Terminal: `xattr -d com.apple.quarantine ~/Downloads/ATLAS-Setup.command`, then double-click it.
3. Follow the prompts in the Terminal window. When it finishes, open **ATLAS** from `~/Applications` (Launchpad / Spotlight).
4. If macOS says `ATLAS.app` can't be opened: right-click it -> **Open**, or `xattr -dr com.apple.quarantine ~/Applications/ATLAS.app`.

You can verify a download against the `SHA256SUMS` file attached to the release (`shasum -a 256 ATLAS-Setup.command` /
`certutil -hashfile ATLAS-Setup.bat SHA256`).

## What you need
* **Windows 10/11** or **macOS 11+**, about 2 GB free disk space, and an internet connection for the install.
* **Your own AI account.** ATLAS doesn't include or resell any AI. The installer sets up Hermes Agent, and the first time ATLAS opens it asks which AI **you** use
  (ChatGPT, Claude, Grok, OpenRouter, or a free local model via Ollama) and signs you in through Hermes - no files to edit. You can change it later in Settings -> AI connection.
  Usage is billed by your provider, not by ATLAS.
* A microphone (voice) and optionally a webcam (vision). Microsoft Edge or Google Chrome (Windows) / Chrome, Edge or Brave (macOS) is used for the app window;
  without one ATLAS opens in your default browser.
* Python 3.11+ - the installer checks and offers to install it for you.

## What ATLAS downloads
Only what is missing on your computer, and the installer asks first. Full details (sizes, sources, locations) are in [docs/INSTALL-NOTES.md](docs/INSTALL-NOTES.md#what-atlas-downloads).

| What | For | When | Size | From | Optional? |
|---|---|---|---|---|---|
| Python 3.11+ | runs ATLAS | install, if not found | ~30 MB | winget / python.org (Windows), Homebrew / python.org (macOS) | no |
| ATLAS's Python packages (aiohttp, psutil, faster-whisper, av, piper-tts) | bridge, speech | install | ~500 MB | PyPI | no |
| Hermes Agent (+ its own dependencies) | your AI's brain | install or first run, if not found | ~1 GB | `hermes-agent.nousresearch.com` (Hermes' official installer) | skip if you already have it |
| Whisper `base` speech model | voice input | install or first use | ~150 MB | Hugging Face | needed for voice |
| Piper voice `en_GB-alan-medium` | spoken replies | install or first use | ~60 MB | Hugging Face | yes (macOS can use its own voice) |
| CUDA libraries | GPU speech recognition | install, only if you have an NVIDIA GPU and say yes | ~1 GB | PyPI | yes |
| `vosk` + Vosk model | "Hey ATLAS" wake word | only when you turn it on | ~55 MB | PyPI, `alphacephei.com` | yes, off by default |
| Ollama + a local model | a free local AI | only if you choose a local model (ATLAS never installs Ollama; it links to `ollama.com/download`) | 2-10+ GB | `ollama.com` | yes |
| ATLAS updates | new versions | update check about every 12 h; you click Update | ~1.3 MB | GitHub Releases (checksum-verified) | yes |

ATLAS uses the Edge/Chrome already on your computer for its window (nothing is installed for that). Apart from the downloads above, the weather tile calls `open-meteo.com`, and Hermes talks to the AI provider you picked. **Nothing else is sent anywhere.**

## What it does
* **Talk to it:** push-to-talk (Space), local speech recognition (faster-whisper) and a local voice (Piper; macOS `say` as a fallback).
* **Show it things:** the camera panel sends one frame to Hermes only when you press **Look**.
* **Live system tiles**, a chat history shared with your voice conversations, a focus timer and notes.
* **Updates itself** (with your click): an *Update* button appears when a new release is published; downloads are checked against a SHA-256 before running.
* **Wake word (optional, off by default):** turn it on in Settings -> Features and just say **"Hey ATLAS"** (or "Hey <the name you gave it>") - fully offline, no account or key. See [GUIDE](docs/GUIDE.md#wake-word).
* **More feature switches** (Settings): daily brief, reminders, computer control, hand gestures and phone access are planned as optional modules.

* **Made for you:** name your assistant (it ships as ATLAS; rename it to anything, 1-20 characters), and tell it your name, city, favorite sports and teams (installer or Settings). Weather and the globe follow your city (free Open-Meteo, no key). The Sports tile groups your teams by sport and can ask Hermes for the latest result on demand - there is no live score feed yet, and ATLAS never invents scores.

Known limits today: no live scores feed; the wake word needs a reasonably quiet room and works best with distinctive names; the other modules above are not built yet.

## Privacy
* ATLAS runs on your computer. The HUD and bridge listen on `127.0.0.1` only and refuse requests from other websites.
* **No data is sent to the ATLAS author or any ATLAS server. There is no telemetry, no account and no analytics.**
* Your voice is transcribed on your machine. With the wake word on, your microphone stays open while ATLAS's window is open; that audio is analysed on your computer only, never saved or sent anywhere, and you can mute it with one click. Text you say or type goes to **Hermes Agent on your computer**, which sends it to **the AI provider you chose** (same as using Hermes in a terminal).
* Network connections ATLAS itself makes: GitHub (`api.github.com`, to check for updates - disable under Settings -> Updates), Hugging Face (one-time speech model download), alphacephei.com (one-time ~41 MB wake-word model, only if you switch the wake word on),
  and open-meteo.com (city lookup and weather for the city you entered). Webcam frames are only sent when you press Look.
* Conversation history is stored locally in ATLAS's data folder (`%LOCALAPPDATA%\ATLAS` or `~/Library/Application Support/ATLAS`). Uninstalling can delete it.
* Hermes' local API key is generated on your machine during install and is never printed or uploaded.

## License
MIT - free to use, modify and share, including commercially. See [LICENSE](LICENSE). Copyright (c) 2026 Amer Tafech.

## Uninstall
Run the installer again and choose **Uninstall** (or `ATLAS-Setup.bat uninstall` / `ATLAS-Setup.command --uninstall`). Hermes Agent itself is not removed.

## More
* [User guide](docs/GUIDE.md) - settings, data folders, troubleshooting, feature modules
* [Contributing](CONTRIBUTING.md) - [Security policy](SECURITY.md) - [Releasing](RELEASING.md) - [License: MIT](LICENSE)
