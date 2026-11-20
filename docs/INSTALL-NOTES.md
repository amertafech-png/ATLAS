# Hermes changes needed for ATLAS

(ATLAS, and its installers `ATLAS-Setup.bat` / `ATLAS-Setup.command`, do all of this for you; these notes document what they do.)

## The installer window (no console prompts)
Both installers show a **setup window** instead of console questions: Welcome, then an **Options** page (a checkbox per optional item with a one-line explanation and its download size), then a **step list with check marks**, a status line and a progress bar, then **Finish**, which opens ATLAS straight into its AI sign-in and the name/city/sports questions. Silent mode (`/update`, `--update`) shows nothing and reuses the saved choices (`install-options.json` in the data folder).
* Windows: the window is Edge/Chrome in app mode, served by the installer itself (`HttpListener` on a random `127.0.0.1` port, with a random token in the URL and a Host-header check). macOS: the same page served by `src/mac/ui-server.pl` (Perl ships with macOS).
* Hermes (detect, install, API key, start) and Ollama are installed inside this window. The first-run marker `<data>/first-run` makes ATLAS open into the sign-in; it is deleted when you finish the questions.
* Options and defaults: Python (required if missing) on; Install Hermes (~1 GB) on if not found; Install Ollama (~1.9 GB Windows / ~230 MB macOS) **off**; speech models (~210 MB) on; wake-word model (~55 MB) **off**; CUDA (~1 GB) only if an NVIDIA GPU is detected, **off**; desktop shortcut on; start at sign-in on.

Findings come from reading the `NousResearch/hermes-agent` source/docs
(`gateway/platforms/api_server.py`, `website/docs/user-guide/features/api-server.md`).

## You do not have to do anything by hand
The first time ATLAS opens it checks Hermes for you and shows a **Connect Hermes** card with a **Fix it** button if anything is missing:

1. **Hermes installed?** If not, one click runs Hermes' official installer (Windows: `install.ps1`, macOS: `install.sh --non-interactive`) and shows its progress.
2. **API key + API server.** ATLAS adopts the key Hermes already has; if there is none it makes a long random one (`atlas-...`), turns the API server on and saves both in Hermes' own files. Nothing is typed or printed. The old file is backed up first (`.env.atlas-backup-<time>`, the last five are kept).
3. **Restart Hermes** (`hermes gateway ensure`, falling back to `gateway run` in the background) and check `http://127.0.0.1:8642/health`.
4. **Your AI.** ATLAS asks which one you use and signs you in through Hermes' own commands: **ChatGPT** (`hermes auth add openai-codex`, browser sign-in), **Claude** (key, or a Max-plan sign-in), **Grok** (SuperGrok / X Premium sign-in, or an xAI API key), **OpenRouter** (key), or a **local model** (Ollama - ATLAS detects it, lists your models, can pull one, and sets the 64k context Hermes needs). It then sends a test message and shows "Connected to <your AI>".
   You can change it any time in **Settings -> AI connection**.

### Where Hermes keeps things
| What | Windows | macOS / Linux |
|---|---|---|
| Hermes home (`HERMES_HOME` overrides it) | `%LOCALAPPDATA%\hermes` | `~/.hermes` |
| API server on/key (`API_SERVER_ENABLED`, `API_SERVER_KEY`), provider API keys | `.env` in that folder | `.env` in that folder |
| Sign-in tokens for ChatGPT / Grok / Claude (OAuth) | `auth.json` in that folder | `auth.json` in that folder |
| Model and provider choice | `config.yaml` in that folder | `config.yaml` in that folder |
`hermes config env-path` prints the exact `.env` path. ATLAS never copies a key into its own files unless you ask it to (see below), and never shows one in the app, logs or errors.

### The same thing by hand (only if the automatic setup cannot work for you)
```powershell
hermes config set API_SERVER_ENABLED true
hermes config set API_SERVER_KEY <pick-a-long-random-string-16+-chars>
hermes gateway restart
```
and then, as before, `hermes auth add <provider>` / `hermes model` to sign in to your AI.

