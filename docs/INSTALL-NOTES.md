# Hermes changes needed for MODI

(The installers `MODI-Setup.bat` / `MODI-Setup.command` do all of this for you; these notes document what they run.)

Findings come from reading the `NousResearch/hermes-agent` source/docs
(`gateway/platforms/api_server.py`, `website/docs/user-guide/features/api-server.md`).

## Required (3 commands, normal non-admin PowerShell / Terminal)
```powershell
hermes config set API_SERVER_ENABLED true
hermes config set API_SERVER_KEY <pick-a-long-random-string-16+-chars>
hermes gateway restart
```
(The installers run exactly these with a random key that is never printed. macOS also runs `hermes gateway install` if no gateway service exists - it becomes a launchd service.)

* These are *environment settings*: `hermes config set` writes UPPER_SNAKE keys into `.env` in Hermes' home (`%LOCALAPPDATA%\hermes` on Windows, `~/.hermes` on macOS/Linux), not `config.yaml`.
* `API_SERVER_KEY` is **mandatory** (≥16 chars, not a placeholder) even on loopback — the adapter refuses to start without it.
* Defaults you do **not** need to change: host `127.0.0.1`, port `8642`, no CORS (the bridge calls Hermes server-side, not from the browser).
* The bridge finds the key automatically by reading `API_SERVER_KEY` from that `.env` (also honours `HERMES_HOME`)
  (set `hermes.read_key_from_hermes_env` to `false` and fill `hermes.api_key` in `config.json` (in MODI's data folder) if you'd rather paste it).
* Verify: `curl http://127.0.0.1:8642/health` → JSON with `"status": "ok"`.
* Equivalent `config.yaml` form (optional): `gateway: { api_server: { enabled: true, key: ..., port: 8642 } }` — env vars win over yaml.

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
| Port 8642 already used | `hermes config set API_SERVER_PORT 8650`, then set `hermes.api_url` in `config.json` (in MODI's data folder) |
| Replies take too long | `hermes config set gateway.api_server.max_concurrent_runs 10` is already the default; long answers are mostly model latency |

## What does NOT need changing
* STT/TTS in Hermes' config (`stt`, `tts.provider: piper`) are untouched. The bridge runs its **own** faster-whisper and Piper
  (Hermes' STT/TTS code lives inside its own venv and isn't a clean standalone import). The bridge also looks for Piper voices in
  `%LOCALAPPDATA%\hermes\cache\piper-voices\` — if you already have `en_US-lessac-medium` there, set `tts.voice` to it to avoid a download.
* Wake word is not implemented yet (marked "not connected yet"); it is planned as the `wake_word` feature module.
* macOS: if Piper can't be installed, MODI speaks with the built-in `say` command (`tts.engine`: `say`).
