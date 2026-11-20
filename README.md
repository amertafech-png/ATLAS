# MODI

**Your own Jarvis-style assistant. Download one file, double-click, done.**

No coding, no terminal, no complicated setup. MODI installs everything it needs on its own. You just sign in with the AI you already use, like ChatGPT, Claude, or Grok, and start talking.

![MODI](docs/screenshot.png)

> MODI is an independent community project. It is not made by or affiliated with Nous Research; "Hermes Agent" belongs to its authors.

## Download

Get the installer for your system from the **[latest release](https://github.com/amertafech-png/modi/releases/latest)**:

| System | File | Direct link |
|---|---|---|
| Windows 10 / 11 | `MODI-Setup.bat` | [download](https://github.com/amertafech-png/modi/releases/latest/download/MODI-Setup.bat) |
| macOS 11+ (Intel & Apple Silicon) | `MODI-Setup.command` | [download](https://github.com/amertafech-png/modi/releases/latest/download/MODI-Setup.command) |

### Windows
1. Download `MODI-Setup.bat` and double-click it.
2. **SmartScreen warning ("Windows protected your PC")?** The file isn't code-signed. Click **More info -> Run anyway**.
3. Follow the prompts (it asks before every big step). When it finishes, open **MODI** from the Desktop or Start Menu.

### macOS
1. Download `MODI-Setup.command`.
2. **Gatekeeper warning ("can't be opened because it is from an unidentified developer")?** Right-click the file -> **Open** -> **Open**.
   Or in Terminal: `xattr -d com.apple.quarantine ~/Downloads/MODI-Setup.command`, then double-click it.
3. Follow the prompts in the Terminal window. When it finishes, open **MODI** from `~/Applications` (Launchpad / Spotlight).
4. If macOS says `MODI.app` can't be opened: right-click it -> **Open**, or `xattr -dr com.apple.quarantine ~/Applications/MODI.app`.

You can verify a download against the `SHA256SUMS` file attached to the release (`shasum -a 256 MODI-Setup.command` /
`certutil -hashfile MODI-Setup.bat SHA256`).

## What you need
* **Windows 10/11** or **macOS 11+**, about 2 GB free disk space, and an internet connection for the install.
* **Your own AI account.** MODI doesn't include or resell any AI. The installer sets up Hermes Agent and runs `hermes setup`, where **you** sign in with
  your own provider (for example a ChatGPT/OpenAI, Anthropic or OpenRouter account - whatever Hermes supports) or choose a free local model via Ollama.
  Usage is billed by your provider, not by MODI.
* A microphone (voice) and optionally a webcam (vision). Microsoft Edge or Google Chrome (Windows) / Chrome, Edge or Brave (macOS) is used for the app window;
  without one MODI opens in your default browser.
* Python 3.11+ - the installer checks and offers to install it for you.

## What it does
* **Talk to it:** push-to-talk (Space), local speech recognition (faster-whisper) and a local voice (Piper; macOS `say` as a fallback).
* **Show it things:** the camera panel sends one frame to Hermes only when you press **Look**.
* **Live system tiles**, a chat history shared with your voice conversations, a focus timer and notes.
* **Updates itself** (with your click): an *Update* button appears when a new release is published; downloads are checked against a SHA-256 before running.
* **Wake word (optional, off by default):** turn it on in Settings -> Features and just say **"Hey MODI"** (or "Hey <the name you gave it>") - fully offline, no account or key. See [GUIDE](docs/GUIDE.md#wake-word).
* **More feature switches** (Settings): daily brief, reminders, computer control, hand gestures and phone access are planned as optional modules.

* **Made for you:** name your assistant (it ships as MODI; rename it to anything, 1-20 characters), and tell it your name, city, favorite sports and teams (installer or Settings). Weather and the globe follow your city (free Open-Meteo, no key). The Sports tile groups your teams by sport and can ask Hermes for the latest result on demand - there is no live score feed yet, and MODI never invents scores.

Known limits today: no live scores feed; the wake word needs a reasonably quiet room and works best with distinctive names; the other modules above are not built yet.

## Privacy
* MODI runs on your computer. The HUD and bridge listen on `127.0.0.1` only and refuse requests from other websites.
* **No data is sent to the MODI author or any MODI server. There is no telemetry, no account and no analytics.**
* Your voice is transcribed on your machine. With the wake word on, your microphone stays open while MODI's window is open; that audio is analysed on your computer only, never saved or sent anywhere, and you can mute it with one click. Text you say or type goes to **Hermes Agent on your computer**, which sends it to **the AI provider you chose** (same as using Hermes in a terminal).
* Network connections MODI itself makes: GitHub (`api.github.com`, to check for updates - disable under Settings -> Updates), Hugging Face (one-time speech model download), alphacephei.com (one-time ~41 MB wake-word model, only if you switch the wake word on),
  and open-meteo.com (city lookup and weather for the city you entered). Webcam frames are only sent when you press Look.
* Conversation history is stored locally in MODI's data folder (`%LOCALAPPDATA%\MODI` or `~/Library/Application Support/MODI`). Uninstalling can delete it.
* Hermes' local API key is generated on your machine during install and is never printed or uploaded.

## License
MIT - free to use, modify and share, including commercially. See [LICENSE](LICENSE). Copyright (c) 2026 Amer Tafech.

## Uninstall
Run the installer again and choose **Uninstall** (or `MODI-Setup.bat uninstall` / `MODI-Setup.command --uninstall`). Hermes Agent itself is not removed.

## More
* [User guide](docs/GUIDE.md) - settings, data folders, troubleshooting, feature modules
* [Contributing](CONTRIBUTING.md) - [Security policy](SECURITY.md) - [Releasing](RELEASING.md) - [License: MIT](LICENSE)