* These are *environment settings*: `hermes config set` writes UPPER_SNAKE keys into `.env`, dotted keys such as `model.provider` into `config.yaml`.
* `API_SERVER_KEY` is **mandatory** (long, not a placeholder) even on loopback - the adapter refuses to start without it.
* Defaults you do **not** need to change: host `127.0.0.1`, port `8642` (ATLAS follows `API_SERVER_PORT` if you change it), no CORS (the bridge calls Hermes server-side, not from the browser).
* The bridge finds the key by itself: first the key Hermes is actually using (environment, then `.env`, then `config.yaml`), then `hermes.api_key` in ATLAS's `config.json`, then the copy it saved when it set things up.
  Set `hermes.read_key_from_hermes_env` to `false` if you would rather paste the key into `config.json` (in ATLAS's data folder) yourself.
* Verify: `curl http://127.0.0.1:8642/health` -> JSON with `"status": "ok"`. (`/health` needs no key; `/v1/models` needs it.)
* Equivalent `config.yaml` form (optional): `gateway: { api_server: { enabled: true, key: ..., port: 8642 } }` - env vars win over yaml.

## What ATLAS downloads
ATLAS itself is one file you downloaded yourself (`ATLAS-Setup.bat` / `ATLAS-Setup.command`, about 1.3 MB; it carries the app inside). Everything below is fetched **only if you do not already have it** (and, for optional items, only if you leave them ticked on the Options page), and each item says when. Sizes are approximate.
This list was written from the code (`bash tools/list-downloads.sh` prints every URL, pip, winget, brew and model-pull in the source; run it after changing anything and keep this table in step).

| What | What it is for | When | Size | From | Stored in | Optional? |
|---|---|---|---|---|---|---|
| **Python 3.11+** | Runs ATLAS's local bridge | Install, only if no Python 3.11+ is found. Windows: `winget install Python.Python.3.12` (asks first), else opens python.org. macOS: `brew install python@3.12` (asks first); if there is no Homebrew you can let the installer fetch Homebrew's official script, or get Python from python.org yourself | ~25-30 MB (Python) | Windows: winget / python.org. macOS: Homebrew (`raw.githubusercontent.com/Homebrew/install`, bottles from ghcr.io) / python.org | System location chosen by winget / Homebrew / the python.org installer | No (ATLAS needs Python), but you can install it yourself first |
| **ATLAS's Python packages** (`bridge/requirements.txt`): `aiohttp`, `psutil`, `faster-whisper` (pulls `ctranslate2`, `onnxruntime`, `tokenizers`, `huggingface_hub`, `numpy` and a few small helpers), `av`, `piper-tts` | The bridge, speech-to-text, text-to-speech | Install, and again on update when `requirements.txt` changed | ~500 MB in total (the app's own Python environment) | PyPI (`pypi.org`, `files.pythonhosted.org`) through `pip` | `<ATLAS data folder>\app\.venv` | No. (macOS: if `piper-tts` cannot be installed, ATLAS skips it and uses the built-in macOS voice) |
| **Hermes Agent** (the assistant's brain) | Runs your AI, tools and memory; ATLAS talks to its local API | Install step 2, or the "Install Hermes" button on ATLAS's first screen - only if `hermes` is not found. Asks first | ~1 GB on disk (measured: 1.1 GB on Linux) | Hermes' own official installer: `https://hermes-agent.nousresearch.com/install.ps1` (Windows) / `install.sh` (macOS). That installer is Nous Research's, not ATLAS's: it clones `github.com/NousResearch/hermes-agent`, fetches a pinned `uv` (`github.com/astral-sh/uv`, checksum-checked), its Python packages from PyPI, a Node.js runtime and pinned assets from `hermes-assets.nousresearch.com`, `git` if missing (macOS: Homebrew or Apple's Command Line Tools dialog) and an optional browser (Chromium) for its web tools. See its script for the exact list | Hermes home: `%LOCALAPPDATA%\hermes` (Windows), `~/.hermes` (macOS) | Skip it if you already use Hermes. Without Hermes ATLAS has no AI |
| **Whisper speech model** (`base`) | Turns your voice into text, on your computer | Install (asks; "No" = first time you talk to ATLAS) | ~150 MB | Hugging Face (`huggingface.co`, repo `Systran/faster-whisper-base`) through `faster-whisper` | Hugging Face cache: `%USERPROFILE%\.cache\huggingface` (Windows), `~/.cache/huggingface` (macOS) | Needed for voice input. Typing works without it |
| **Piper voice** (`en_GB-alan-medium`) | ATLAS's spoken voice | Install (asks; "No" = first time it speaks) | ~60 MB | Hugging Face (`huggingface.co`, repo `rhasspy/piper-voices`) through `piper.download_voices` | `<ATLAS data folder>\voices` | Optional: macOS can use its built-in `say` voice instead; set `tts.download_if_missing` to `false` to forbid the download |
| **CUDA libraries** (`nvidia-cublas-cu12`, `nvidia-cudnn-cu9`) | Lets speech recognition use an NVIDIA GPU | Install, **only** if `nvidia-smi` is found **and** you answer yes (default no) | ~1 GB | PyPI | The same `.venv` | Optional. The CPU is already fast for the base model |
| **`vosk` package + Vosk speech model** `vosk-model-small-en-us-0.15` | The optional "Hey ATLAS" wake word (runs fully offline) | **Only when you switch Wake word on** in Settings -> Features (ATLAS asks you to confirm first) | package ~15 MB; model ~41 MB (SHA-256 checked, bad files are discarded) | package: PyPI. Model: `https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip` | `<ATLAS data folder>\features\wake_word\model` | Yes - off by default |
| **Ollama** | Only if you want a local model as your AI | **Only if you tick "Install Ollama for local models" on the Options page (default off)**, or install it yourself (the sign-in step links to `https://ollama.com/download`). Windows: `https://ollama.com/download/OllamaSetup.exe`, run silently. macOS: `https://ollama.com/download/Ollama-darwin.zip`, unpacked into `~/Applications`. Both are Ollama's official downloads | Windows ~1.9 GB, macOS ~230 MB | `ollama.com` | Chosen by Ollama's installer (Windows) / `~/Applications` (macOS) | Yes |
| **A local model** (any Ollama model you pick, e.g. `llama3.1:8b`) | The AI itself when you pick the local option | Only when you press Connect / Download for a model Ollama does not have yet. ATLAS also creates a small copy of the model with a 64k context window (`ollama create`, no download) because Hermes needs that | 2-10+ GB depending on the model | Ollama's model library (via your own Ollama, `registry.ollama.ai`) | Ollama's model folder | Yes |
| **ATLAS updates** | New versions of ATLAS | A check roughly every 12 hours (turn off in Settings -> Updates), the **Update** button downloads - nothing installs without you clicking. Release assets must match the release's `SHA256SUMS` or they are refused, and only `github.com` / `githubusercontent.com` addresses are accepted | ~1.3 MB per installer | `https://api.github.com/repos/amertafech-png/atlas/releases/latest` and that release's `ATLAS-Setup.*` files | A temporary file, then replaces the app in place | Yes (turn updates off) |

**The app window is not downloaded.** ATLAS uses the Microsoft Edge (or Google Chrome) already on your PC in "app mode" - no WebView or browser runtime is installed. On macOS it uses Chrome, Edge, Brave or Chromium if present, otherwise your default browser.

**Signing in to your AI** (ChatGPT, Claude, Grok, OpenRouter) is done by Hermes with that provider's own sign-in page; ATLAS downloads nothing for it. Your messages go from Hermes to the provider you picked, exactly as when you use Hermes alone.

### Other network traffic (not downloads)
* **Weather and city search:** when you set a city, the HUD asks `api.open-meteo.com` (forecast, by latitude/longitude) and `geocoding-api.open-meteo.com` (the city name you typed). No account, no key; nothing else is sent.
* **Update check:** `api.github.com` (see above). It sends no personal data (only a `User-Agent: ATLAS/<version>` header).
* **Your AI provider:** only through Hermes, as described above.
* The HUD page loads **no** outside scripts, fonts, analytics or images. Everything except the items above stays on `127.0.0.1` (your own computer).
* **Nothing else is sent anywhere** - no telemetry, no crash reports, no usage statistics, and your voice, camera frames, notes and API keys never go to ATLAS's author or to any service except your own Hermes (and, from there, your own AI provider).

## How the bridge talks to it
* `POST /v1/chat/completions` with `Authorization: Bearer <key>`, `model: "hermes-agent"`, non-streaming.
* **Continuity:** header `X-Hermes-Session-Id: jarvis-hud` on every call → Hermes keeps one transcript (history is loaded from its own session DB). "New" in the Chat module starts `jarvis-hud-<timestamp>`.
  (This id must not collide with a CLI/TUI/messaging session — hence the dedicated name.)
* **Vision:** user message `content` = `[{type:"text"}, {type:"image_url", image_url:{url:"data:image/jpeg;base64,…"}}]` — documented as supported on `/v1/chat/completions`.
  Hermes routes it by `agent.image_input_mode` (`auto` default): a vision-capable main model gets the image natively; a text-only model (for example a local `hermes3:8b` fallback) makes Hermes run `vision_analyze` first and use the text description.
* A short system message ("speak concisely, plain text…") is layered on top of Hermes' own prompt for voice-friendly answers; tools/memory/skills stay available.
* Hermes' API also offers `/v1/responses`, `/v1/runs` (+ SSE `/events`) and `/api/sessions/*`; the bridge uses only chat/completions for simplicity.

## Optional, only if something needs it
| Situation | Command |
|---|---|
| Vision says the model can't see images, but your main model does support them | `hermes config set model.supports_vision true` |
| You want vision to also work while a local fallback model such as `hermes3` is active (it has no vision) | `hermes config set auxiliary.vision.provider codex` — uses your existing ChatGPT/Codex login (if you have one), which the Hermes example config says supports vision. Caveat: an explicit `auxiliary.vision` backend makes Hermes describe images as text first (lossy) even when the main model could see them, so only set it if you need it. Leave at `auto` otherwise. |
| Port 8642 already used | `hermes config set API_SERVER_PORT 8650`, then set `hermes.api_url` in `config.json` (in ATLAS's data folder) |
| Replies take too long | `hermes config set gateway.api_server.max_concurrent_runs 10` is already the default; long answers are mostly model latency |

## What does NOT need changing
* STT/TTS in Hermes' config (`stt`, `tts.provider: piper`) are untouched. The bridge runs its **own** faster-whisper and Piper
  (Hermes' STT/TTS code lives inside its own venv and isn't a clean standalone import). The bridge also looks for Piper voices in
  `%LOCALAPPDATA%\hermes\cache\piper-voices\` — if you already have `en_US-lessac-medium` there, set `tts.voice` to it to avoid a download.
* Wake word is not implemented yet (marked "not connected yet"); it is planned as the `wake_word` feature module.
* macOS: if Piper can't be installed, ATLAS speaks with the built-in `say` command (`tts.engine`: `say`).
