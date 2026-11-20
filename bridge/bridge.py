#!/usr/bin/env python3
"""
Jarvis HUD bridge  --  local-only glue between the HUD web page and Hermes Agent.

  * serves the HUD (../index.html) on http://127.0.0.1:8765
  * POST /api/chat    text            -> Hermes (conversation continuity via X-Hermes-Session-Id)
  * POST /api/listen  recorded audio  -> local speech-to-text (faster-whisper) -> transcript
  * POST /api/look    webcam JPEG     -> Hermes vision -> answer
  * POST /api/speak   text            -> Piper wav
  * GET  /api/system  live CPU / RAM / disk / GPU / uptime
  * GET  /api/health  bridge, Hermes, STT and TTS status
  * GET  /api/history, POST /api/new  conversation transcript / start a fresh session

Binds to 127.0.0.1 only and rejects foreign Host / Origin headers (the Hermes API can run
terminal commands, so a random web page must never be able to talk to this bridge).
Settings: config.json next to this file (env overrides: JARVIS_PORT, JARVIS_MODE, HERMES_API_URL, HERMES_API_KEY).
"""
import asyncio
import base64
import copy
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from datetime import datetime, timedelta
from pathlib import Path

import psutil
from aiohttp import ClientError, ClientSession, ClientTimeout, web

HERE = Path(__file__).resolve().parent
HUD_FILE = HERE.parent / "index.html"


def app_data_dir():
    """Per-user data folder: config overrides, conversation state, voices, logs.
    Windows %LOCALAPPDATA%\\ATLAS | macOS ~/Library/Application Support/ATLAS | Linux ~/.local/share/ATLAS"""
    if os.getenv("ATLAS_HOME"):
        return Path(os.environ["ATLAS_HOME"])
    if os.getenv("MODI_HOME"):  # legacy-modi: environment override from before the rename
        return Path(os.environ["MODI_HOME"])   # legacy-modi
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "ATLAS"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ATLAS"
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "ATLAS"


DATA_DIR = app_data_dir()
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    DATA_DIR = HERE
STATE_FILE = DATA_DIR / "state.json"
FIRST_RUN_FILE = DATA_DIR / "first-run"   # written by a fresh interactive install; the HUD then opens straight into AI sign-in -> "about you" and removes it


def first_run():
    return FIRST_RUN_FILE.exists()

DEFAULTS = {
    "host": "127.0.0.1", "port": 8765, "mode": "api",
    "hermes": {"api_url": "http://127.0.0.1:8642", "api_key": "", "read_key_from_hermes_env": True,
               "model": "hermes-agent", "session_id": "jarvis-hud", "timeout_seconds": 180,
               "cli_command": "hermes", "autostart": True,    # autostart: start Hermes' gateway together with the bridge when it is installed but not running
               "display_primary": "",       # "" = read the model name from Hermes' own config.yaml
               "display_fallback": "", "display_next_job": ""},   # shown in the Hermes status tile only when set
    "stt": {"model": "base", "device": "auto", "language": "en", "vad": True, "beam_size": 1},
    "tts": {"engine": "auto", "voice": "en_GB-alan-medium", "length_scale": 1.0, "max_chars": 700,
            "download_if_missing": True, "say_voice": "Daniel", "say_rate": 180},
    "vision": {"default_question": "What do you see? Answer briefly."},
    "update": {"enabled": True, "repo": "", "check_hours": 12},   # repo "" = atlas_meta.REPO
    "assistant": {"name": "ATLAS", "wake_phrase": "Hey ATLAS"},
    "profile": {"name": "", "city": "", "label": "", "lat": None, "lon": None, "timezone": "", "country": "", "units": "metric",
                "sports": []},   # [{"sport": "Soccer", "leagues": ["Serie A"], "teams": ["Arsenal"]}] - set in HUD Settings or by the installer
    "features": {},   # {"<id>": {"enabled": bool, "settings": {...}}}  - managed from the HUD Settings panel
}

ASSISTANT_DEFAULT = "ATLAS"


class BadInput(ValueError):
    """Invalid user input (shown to the user as-is)."""


def clean_assistant_name(v):
    """1-20 chars; letters/digits/space/. ' - only (so it is safe in HTML, prompts and file-free); empty = default."""
    v = re.sub(r"\s+", " ", str(v or "")).strip()
    if not v:
        return ASSISTANT_DEFAULT
    if len(v) > 20:
        raise BadInput("The assistant's name can be at most 20 characters.")
    if not re.fullmatch(r"\w[\w .'\-]*", v):
        raise BadInput("Use letters, numbers, spaces, and . ' - only in the assistant's name.")
    return v


def assistant_name():
    return ((CFG.get("assistant") or {}).get("name") or ASSISTANT_DEFAULT).strip() or ASSISTANT_DEFAULT


def system_prompt():
    name = (CFG.get("profile") or {}).get("name", "").strip()
    me = assistant_name()
    who = f"{name}'s personal assistant" if name else "the user's personal assistant"
    return (
        f"You are {me}, {who}, speaking out loud through a Jarvis-style HUD on their desk (you run on the Hermes agent). "
        f"If asked your name, it is {me}. "
        "Be warm, sharp and concise: normally one to three short spoken sentences. Plain text only - no markdown, "
        "no bullet lists, no emojis - unless the user explicitly asks for detail. Messages may come from voice "
        "transcription, so be forgiving of small mis-hearings. If a webcam image is attached, it is a live frame from their desk. "
        "Calendar: ATLAS reads the user's calendar itself and adds the relevant events to the message as [ATLAS calendar]; answer from those events only. "
        "Never use your own Google/Microsoft calendar connectors or skills for it, never ask the user to sign in or paste OAuth links for the calendar, "
        "and never search the user's files or folders for credentials, tokens or client secrets."
    )


def deep_merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def migrate_legacy_config():  # legacy-modi
    """The product used to be called MODI. A user who never renamed the assistant (name still the old default) becomes ATLAS / (legacy-modi)
    "Hey ATLAS"; a custom name is kept untouched. Runs once on the per-user config file, quietly, on every start (it is a no-op after)."""
    f = DATA_DIR / "config.json"
    try:
        cur = json.loads(f.read_text(encoding="utf-8-sig")) if f.exists() else None
    except Exception:
        return
    if not isinstance(cur, dict) or not isinstance(cur.get("assistant"), dict):
        return
    a = cur["assistant"]
    name, phrase = str(a.get("name") or "").strip(), str(a.get("wake_phrase") or "").strip()
    if name.lower() == "modi" or (not name and phrase.lower() == "hey modi"):   # legacy-modi
        try:
            atlas_cfgio.update_config(f, lambda c: c.update({"assistant": {"name": ASSISTANT_DEFAULT, "wake_phrase": "Hey " + ASSISTANT_DEFAULT}}))
            print("[bridge] migrated the default assistant name MODI -> ATLAS", flush=True)   # legacy-modi
        except OSError:
            pass


def load_config():
    migrate_legacy_config()
    cfg = copy.deepcopy(DEFAULTS)
    # app-folder config.json first, then the per-user override file (survives app updates)
    for p in (HERE / "config.json", DATA_DIR / "config.json"):
        if p.exists():
            try:
                deep_merge(cfg, json.loads(p.read_text(encoding="utf-8-sig")))
            except Exception as e:  # noqa
                print(f"[bridge] WARNING: could not read {p} ({e}); skipping", flush=True)
    if os.getenv("JARVIS_PORT"):
        cfg["port"] = int(os.environ["JARVIS_PORT"])
    if os.getenv("JARVIS_MODE") in ("api", "cli"):
        cfg["mode"] = os.environ["JARVIS_MODE"]
    if os.getenv("HERMES_API_URL"):
        cfg["hermes"]["api_url"] = os.environ["HERMES_API_URL"]
    if os.getenv("HERMES_API_KEY"):
        cfg["hermes"]["api_key"] = os.environ["HERMES_API_KEY"]
    return cfg


CFG = load_config()

import atlas_cfgio  # noqa: E402  (atomic, merge-safe config.json writes)
import atlas_browser  # noqa: E402  (links open in the user's default browser)
import atlas_calendar  # noqa: E402  (calendar panel: local .ics files and secret links)
import atlas_features  # noqa: E402  (feature framework, see atlas_features.py)
import atlas_hermes  # noqa: E402  (finds / installs / connects Hermes, see atlas_hermes.py)
import atlas_meta  # noqa: E402
import atlas_info  # noqa: E402
import atlas_modules  # noqa: E402  (add-on safety: approval, red-flag scan, AI second opinion)  (calendar / score questions and the daily brief)
import atlas_reminders  # noqa: E402  (spoken reminders and timers)
import atlas_scores  # noqa: E402  (live scores from ESPN's public scoreboard)
import atlas_settings  # noqa: E402  (settings registry + local voice/chat intent handler)
import atlas_update  # noqa: E402
VERSION = atlas_meta.VERSION


HM = atlas_hermes.HermesManager(CFG, DATA_DIR, say=lambda m: print(m, flush=True))


def hermes_home_candidates():
    return HM.home_candidates()


def resolve_api_key():
    """ATLAS config > the key Hermes itself is configured with (.env / config.yaml) > ATLAS's saved copy."""
    return HM.resolve_api_key()


# --------------------------------------------------------------------------- profile (name / city / teams)
def geocode(q, count=5):
    """Free, key-less city lookup (Open-Meteo geocoding). Returns [{label, city, lat, lon, timezone, country, units}]."""
    import urllib.parse
    import urllib.request
    q = (q or "").strip()
    if not q:
        return []
    name = q.split(",")[0].strip()
    url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode({"name": name, "count": 8, "language": "en", "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": "ATLAS/" + atlas_meta.VERSION})
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.loads(r.read().decode("utf-8"))
    hint = [x.strip().lower() for x in q.split(",")[1:] if x.strip()]
    out = []
    for g in data.get("results") or []:
        label = ", ".join(x for x in (g.get("name"), g.get("admin1"), g.get("country")) if x)
        out.append({"label": label, "city": g.get("name", name), "lat": round(g["latitude"], 4), "lon": round(g["longitude"], 4),
                    "timezone": g.get("timezone", ""), "country": g.get("country_code", ""),
                    "units": "imperial" if g.get("country_code") == "US" else "metric",
                    "_hit": sum(1 for h in hint if h in label.lower() or h == (g.get("country_code") or "").lower())})
    out.sort(key=lambda c: -c["_hit"])           # "Austin, Texas" ranks the Texas match first
    for c in out:
        c.pop("_hit")
    return out[:5]


SPORT_NAMES = {"soccer": "Soccer", "football": "Football", "nfl": "NFL", "nba": "NBA", "mlb": "MLB", "nhl": "NHL", "f1": "F1",
               "formula 1": "F1", "formula one": "F1", "college football": "College football", "ncaa football": "College football"}


def _clean_list(t, limit, maxlen=40):
    if isinstance(t, str):
        t = t.split(",")
    out = []
    for x in t or []:
        x = str(x).strip()[:maxlen]
        if x and x.lower() not in [y.lower() for y in out]:
            out.append(x)
    return out[:limit]


def clean_sports(sports):
    """Validate/normalise profile.sports -> [{sport, leagues[], teams[]}] (max 8 sports, 6 leagues, 10 teams each)."""
    out = []
    for e in sports or []:
        if isinstance(e, str):
            e = {"sport": e}
        if not isinstance(e, dict):
            continue
        name = str(e.get("sport", "")).strip()[:30]
        if not name:
            continue
        name = SPORT_NAMES.get(name.lower(), name)
        if name.lower() in [x["sport"].lower() for x in out]:
            continue
        out.append({"sport": name, "leagues": _clean_list(e.get("leagues"), 6), "teams": _clean_list(e.get("teams"), 10)})
    return out[:8]


def save_user_config(patch):
    """Merge `patch` into the per-user config.json: atomic, one writer at a time, never starts from {} on a read error."""
    atlas_cfgio.update_config(DATA_DIR / "config.json", lambda cur: deep_merge(cur, copy.deepcopy(patch)))


def update_profile(body, resolve=True):
    """Apply {name, city, teams, pick:{...candidate}}; returns (profile, candidates, message)."""
    prof = CFG.setdefault("profile", {})
    patch, cands, msg = {}, [], ""
    new_assistant = None
    if "assistant" in body or "assistant_name" in body:       # validate first so a bad name saves nothing
        a = body.get("assistant")
        raw = a.get("name") if isinstance(a, dict) else body.get("assistant_name")
        new_assistant = clean_assistant_name(raw)
    if "name" in body:
        patch["name"] = str(body["name"]).strip()[:40]
    if "sports" in body:
        patch["sports"] = clean_sports(body["sports"])
    pick = body.get("pick")
    if isinstance(pick, dict) and pick.get("lat") is not None:
        patch.update({k: pick[k] for k in ("label", "city", "lat", "lon", "timezone", "country", "units") if k in pick})
    elif "city" in body:
        city = str(body["city"]).strip()[:80]
        if not city:
            patch.update({"city": "", "label": "", "lat": None, "lon": None, "timezone": "", "country": ""})
        elif city != prof.get("city") and city != prof.get("label"):
            if resolve:
                try:
                    cands = geocode(city)
                except Exception as e:  # noqa
                    raise RuntimeError("Couldn't look up the city (are you online?).") from e
                if not cands:
                    raise ValueError("Couldn't find that city. Try the city name only, or 'City, Country'.")
                patch.update(cands[0])
                msg = "Using " + cands[0]["label"]
            else:
                patch["city"] = city
    deep_merge(prof, patch)
    prof["sports"] = clean_sports(prof.get("sports"))
    prof.pop("teams", None)
    save_user_config({"profile": {k: prof.get(k) for k in patch}})
    if new_assistant is not None:
        CFG["assistant"] = {"name": new_assistant, "wake_phrase": "Hey " + new_assistant}
        save_user_config({"assistant": CFG["assistant"]})
        msg = (msg + " · " if msg else "") + "Assistant is now " + new_assistant
    return prof, cands, msg


def hermes_model_label():
    """Model name for the status tile: config override, else Hermes' own config.yaml (model.default / provider)."""
    d = CFG["hermes"].get("display_primary") or ""
    if d:
        return d
    for home in hermes_home_candidates():
        try:
            txt = (home / "config.yaml").read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        m = re.search(r"(?m)^model:\s*\n((?:[ \t]+.*\n?|\s*\n)+)", txt)
        if m:
            blk = m.group(1)
            name = re.search(r"(?m)^[ \t]+(?:default|model|name):\s*[\"']?([^\"'#\n]+?)[\"']?\s*(?:#.*)?$", blk)
            prov = re.search(r"(?m)^[ \t]+provider:\s*[\"']?([^\"'#\n]+?)[\"']?\s*(?:#.*)?$", blk)
            if name:
                return name.group(1) + (f" ({prov.group(1)})" if prov and prov.group(1) != "auto" else "")
    return "Hermes Agent"

# --------------------------------------------------------------------------- state / history
STATE = {"session_id": CFG["hermes"]["session_id"], "history": []}
_state_lock = threading.Lock()


def load_state():
    try:
        s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(s, dict):
            STATE["session_id"] = s.get("session_id") or STATE["session_id"]
            STATE["history"] = s.get("history", [])[-100:]
    except Exception:
        pass


def save_state():
    with _state_lock:
        try:
            STATE_FILE.write_text(json.dumps({"session_id": STATE["session_id"], "history": STATE["history"][-100:]}),
                                  encoding="utf-8")
        except OSError:
            pass


def add_history(role, text, kind="chat"):
    STATE["history"].append({"role": role, "text": text, "kind": kind, "ts": int(time.time())})
    STATE["history"] = STATE["history"][-100:]
    save_state()


# --------------------------------------------------------------------------- Hermes client
class HermesError(Exception):
    def __init__(self, msg, status=502, fix=""):
        super().__init__(msg)
        self.status = status
        self.fix = fix          # "setup" = the HUD should offer its Connect-Hermes / sign-in wizard


_hermes_lock = asyncio.Lock()
_health_cache = {"t": 0, "v": None}


def _content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def _unreachable_msg(tried):
    """What to say when Hermes (the AI engine ATLAS talks to) is not answering - plain words, and what to do."""
    if not HM.find_cli():
        return "Hermes, the AI engine I use, isn't installed on this computer. Press Fix it and I'll install it."
    if not (HM.resolve_api_key() and HM.api_enabled()):
        return "Hermes isn't connected to me yet. Press Fix it and I'll connect it."
    if tried or os.getenv("ATLAS_NO_BOOT") or not CFG["hermes"].get("autostart", True):
        return ("Hermes, the AI engine I use, isn't running and didn't start. Press Fix it to try again. "
                "If it still fails, the details are in %s." % (HM.home() / "logs"))
    return "Hermes, the AI engine I use, isn't running. I'm starting it - try again in a few seconds, or press Fix it."


async def hermes_api(user_content, session_id=None, system=None):
    """user_content: str or OpenAI content-part list. Returns reply text.  session_id/system: a separate one-off conversation (used for add-on reviews)."""
    h = CFG["hermes"]
    key = resolve_api_key()
    if not key:
        raise HermesError("Hermes isn't connected yet. Press Fix it and I'll set it up.", 503, "setup")
    url = HM.api_url() + "/v1/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "X-Hermes-Session-Id": session_id or STATE["session_id"]}
    sysmsg = system or system_prompt()
    res = None
    revived = False
    attempt = 0
    while attempt < 2:
        attempt += 1
        body = {"model": h["model"], "stream": False,
                "messages": [{"role": "system", "content": sysmsg}, {"role": "user", "content": user_content}]}
        try:
            async with ClientSession(timeout=ClientTimeout(total=h["timeout_seconds"])) as s:
                async with s.post(url, json=body, headers=headers) as r:
                    txt = await r.text()
                    code = r.status
        except asyncio.TimeoutError:
            raise HermesError("Hermes took too long to answer (timeout).", 504)
        except ClientError:
            HM.invalidate()
            if not revived:
                revived = True
                job = asyncio.ensure_future(revive_hermes("chat", min_gap=10))   # keeps going in the background if it takes longer
                try:
                    ok = await asyncio.wait_for(asyncio.shield(job), 25)
                except asyncio.TimeoutError:
                    raise HermesError("Hermes, the AI engine I use, had stopped. I'm starting it again - ask me once more in a few seconds.", 503, "setup")
                if ok:                         # Hermes had stopped (e.g. its window was closed); it is running again - ask once more
                    attempt -= 1
                    continue
            raise HermesError(_unreachable_msg(revived), 503, "setup")
        res = atlas_hermes.interpret_chat(code, txt, key, limit=None)
        if res.get("kind") != "tool_echo":
            break
        if attempt == 1:     # a weak local model answered with tool-call text: ask once more, plainly
            print("[bridge] the AI answered with tool-call text; retrying once", flush=True)
            sysmsg += "\nIMPORTANT: answer the user directly in plain spoken sentences. Do not call tools and do not write function calls, JSON or 'Tool:' lines."
    if res["ok"]:
        HM.note_chat_ok()
        return res["reply"] or "(Hermes returned an empty reply.)"
    kind = res.get("kind")
    if kind == "tool_echo":
        _lab, _mdl = HM.provider_label()
        raise HermesError(res["error"].replace("the local model", _mdl) if _mdl else res["error"], 502, "model")
    if kind == "no_provider":
        HM.chat_state = "no_provider"
        raise HermesError("Hermes isn't signed in to an AI yet. Choose ChatGPT, Claude, Grok or a local model.", 503, "setup")
    if kind in ("auth", "no_key"):
        HM.invalidate()
        raise HermesError("Hermes and I lost our secure link. Press Fix it to reconnect.", 502, "setup")
    if kind == "unreachable":
        raise HermesError(_unreachable_msg(revived), 503, "setup")
    if kind == "provider_auth":
        HM.chat_state = "no_provider"
        raise HermesError(res["error"], 502, "setup")
    raise HermesError(res["error"] if res.get("error") else f"Hermes API error {code}", 502)


async def hermes_cli(prompt_text, image_path=None, plain=False):
    """Fallback: `hermes -z "<prompt>"`. Continuity is approximated by replaying recent turns in the prompt."""
    h = CFG["hermes"]
    exe = HM.find_cli() or h["cli_command"]
    turns = [] if plain else [t for t in STATE["history"] if t.get("kind") in ("chat", "look")][-6:]
    uname = (CFG.get('profile') or {}).get('name') or 'User'
    ctx = "".join(f"{uname if t['role']=='user' else 'Hermes'}: {t['text']}\n" for t in turns)
    prompt = ("" if plain else system_prompt() + "\n\n")
    if ctx:
        prompt += "Recent conversation:\n" + ctx + "\n"
    if image_path:
        prompt += (f"A webcam frame from the user's desk was saved at: {image_path}\n"
                   "Use your vision_analyze tool on that file to answer.\n")
    prompt += prompt_text if plain else uname + ": " + prompt_text
    try:
        proc = await asyncio.create_subprocess_exec(exe, "-z", prompt, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), timeout=h["timeout_seconds"])
    except FileNotFoundError:
        raise HermesError(f"Could not run '{h['cli_command']}'. Is Hermes on PATH?", 503)
    except asyncio.TimeoutError:
        raise HermesError("Hermes (CLI) took too long to answer.", 504)
    if proc.returncode != 0:
        raise HermesError("Hermes CLI failed: " + (err.decode(errors="ignore") or out.decode(errors="ignore"))[:300], 502)
    return out.decode("utf-8", errors="ignore").strip() or "(Hermes returned an empty reply.)"


async def hermes_text(text):
    async with _hermes_lock:
        if CFG["mode"] == "cli":
            return await hermes_cli(text)
        return await hermes_api(text)


async def hermes_review(text):
    """A one-off question to the user's own AI that is NOT part of their chat history (add-on reviews, module writing)."""
    async with _hermes_lock:
        if CFG["mode"] == "cli":
            return await hermes_cli(text, plain=True)
        return await hermes_api(text, session_id="atlas-review-" + str(int(time.time())),
                                system="You are a careful, plain-spoken reviewer. Answer exactly in the format you are asked for. Do not use tools.")


async def hermes_look(jpeg_bytes, question):
    async with _hermes_lock:
        if CFG["mode"] == "cli":
            d = Path(tempfile.gettempdir()) / "jarvis-hud"
            d.mkdir(exist_ok=True)
            for old in d.glob("look-*.jpg"):
                try:
                    if time.time() - old.stat().st_mtime > 3600:
                        old.unlink()
                except OSError:
                    pass
            p = d / f"look-{int(time.time()*1000)}.jpg"
            p.write_bytes(jpeg_bytes)
            return await hermes_cli(question, image_path=str(p))
        data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode()
        content = [{"type": "text", "text": question},
                   {"type": "image_url", "image_url": {"url": data_url}}]
        return await hermes_api(content)


async def hermes_health():
    """Cheap reachability probe (cached 4s)."""
    now = time.time()
    if _health_cache["v"] is not None and now - _health_cache["t"] < 4:
        return _health_cache["v"]
    if CFG["mode"] == "cli":
        ok = HM.find_cli() is not None
        v = {"mode": "cli", "reachable": ok, "detail": "hermes CLI found" if ok else "hermes CLI not on PATH"}
    else:
        url = HM.api_url() + "/health"
        try:
            async with ClientSession(timeout=ClientTimeout(total=2)) as s:
                async with s.get(url) as r:
                    v = {"mode": "api", "reachable": r.status < 500, "detail": f"HTTP {r.status}"}
        except Exception as e:  # noqa
            v = {"mode": "api", "reachable": False, "detail": f"{type(e).__name__}"}
        v["has_key"] = bool(resolve_api_key())
    _health_cache.update(t=now, v=v)
    return v


# --------------------------------------------------------------------------- speech to text
class STT:
    def __init__(self):
        self.model = None
        self.device = None
        self.note = ""
        self.loading = False
        self.error = ""
        self.lock = threading.Lock()

    @staticmethod
    def _add_nvidia_dirs():
        try:
            import importlib.util
            for pkg in ("nvidia.cublas", "nvidia.cudnn"):
                spec = importlib.util.find_spec(pkg)
                if spec and spec.submodule_search_locations:
                    for loc in spec.submodule_search_locations:
                        b = Path(loc) / "bin"
                        if b.is_dir() and hasattr(os, "add_dll_directory"):
                            os.add_dll_directory(str(b))
                            os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")
        except Exception:
            pass

    @staticmethod
    def _cuda_ok():
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() < 1:
                return False
        except Exception:
            return False
        try:
            import ctypes
            if os.name == "nt":
                ctypes.WinDLL("cublas64_12.dll")
            else:
                ctypes.CDLL("libcublas.so.12")
            return True
        except Exception:
            return False

    def load(self):
        with self.lock:
            if self.model is not None:
                return
            self.loading = True
            try:
                from faster_whisper import WhisperModel
                import numpy as np
                c = CFG["stt"]
                want = c.get("device", "auto")
                self._add_nvidia_dirs()
                if want in ("auto", "cuda") and (want == "cuda" or self._cuda_ok()):
                    try:
                        m = WhisperModel(c["model"], device="cuda", compute_type="float16")
                        noise = (np.random.randn(16000) * 0.01).astype("float32")
                        list(m.transcribe(noise, vad_filter=False, beam_size=1)[0])
                        self.model, self.device = m, "cuda"
                        return
                    except Exception as e:  # noqa
                        self.note = f"CUDA unavailable ({str(e)[:120]}); using CPU"
                        print("[bridge] STT:", self.note, flush=True)
                elif want == "auto":
                    self.note = "CUDA libraries not found; using CPU (fine for the 'base' model)"
                self.model = WhisperModel(c["model"], device="cpu", compute_type="int8")
                self.device = "cpu"
            except Exception as e:  # noqa
                self.error = f"{type(e).__name__}: {e}"
                print("[bridge] STT load failed:", self.error, flush=True)
                raise
            finally:
                self.loading = False

    def transcribe(self, path):
        self.load()
        c = CFG["stt"]
        t0 = time.time()
        segs, info = self.model.transcribe(
            path, language=(c.get("language") or None), vad_filter=bool(c.get("vad", True)),
            beam_size=int(c.get("beam_size", 1)), condition_on_previous_text=False)
        text = " ".join(s.text.strip() for s in segs).strip()
        return {"transcript": text, "language": info.language, "duration": round(info.duration, 2),
                "ms": int((time.time() - t0) * 1000), "device": self.device}


stt = STT()


# --------------------------------------------------------------------------- text to speech
class TTS:
    """Piper (neural, offline) with automatic fallback to macOS `say` if Piper can't load."""

    def __init__(self):
        self.voice = None
        self.path = None
        self.engine = None     # "piper" | "say"
        self.status = "idle"   # idle | downloading | ready | error
        self.error = ""
        self.lock = threading.Lock()

    def search_dirs(self):
        dirs = [DATA_DIR / "voices", HERE / "voices"]
        for home in hermes_home_candidates():
            dirs.append(home / "cache" / "piper-voices")
        return dirs

    def find(self, name):
        for d in self.search_dirs():
            p = d / f"{name}.onnx"
            if p.exists() and (d / f"{name}.onnx.json").exists():
                return p
        return None

    def _load_piper(self):
        name = CFG["tts"]["voice"]
        p = self.find(name)
        if p is None:
            if not CFG["tts"].get("download_if_missing", True):
                raise RuntimeError(f"voice {name} not found")
            self.status = "downloading"
            d = DATA_DIR / "voices"
            d.mkdir(parents=True, exist_ok=True)
            r = subprocess.run([sys.executable, "-m", "piper.download_voices", name, "--download-dir", str(d)],
                               capture_output=True, text=True, timeout=900)
            p = self.find(name)
            if p is None:
                raise RuntimeError("voice download failed: " + (r.stderr or r.stdout)[-200:])
        from piper import PiperVoice
        self.voice = PiperVoice.load(str(p))
        self.path = p
        self.engine = "piper"
        self.status = "ready"

    def ensure(self):
        with self.lock:
            if self.engine is not None:
                return
            want = CFG["tts"].get("engine", "auto")
            if want in ("auto", "piper"):
                try:
                    self._load_piper()
                    return
                except Exception as e:  # noqa
                    self.error = f"Piper unavailable: {type(e).__name__}: {str(e)[:160]}"
                    print("[bridge] TTS:", self.error, flush=True)
                    if want == "piper" or not shutil.which("say"):
                        self.status = "error"
                        raise
            if shutil.which("say"):
                self.engine, self.status = "say", "ready"
                print("[bridge] TTS: using macOS 'say'", flush=True)
                return
            self.status = "error"
            raise RuntimeError("No text-to-speech engine available (Piper failed and `say` not found)")

    def _say(self, text):
        t = CFG["tts"]
        fd, out = tempfile.mkstemp(suffix=".wav", prefix="atlas-say-")
        os.close(fd)
        try:
            cmd = ["say", "-v", t.get("say_voice", "Daniel"), "-r", str(t.get("say_rate", 180)), "-o", out,
                   "--file-format=WAVE", "--data-format=LEI16@22050", "--", text]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if r.returncode != 0:      # voice name may not be installed -> retry with the system voice
                cmd = ["say", "-r", str(t.get("say_rate", 180)), "-o", out, "--file-format=WAVE",
                       "--data-format=LEI16@22050", "--", text]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if r.returncode != 0:
                    raise RuntimeError("say failed: " + (r.stderr or "")[:200])
            return Path(out).read_bytes()
        finally:
            try:
                os.unlink(out)
            except OSError:
                pass

    def synth(self, text):
        self.ensure()
        if self.engine == "say":
            return self._say(text)
        from piper import SynthesisConfig
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            self.voice.synthesize_wav(text, w, syn_config=SynthesisConfig(length_scale=float(CFG["tts"].get("length_scale", 1.0))))
        return buf.getvalue()


tts = TTS()


def clean_for_speech(text):
    t = re.sub(r"```.*?```", " (code omitted) ", text, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", " link ", t)
    t = re.sub(r"[*_#>~]+", "", t)
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.M)
    t = re.sub(r"\s+", " ", t).strip()
    mx = int(CFG["tts"].get("max_chars", 700))
    if len(t) > mx:
        cut = t[:mx]
        k = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        t = cut[:k + 1] if k > 200 else cut
    return t


# --------------------------------------------------------------------------- system stats
psutil.cpu_percent(interval=None)
_gpu = {"t": 0, "v": None}


_chip = {"v": None}


def mac_chip_name():
    if _chip["v"] is None:
        try:
            _chip["v"] = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True,
                                        text=True, timeout=3).stdout.strip() or "Mac"
        except Exception:
            _chip["v"] = "Mac"
    return _chip["v"]


def gpu_stats():
    now = time.time()
    if now - _gpu["t"] < 1.5:
        return _gpu["v"]
    v = None
    exe = shutil.which("nvidia-smi")
    if exe:
        try:
            out = subprocess.run(
                [exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                 "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            line = out.stdout.strip().splitlines()[0]
            n, u, mu, mt, tc = [x.strip() for x in line.split(",")]
            v = {"name": n, "util": float(u), "mem_used_mb": float(mu), "mem_total_mb": float(mt),
                 "temp_c": float(tc) if tc.replace(".", "").isdigit() else None}
        except Exception:
            v = None
    if v is None and sys.platform == "darwin":
        # No public, permission-free GPU load API on macOS: report the chip but no utilisation.
        v = {"name": mac_chip_name() + " GPU", "util": None, "mem_used_mb": None, "mem_total_mb": None,
             "temp_c": None, "integrated": True}
    _gpu.update(t=now, v=v)
    return v


def system_stats():
    vm = psutil.virtual_memory()
    root = os.path.splitdrive(sys.executable)[0] + "\\" if os.name == "nt" else "/"
    try:
        du = psutil.disk_usage(root)
        disk = {"path": root, "percent": du.percent, "used_gb": round(du.used / 1e9, 1), "total_gb": round(du.total / 1e9, 1)}
    except Exception:
        disk = None
    return {
        "cpu": {"percent": psutil.cpu_percent(interval=None), "cores": psutil.cpu_count(logical=False),
                "threads": psutil.cpu_count(logical=True)},
        "ram": {"percent": vm.percent, "used_gb": round(vm.used / 2**30, 1), "total_gb": round(vm.total / 2**30, 1)},
        "disk": disk, "gpu": gpu_stats(),
        "uptime_s": int(time.time() - psutil.boot_time()),
        "host": platform.node(),
        "os": (f"macOS {platform.mac_ver()[0]}" if sys.platform == "darwin" else f"{platform.system()} {platform.release()}"),
    }


# --------------------------------------------------------------------------- HTTP handlers
def jerr(msg, status=500):
    return web.json_response({"ok": False, "error": msg}, status=status)


async def h_index(request):
    if not HUD_FILE.exists():
        return web.Response(text="index.html not found next to the bridge folder.", status=404)
    return web.Response(body=HUD_FILE.read_bytes(), content_type="text/html", charset="utf-8",
                        headers={"Cache-Control": "no-store"})


def _asset(name, ctype):
    f = HERE.parent / "assets" / name
    if not f.exists():
        return web.Response(status=204)
    return web.Response(body=f.read_bytes(), content_type=ctype, headers={"Cache-Control": "public, max-age=86400"})


async def h_icon(request):
    return _asset("atlas.ico", "image/x-icon")


async def h_icon_png(request):
    return _asset("atlas.png", "image/png")


async def h_icon_size(request):
    return _asset("atlas-%s.png" % request.match_info["size"], "image/png")


MANIFEST = {"name": "ATLAS", "short_name": "ATLAS", "id": "/", "start_url": "/", "scope": "/", "display": "standalone",
            "background_color": "#02080f", "theme_color": "#02080f",
            "icons": [{"src": "/atlas-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                      {"src": "/atlas-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                      {"src": "/atlas.png", "sizes": "256x256", "type": "image/png"},
                      {"src": "/atlas.ico", "sizes": "16x16 24x24 32x32 48x48 64x64 128x128 256x256", "type": "image/x-icon"}]}


async def h_manifest(request):
    """Web-app manifest: gives the Edge/Chrome app window (and so the Windows taskbar button) the ATLAS name and icon."""
    return web.json_response(MANIFEST, content_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


async def h_quit(request):
    """Stop the bridge (used by the HUD power button and Stop-ATLAS)."""
    print("[bridge] quit requested", flush=True)

    def _exit():
        raise web.GracefulExit()
    asyncio.get_running_loop().call_later(0.4, _exit)
    return web.json_response({"ok": True})


_CLIENT_ERR = {"n": 0, "t0": 0.0}


async def h_client_error(request):
    """The HUD reports its own script errors here (v1.4.3) so they end up in bridge.log. Rate-limited, nothing is sent anywhere else."""
    now = time.time()
    if now - _CLIENT_ERR["t0"] > 600:
        _CLIENT_ERR.update(n=0, t0=now)
    _CLIENT_ERR["n"] += 1
    if _CLIENT_ERR["n"] > 60:
        return web.json_response({"ok": True, "logged": False})
    try:
        b = await request.json()
    except Exception:  # noqa
        b = {}
    if not isinstance(b, dict):
        b = {}
    clip = lambda k, n: re.sub(r"[\x00-\x1f]+", " ", str(b.get(k) or ""))[:n]   # noqa: E731
    stack = " | ".join(x.strip() for x in str(b.get("stack") or "").splitlines()[:4])[:600]
    print("[hud] script error (%s, %s): %s  at %s:%s:%s  window %sx%s  %s%s" % (clip("phase", 20), clip("kind", 30), clip("message", 400), clip("source", 160).rsplit("/", 1)[-1] or "page",
          clip("line", 8), clip("col", 8), clip("w", 6), clip("h", 6), clip("ua", 120), ("\n[hud]   stack: " + stack) if stack else ""), flush=True)
    return web.json_response({"ok": True, "logged": True})


def reset_layout():
    """Every panel back in its default slot (also used by the HUD's Reset button and 'reset ATLAS')."""
    prefs = CFG.setdefault("prefs", {})
    prefs.pop("layout", None)
    prefs.pop("panels_hidden", None)

    def change(cur):
        p = cur.get("prefs")
        if isinstance(p, dict):
            p.pop("layout", None)
            p.pop("panels_hidden", None)
    atlas_cfgio.update_config(DATA_DIR / "config.json", change)


async def h_layout_reset(request):
    await asyncio.get_running_loop().run_in_executor(None, reset_layout)
    print("[bridge] panel layout reset to the default", flush=True)
    return web.json_response({"ok": True, "prefs": SETTINGS.prefs_view()})


def _respawn_bridge():
    """Start a fresh bridge (same Python, same script) that waits for this one to let go of the port, then exit this one."""
    logs = DATA_DIR / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    out = open(logs / "bridge.log", "a", encoding="utf-8")
    err = open(logs / "bridge.err.log", "a", encoding="utf-8")
    flags = atlas_hermes._flags(detached=True, breakaway=True)
    cmd = [sys.executable, str(Path(__file__).resolve()), "--after-restart"]
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=err, cwd=str(HERE), **flags)
    except OSError:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=err, cwd=str(HERE), **atlas_hermes._flags(detached=True))


async def h_restart(request):
    """Restart ATLAS services: the Hermes gateway, then this bridge (a new copy takes over the port). The HUD reloads when /api/health answers."""
    async def run():
        loop = asyncio.get_running_loop()
        print("[bridge] restart requested from the HUD - restarting Hermes and the bridge", flush=True)
        if os.environ.get("ATLAS_NO_BOOT") != "1" and HM.find_cli():
            try:
                await asyncio.wait_for(loop.run_in_executor(None, lambda: HM.start_gateway(restart=True)), 75)
            except Exception as e:  # noqa
                print(f"[bridge] Hermes restart: {type(e).__name__}", flush=True)
        await loop.run_in_executor(None, _respawn_bridge)
        await asyncio.sleep(0.3)

        def _exit():
            raise web.GracefulExit()
        loop.call_soon(_exit)
    asyncio.ensure_future(run())
    return web.json_response({"ok": True})


async def h_open_url(request):
    """Open an http(s) link from the HUD in the user's DEFAULT browser (the HUD window itself is an Edge/Chrome app window)."""
    try:
        body = await request.json()
    except Exception:  # noqa
        return web.json_response({"ok": False, "error": "bad request"}, status=400)
    url = str((body or {}).get("url") or "")
    ok, msg = await asyncio.get_running_loop().run_in_executor(None, atlas_browser.open_default, url)
    return web.json_response({"ok": ok, "error": "" if ok else msg}, status=200 if ok else 400)


async def h_health(request):
    hv = await hermes_health()
    h = CFG["hermes"]
    st = await asyncio.get_running_loop().run_in_executor(None, HM.status)
    return web.json_response({
        "setup": {"state": st["state"], "provider": st["provider"], "model": st["model"], "busy": st["busy"], "first_run": first_run()},
        "ok": True, "bridge": True, "version": VERSION, "session_id": STATE["session_id"], "mode": CFG["mode"],
        "hermes": hv,
        "display": {"primary": hermes_model_label(), "fallback": h["display_fallback"], "next_job": h["display_next_job"]},
        "stt": {"model": CFG["stt"]["model"], "device": stt.device, "loaded": stt.model is not None,
                "loading": stt.loading, "note": stt.note, "error": stt.error},
        "tts": {"voice": CFG["tts"]["voice"], "engine": tts.engine, "status": tts.status, "error": tts.error},
        "gpu": gpu_stats() is not None,
        "profile": CFG.get("profile") or {},
        "addons": MODULES.summary(),
        "assistant": {"name": assistant_name(), "wake_phrase": (CFG.get("assistant") or {}).get("wake_phrase") or "Hey " + assistant_name()},
    })


# ---- Hermes connection wizard (see atlas_hermes.py) -----------------------------------------------------------
async def _body(request):
    try:
        b = await request.json()
        return b if isinstance(b, dict) else {}
    except Exception:  # noqa
        return {}


async def _in_thread(fn, *a):
    return await asyncio.get_running_loop().run_in_executor(None, lambda: fn(*a))


def _job_reply(res):
    job, started = res
    return web.json_response({"ok": True, "job": job, "started": started})


async def h_setup_status(request):
    st = await _in_thread(HM.status, request.query.get("fresh") == "1")
    return web.json_response({"ok": True, **st, "providers": [{"id": k, "label": v["label"], "kind": v["kind"]} for k, v in atlas_hermes.PROVIDERS.items()]})


async def h_setup_first_run_done(request):
    try:
        FIRST_RUN_FILE.unlink()
    except OSError:
        pass
    return web.json_response({"ok": True})


async def h_setup_install(request):
    return _job_reply(await _in_thread(HM.start_install))


async def h_setup_fix(request):
    return _job_reply(await _in_thread(HM.start_fix))


async def h_setup_provider(request):
    body = await _body(request)
    try:
        return _job_reply(await _in_thread(HM.start_provider, body))
    except atlas_hermes.SetupError as e:
        return jerr(str(e), 400)


async def h_setup_pull(request):
    body = await _body(request)
    try:
        return _job_reply(await _in_thread(HM.start_pull, str(body.get("model") or "")))
    except atlas_hermes.SetupError as e:
        return jerr(str(e), 400)


async def h_setup_job(request):
    j = HM.job(request.match_info["jid"])
    return web.json_response({"ok": True, "job": j}) if j else jerr("No such job.", 404)


async def h_setup_job_input(request):
    body = await _body(request)
    ok = await _in_thread(HM.send_input, request.match_info["jid"], str(body.get("text") or "")[:600])
    return web.json_response({"ok": bool(ok)})


async def h_setup_job_cancel(request):
    return web.json_response({"ok": await _in_thread(HM.cancel, request.match_info["jid"])})


async def h_setup_test(request):
    res = await _in_thread(HM.test_chat)
    if not res["ok"] and res.get("kind") in ("no_provider", "provider_auth"):
        HM.chat_state = "no_provider"
    elif res["ok"]:
        HM.note_chat_ok()
    lab, model = HM.provider_label()
    return web.json_response({"ok": True, "result": res, "provider": lab, "model": model})


def _hermes_should_run():
    """Hermes is installed, connected (key + API server on) and ATLAS is allowed to start it - so if it is not answering, starting it is the right fix."""
    return bool(CFG["hermes"].get("autostart", True) and CFG["mode"] == "api" and HM.find_cli() and HM.resolve_api_key() and HM.api_enabled())


_revive = {"last": 0.0, "fails": 0, "lock": None}


async def revive_hermes(reason, min_gap=45):
    """Hermes stopped answering (its window was closed, it crashed, the PC woke from sleep): start it again in the background.
    Throttled; returns True when Hermes answers again."""
    if os.getenv("ATLAS_NO_BOOT") or not await _in_thread(_hermes_should_run):
        return False
    if _revive["lock"] is None:
        _revive["lock"] = asyncio.Lock()
    async with _revive["lock"]:
        if await _in_thread(HM.healthy):
            return True
        if time.time() - _revive["last"] < min_gap:
            return False
        _revive["last"] = time.time()
        print("[bridge] Hermes is not answering (%s) - starting it" % reason, flush=True)
        ok = await _in_thread(HM.start_gateway)
        _revive["fails"] = 0 if ok else _revive["fails"] + 1
        HM.invalidate()
        _health_cache["t"] = 0
        print("[bridge] Hermes " + ("is up again" if ok else "did not start"), flush=True)
        return ok


async def hermes_watchdog():
    """Keep Hermes running while ATLAS runs: check every 30 s, restart it with a growing pause (45 s .. 5 min) if it stopped."""
    await asyncio.sleep(20)
    while True:
        try:
            if not await _in_thread(HM.healthy):
                gap = min(300, 45 * (2 ** min(_revive["fails"], 3)))
                await revive_hermes("watchdog", min_gap=gap)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa
            print("[bridge] Hermes watchdog:", type(e).__name__, e, flush=True)
        await asyncio.sleep(30)


def _hermes_boot():
    """At bridge start: remember Hermes' key and, if Hermes is installed but idle, start it (so it comes up with ATLAS)."""
    try:
        if os.getenv("ATLAS_NO_BOOT"):          # tests: don't auto-start Hermes
            return
        src = HM.adopt_key()
        if src:
            print(f"[bridge] adopted the Hermes API key from {src}", flush=True)
        if CFG["hermes"].get("autostart", True) and CFG["mode"] == "api" and HM.find_cli() and HM.resolve_api_key() \
                and HM.api_enabled() and not HM.healthy():
            print("[bridge] Hermes is installed but not running - starting it", flush=True)
            ok = HM.start_gateway()
            print("[bridge] Hermes " + ("is up" if ok else "did not start (the HUD will offer Fix it)"), flush=True)
    except Exception as e:  # noqa
        print(f"[bridge] Hermes auto-start skipped: {type(e).__name__}: {e}", flush=True)


async def h_system(request):
    loop = asyncio.get_running_loop()
    return web.json_response(await loop.run_in_executor(None, system_stats))


async def h_history(request):
    return web.json_response({"session_id": STATE["session_id"], "history": STATE["history"]})


async def h_new(request):
    STATE["session_id"] = f"{CFG['hermes']['session_id']}-{time.strftime('%Y%m%d-%H%M%S')}"
    STATE["history"] = []
    save_state()
    return web.json_response({"ok": True, "session_id": STATE["session_id"]})


async def h_chat(request):
    try:
        body = await request.json()
    except Exception:
        return jerr("Send JSON like {\"text\": \"...\"}", 400)
    text = (body.get("text") or "").strip()
    if not text:
        return jerr("Empty message.", 400)
    t0 = time.time()
    add_history("user", text, body.get("kind", "chat"))
    try:                                   # settings by voice/chat ("turn off wake word", "set my city to Dallas") are handled here, never sent to Hermes
        local = await local_intent(text)
    except Exception as e:  # noqa
        print("[bridge] local intent failed:", type(e).__name__, e, flush=True)
        local = None
    if local:
        add_history("assistant", local["reply"])
        return web.json_response({"ok": True, "reply": local["reply"], "ms": int((time.time() - t0) * 1000), "session_id": STATE["session_id"],
                                  "local": True, "changed": local["changed"], "ui": local["ui"], "pending": local["pending"], "brief": local.get("brief"),
                                  "prefs": SETTINGS.prefs_view(), "profile": CFG.get("profile") or {}, "assistant": CFG.get("assistant")})
    ask = text
    if atlas_info.is_calendar_topic(text):     # calendar questions ATLAS didn't answer itself: Hermes gets ATLAS's events and the rule not to go looking itself
        try:
            ctx = await asyncio.get_running_loop().run_in_executor(None, INFO.calendar_context, text)
            ask = text + "\n\n" + ctx
        except Exception as e:  # noqa
            print("[bridge] calendar context failed:", type(e).__name__, flush=True)
    try:
        reply = await hermes_text(ask)
    except HermesError as e:
        return web.json_response({"ok": False, "error": str(e), "fix": e.fix}, status=e.status)
    add_history("assistant", reply)
    return web.json_response({"ok": True, "reply": reply, "ms": int((time.time() - t0) * 1000),
                              "session_id": STATE["session_id"]})


async def h_listen(request):
    data = await request.read()
    if len(data) < 800:
        return jerr("No audio received.", 400)
    ct = (request.content_type or "").lower()
    ext = ".webm" if "webm" in ct else ".ogg" if "ogg" in ct else ".wav" if ("wav" in ct or "wave" in ct) else ".mp4" if ("mp4" in ct or "aac" in ct) else ".bin"
    fd, path = tempfile.mkstemp(suffix=ext, prefix="jarvis-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, stt.transcribe, path)
    except Exception as e:  # noqa
        return jerr(f"Speech-to-text failed: {type(e).__name__}: {e}", 500)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    res["ok"] = True
    return web.json_response(res)


async def h_look(request):
    try:
        body = await request.json()
        img = body.get("image", "")
        if "," in img:
            img = img.split(",", 1)[1]
        jpeg = base64.b64decode(img)
    except Exception:
        return jerr("Send JSON {\"image\": <base64 or data URL JPEG>, \"question\": \"...\"}", 400)
    if len(jpeg) < 500:
        return jerr("Image looks empty.", 400)
    question = (body.get("question") or "").strip() or CFG["vision"]["default_question"]
    t0 = time.time()
    add_history("user", f"[camera] {question}", "look")
    try:
        reply = await hermes_look(jpeg, question)
    except HermesError as e:
        return jerr(str(e), e.status)
    add_history("assistant", reply, "look")
    return web.json_response({"ok": True, "reply": reply, "ms": int((time.time() - t0) * 1000), "bytes": len(jpeg)})


async def h_speak(request):
    try:
        body = await request.json()
    except Exception:
        return jerr("Send JSON {\"text\": \"...\"}", 400)
    text = clean_for_speech(body.get("text") or "")
    if not text:
        return jerr("Nothing to say.", 400)
    loop = asyncio.get_running_loop()
    try:
        wav = await loop.run_in_executor(None, tts.synth, text)
    except Exception as e:  # noqa
        return jerr(f"Text-to-speech failed: {type(e).__name__}: {e}", 500)
    return web.Response(body=wav, content_type="audio/wav", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- feature framework endpoints
MODULES = atlas_modules.Modules(CFG, save_user_config, HERE, DATA_DIR, ask=hermes_review, assistant_name=assistant_name, provider_label=lambda: (HM.provider_label()[0] if HM else ""))
FEATURES = atlas_features.Features(HERE, DATA_DIR, CFG, hermes_text, DATA_DIR / "config.json", gate=MODULES)
MODULES.features = FEATURES


async def h_features(request):
    return web.json_response({"ok": True, "features": FEATURES.describe()})


async def h_feature_set(request):
    fid = request.match_info["fid"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        if body.get("action") == "install":
            ok, out = await FEATURES.install_deps(fid)
            return web.json_response({"ok": ok, "output": out, "features": FEATURES.describe()}, status=200 if ok else 500)
        await FEATURES.set_enabled(fid, bool(body.get("enabled")), body.get("settings"))
    except KeyError:
        return jerr("Unknown feature.", 404)
    except PermissionError as e:
        return web.json_response({"ok": False, "error": str(e), "features": FEATURES.describe()}, status=403)
    return web.json_response({"ok": True, "features": FEATURES.describe()})


def _mods_view():
    return {"ok": True, **MODULES.summary(), "explain": atlas_modules.EXPLAIN, "modules": MODULES.list(), "features": FEATURES.describe()}


async def h_modules(request):
    return web.json_response(_mods_view())


async def h_modules_ack(request):
    MODULES.set_explained()
    return web.json_response(_mods_view())


async def h_module_review(request):
    fid = request.match_info["fid"]
    try:
        if request.method == "POST":
            await MODULES.second_opinion(fid)         # asks the user's own AI; always ends with a recorded outcome
        return web.json_response({"ok": True, **MODULES.review_view(fid)})
    except KeyError:
        return jerr("No such module.", 404)


async def h_module_decide(request):
    fid = request.match_info["fid"]
    act = request.match_info["act"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        if act == "approve":
            MODULES.approve(fid, override=bool(body.get("override")))
            FEATURES.discover()
            if body.get("enable", True):
                await FEATURES.set_enabled(fid, True)
        elif act == "reject":
            MODULES.reject(fid)
            await FEATURES.set_enabled(fid, False)
        else:
            return jerr("Unknown action.", 404)
    except KeyError:
        return jerr("No such module.", 404)
    except (ValueError, PermissionError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=409)
    FEATURES.emit("features", features=FEATURES.describe())
    return web.json_response(_mods_view())


def _rem_view():
    now = time.time()
    tz = INFO.now().tzinfo
    return {"ok": True, "now": now, "items": [atlas_reminders.view(i, now, tz) for i in REMINDERS.active()], "alerts": [atlas_reminders.view(i, now, tz) for i in REMINDERS.alerts()],
            "chime": bool((CFG.get("prefs") or {}).get("reminder_chime", True))}


async def h_reminders(request):
    return web.json_response(_rem_view())


async def h_reminder_act(request):
    rid, act = request.match_info["rid"], request.match_info["act"]
    ok = REMINDERS.cancel(rid) if act == "cancel" else REMINDERS.dismiss(rid)
    FEATURES.emit("reminders")
    return web.json_response({**_rem_view(), "ok": ok})


async def h_reminder_quick(request):
    """The little form on the Reminders panel: 'call Mike at 3pm' or '10 minute timer'."""
    try:
        body = await request.json()
    except Exception:
        return jerr("Send JSON.", 400)
    text = str(body.get("text") or "").strip()[:200]
    if not text:
        return jerr("Type what to remind you about, or a timer length.", 400)
    probe = text if re.search(r"\btimer\b", text, re.I) else "remind me " + (text if re.match(r"(?i)to |that ", text) else "to " + text)
    r = await REMIND.handle(probe)
    FEATURES.emit("reminders")
    return web.json_response({**_rem_view(), "message": (r or {}).get("reply", ""), "pending": bool((r or {}).get("pending"))})


async def reminder_loop():
    """Every second: deliver what is due to the open HUD (chime + speech there).  Nothing is delivered while no HUD is open; it arrives, marked late, when one opens."""
    while True:
        try:
            await asyncio.sleep(1)
            due = REMINDERS.due(has_clients=bool(FEATURES.clients))
            for it in due:
                tz = INFO.now().tzinfo
                FEATURES.emit("reminder", **atlas_reminders.alert_payload(it, time.time(), tz))
                REMINDERS.mark_fired(it["id"])
            if due:
                FEATURES.emit("reminders")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa
            print("[bridge] reminder loop:", type(e).__name__, e, flush=True)


async def h_events(request):
    """Server-sent events: modules push say/notify/confirm/... to the HUD."""
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store", "X-Accel-Buffering": "no"})
    await resp.prepare(request)
    q = asyncio.Queue(maxsize=100)
    FEATURES.clients.add(q)
    try:
        await resp.write(b"event: hello\ndata: {}\n\n")
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), 15)
                await resp.write(f"data: {json.dumps(msg)}\n\n".encode())
            except asyncio.TimeoutError:
                await resp.write(b": keepalive\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        FEATURES.clients.discard(q)
    return resp


async def h_confirm(request):
    try:
        body = await request.json()
    except Exception:
        return jerr("Send JSON {id, ok}", 400)
    return web.json_response({"ok": FEATURES.answer(str(body.get("id")), bool(body.get("ok")))})


async def h_feature_http(request):
    fid = request.match_info["fid"]
    mod = FEATURES.mods.get(fid)
    if not mod or fid not in FEATURES.running or not hasattr(mod, "handle"):
        return jerr("Feature not running.", 404)
    return await mod.handle(FEATURES.ctxs[fid], request)


async def h_feature_js(request):
    fid = request.match_info["fid"]
    p = FEATURES.find(fid, ".js")
    if not p or not FEATURES.enabled(fid) or not FEATURES.permitted(fid):
        return web.Response(status=404, text="no ui")
    return web.Response(body=p.read_bytes(), content_type="application/javascript", headers={"Cache-Control": "no-store"})


async def _on_startup(app):
    threading.Thread(target=_hermes_boot, daemon=True, name="atlas-hermes-boot").start()
    await FEATURES.start_enabled()
    app["reminder_task"] = asyncio.create_task(reminder_loop())
    if not os.getenv("ATLAS_NO_BOOT"):
        app["hermes_watchdog"] = asyncio.create_task(hermes_watchdog())


async def _on_cleanup(app):
    for k in ("reminder_task", "hermes_watchdog"):
        t = app.get(k)
        if t:
            t.cancel()
    await FEATURES.stop_all()


async def h_profile_get(request):
    return web.json_response({"ok": True, "profile": CFG.get("profile") or {}, "assistant": CFG.get("assistant")})


async def h_profile_set(request):
    try:
        body = await request.json()
    except Exception:
        return jerr("Send JSON {name, city, teams}", 400)
    loop = asyncio.get_running_loop()
    try:
        prof, cands, msg = await loop.run_in_executor(None, update_profile, body)
    except BadInput as e:
        return jerr(str(e), 400)
    except ValueError as e:
        return jerr(str(e), 404)
    except RuntimeError as e:
        return jerr(str(e), 502)
    return web.json_response({"ok": True, "profile": prof, "assistant": CFG.get("assistant"), "candidates": cands, "message": msg})


# ---------------------------------------------------------------- self-update endpoints
def _request_exit():
    raise web.GracefulExit()


UPDATER = atlas_update.Updater(CFG, DATA_DIR, DATA_DIR / "config.json", FEATURES.emit, _request_exit)


class SettingsEnv:
    """What the settings registry / intent handler (atlas_settings.py) may touch."""
    cfg = CFG
    features = FEATURES
    updater = UPDATER
    sport_names = SPORT_NAMES

    def calendar_sources(self):
        return CALENDAR.public_sources()

    modules = MODULES

    def addons_running(self):
        return [f["title"] for f in FEATURES.describe() if f["status"] == "running"]

    async def set_addons(self, on):
        MODULES.set_master(on)
        await FEATURES.master_changed()

    def save(self, patch):
        save_user_config(patch)

    def assistant_name(self):
        return assistant_name()

    def update_profile(self, body):
        return update_profile(body)[0:3]

    def provider_label(self):
        try:
            return HM.provider_label()[0]
        except Exception:  # noqa
            return ""

    def voice_changed(self):
        with tts.lock:
            tts.engine, tts.voice = None, None      # the next sentence loads the new voice

    def autostart_get(self):
        return atlas_settings.autostart_get()

    def autostart_set(self, on):
        return atlas_settings.autostart_set(on, HERE, DATA_DIR)

    def uninstall_hint(self):
        if atlas_settings._platform() == "windows":
            return ("To uninstall, run ATLAS-Setup.bat again with the word uninstall: open a Command Prompt where the installer is and type ATLAS-Setup.bat uninstall. "
                    "It asks whether to keep your notes and settings. I haven't deleted anything.")
        return ("To uninstall, run the installer again from Terminal with the word uninstall, for example ./ATLAS-Setup.command uninstall. "
                "It asks whether to keep your notes and settings. I haven't deleted anything.")

    def reset(self):
        def change(cur):
            for k in ("profile", "prefs", "assistant"):
                cur.pop(k, None)
            if isinstance(cur.get("tts"), dict):
                for k in ("voice", "length_scale"):
                    cur["tts"].pop(k, None)
        atlas_cfgio.update_config(DATA_DIR / "config.json", change)
        CFG["profile"] = copy.deepcopy(DEFAULTS["profile"])
        CFG["assistant"] = copy.deepcopy(DEFAULTS["assistant"])
        CFG["prefs"] = {}
        CFG["tts"]["voice"], CFG["tts"]["length_scale"] = DEFAULTS["tts"]["voice"], DEFAULTS["tts"]["length_scale"]
        self.voice_changed()


SETTINGS = atlas_settings.Settings(SettingsEnv())
CALENDAR = atlas_calendar.Calendar(CFG, save_user_config, lambda: (CFG.get("profile") or {}).get("timezone") or "")
SCORES = atlas_scores.Scores()


class InfoEnv:
    """What atlas_info.py (calendar / scores / brief) may touch."""
    cfg = CFG
    calendar = CALENDAR
    scores = SCORES
    weather = None            # None = the real Open-Meteo lookup

    def assistant_name(self):
        return assistant_name()

    def reminders_upcoming(self):
        """Reminders still to come today, as short phrases for the daily brief."""
        now = INFO.now()
        end = now.replace(hour=23, minute=59, second=59).timestamp()
        return ["%s at %s" % (atlas_reminders.second_person(i["text"]), atlas_reminders.say_clock(datetime.fromtimestamp(i["due"], now.tzinfo)))
                for i in REMINDERS.active() if i["kind"] == "reminder" and i["due"] <= end]


class ModuleIntents:
    """Lets an enabled add-on answer a voice/chat request (e.g. computer_control: "open Steam").  Tried last, after every built-in."""
    async def handle(self, text):
        return await FEATURES.run_intents(text)

    @property
    def pending(self):
        return FEATURES.pending_intent()

    @pending.setter
    def pending(self, v):
        if not v:
            FEATURES.clear_pending_intent()


INFO = atlas_info.Info(InfoEnv())
REMINDERS = atlas_reminders.Store(DATA_DIR / "reminders.json")
REMIND = atlas_reminders.Reminders(REMINDERS, lambda: INFO.now(), assistant_name)
HANDLERS = [("modules", MODULES), ("reminders", REMIND), ("settings", SETTINGS), ("info", INFO), ("addon-intents", ModuleIntents())]       # asked in this order; the first that recognises the request answers (nothing here reaches Hermes)


async def local_intent(text):
    """Voice/chat requests ATLAS handles itself.  Returns None (send it to Hermes) or {reply, changed, ui, pending, ...}."""
    for name, h in HANDLERS:
        try:
            r = await h.handle(text)
        except Exception as e:  # noqa
            print("[bridge] %s intent failed: %s %s" % (name, type(e).__name__, e), flush=True)
            r = None
        if r:
            for other_name, o in HANDLERS:               # a request for one handler cancels another's pending yes/no
                if o is not h and getattr(o, "pending", None):
                    o.pending = None
            return r
    return None


def _iso(d):
    return d.isoformat(timespec="minutes") if hasattr(d, "isoformat") else d


async def h_calendar(request):
    loop = asyncio.get_running_loop()
    try:
        days = max(1, min(31, int(request.query.get("days", "7"))))
    except ValueError:
        days = 7
    def work():
        now = INFO.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        evs = CALENDAR.events(start, start + timedelta(days=days), force=request.query.get("refresh") == "1")
        return [{"title": e["title"], "start": _iso(e["start"]), "end": _iso(e["end"]), "all_day": e["all_day"], "location": e["location"], "source": e["source"]} for e in evs if e["all_day"] or e["end"] > now or e["start"].date() != now.date()], now
    evs, now = await loop.run_in_executor(None, work)
    return web.json_response({"ok": True, "events": evs, "now": _iso(now), "sources": CALENDAR.public_sources(), "configured": bool(CALENDAR.sources())})


async def h_calendar_sources(request):
    if request.method == "GET":
        return web.json_response({"ok": True, "sources": CALENDAR.public_sources()})
    try:
        body = await request.json()
    except Exception:
        return jerr("Send JSON.", 400)
    loop = asyncio.get_running_loop()
    if request.path.endswith("/remove"):
        ok = CALENDAR.remove(str(body.get("id") or body.get("name") or ""))
        return web.json_response({"ok": ok, "sources": CALENDAR.public_sources(), **({} if ok else {"error": "No such calendar."})}, status=200 if ok else 404)
    try:
        sid = CALENDAR.add(body.get("name"), body.get("value"))
    except ValueError as e:
        return jerr(str(e), 400)
    src = next((x for x in CALENDAR.sources() if x["id"] == sid), None)
    try:
        await loop.run_in_executor(None, CALENDAR.load, src, True)           # fetch once now so a wrong link is reported right away
    except Exception:  # noqa
        pass
    pub = next((x for x in CALENDAR.public_sources() if x["id"] == sid), {})
    return web.json_response({"ok": True, "id": sid, "source": pub, "sources": CALENDAR.public_sources(),
                              "message": ("Added. I found %d events." % pub.get("count", 0)) if pub.get("ok") else ("Added, but I couldn't read it yet: %s" % (pub.get("error") or "unknown problem"))})


async def h_scores(request):
    loop = asyncio.get_running_loop()
    r = await loop.run_in_executor(None, INFO.scores)
    return web.json_response({"ok": True, "live": INFO.live_scores_on(), "followed": bool((CFG.get("profile") or {}).get("sports")), **r})


async def h_brief(request):
    loop = asyncio.get_running_loop()
    b = await loop.run_in_executor(None, INFO.brief)
    return web.json_response({"ok": True, **b})


async def h_brief_morning(request):
    """The HUD asks once on open: is the automatic morning brief due?  (on, not yet given today, before 1 pm)  Marks it given."""
    prefs = CFG.setdefault("prefs", {})
    now = INFO.now()
    today = now.date().isoformat()
    if not prefs.get("brief_morning") or prefs.get("brief_last") == today or now.hour >= 13 or now.hour < 4:
        return web.json_response({"ok": True, "due": False})
    prefs["brief_last"] = today
    save_user_config({"prefs": {"brief_last": today}})
    loop = asyncio.get_running_loop()
    b = await loop.run_in_executor(None, INFO.brief)
    return web.json_response({"ok": True, "due": True, **b})


async def h_settings(request):
    return web.json_response({"ok": True, "settings": SETTINGS.describe(), "prefs": SETTINGS.prefs_view(), "assistant": CFG.get("assistant"), "profile": CFG.get("profile") or {}})


async def h_setting_set(request):
    """The Settings screen's setter: same rules as voice/chat (a risky change needs {"confirmed": true})."""
    sid = request.match_info["sid"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    s = SETTINGS.by_id(sid)
    if not s or s.kind in ("action", "list"):
        return jerr("Unknown setting.", 404)
    try:
        value = SETTINGS._coerce(s, body.get("value"))
    except ValueError as e:
        return jerr(str(e), 400)
    if SETTINGS.needs_confirm(s, value) and not body.get("confirmed"):
        return web.json_response({"ok": False, "needs_confirm": True, "prompt": SETTINGS.confirm_prompt(s, value), "error": "Confirmation needed."}, status=409)
    res = await SETTINGS._apply(s, value)
    return web.json_response({"ok": True, "message": res["reply"], "ui": res.get("ui"), "settings": SETTINGS.describe(), "prefs": SETTINGS.prefs_view()})


async def h_setting_run(request):
    """Buttons (check for updates, install, reset, uninstall help). A risky one needs {"confirmed": true}."""
    sid = request.match_info["sid"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    s = SETTINGS.by_id(sid)
    if not s or s.kind != "action":
        return jerr("Unknown action.", 404)
    if s.confirm == "always" and not body.get("confirmed"):
        return web.json_response({"ok": False, "needs_confirm": True, "prompt": SETTINGS.confirm_prompt(s, True), "error": "Confirmation needed."}, status=409)
    res = await SETTINGS._run_action(s, True)
    return web.json_response({"ok": True, "message": (res or {}).get("reply", ""), "changed": (res or {}).get("changed", []), "settings": SETTINGS.describe(), "prefs": SETTINGS.prefs_view()})


async def h_update(request):
    return web.json_response({"ok": True, **UPDATER.state()})


async def h_update_check(request):
    await UPDATER.check(force=True)
    return web.json_response({"ok": True, **UPDATER.state()})


async def h_update_install(request):
    try:
        res = await UPDATER.install()
    except RuntimeError as e:
        return jerr(str(e), 409)
    return web.json_response({"ok": True, **res})


async def h_update_settings(request):
    try:
        body = await request.json()
    except Exception:
        return jerr("Send JSON {\"enabled\": true|false}", 400)
    UPDATER.set_enabled(bool(body.get("enabled")))
    if UPDATER.enabled:
        asyncio.get_running_loop().create_task(UPDATER.check())
    return web.json_response({"ok": True, **UPDATER.state()})


async def _update_startup(app):
    UPDATER.cleanup_old()
    app["update_task"] = asyncio.get_running_loop().create_task(UPDATER.run())


async def _update_cleanup(app):
    t = app.get("update_task")
    if t:
        t.cancel()


@web.middleware
async def guard(request, handler):
    port = CFG["port"]
    host = request.headers.get("Host", "")
    if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
        return web.Response(status=403, text="Forbidden host")
    origin = request.headers.get("Origin")
    if origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
        return web.Response(status=403, text="Forbidden origin")
    resp = await handler(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def make_app():
    app = web.Application(middlewares=[guard], client_max_size=40 * 1024 * 1024)
    app.add_routes([
        web.get("/", h_index), web.get("/index.html", h_index),
        web.get("/favicon.ico", h_icon), web.get("/atlas.ico", h_icon), web.get("/atlas.png", h_icon_png), web.get("/atlas-{size:192|512}.png", h_icon_size), web.get("/manifest.webmanifest", h_manifest),
        web.post("/api/quit", h_quit), web.post("/api/open-url", h_open_url), web.post("/api/client-error", h_client_error), web.post("/api/layout/reset", h_layout_reset), web.post("/api/restart", h_restart),
        web.get("/api/health", h_health), web.get("/api/system", h_system),
        web.get("/api/history", h_history), web.post("/api/new", h_new),
        web.post("/api/chat", h_chat), web.post("/api/listen", h_listen),
        web.post("/api/look", h_look), web.post("/api/speak", h_speak),
        # feature framework
        web.get("/api/features", h_features), web.post("/api/features/{fid}", h_feature_set),
        web.get("/api/profile", h_profile_get), web.post("/api/profile", h_profile_set),
        web.get("/api/calendar", h_calendar), web.get("/api/calendar/sources", h_calendar_sources), web.post("/api/calendar/sources", h_calendar_sources), web.post("/api/calendar/sources/remove", h_calendar_sources),
        web.get("/api/reminders", h_reminders), web.post("/api/reminders/quick", h_reminder_quick), web.post("/api/reminders/{rid:[a-f0-9]+}/{act:cancel|dismiss}", h_reminder_act),
        web.get("/api/scores", h_scores), web.get("/api/brief", h_brief), web.post("/api/brief/morning", h_brief_morning),
        web.get("/api/modules", h_modules), web.post("/api/modules/ack", h_modules_ack),
        web.get("/api/modules/{fid:[a-z0-9_]+}/review", h_module_review), web.post("/api/modules/{fid:[a-z0-9_]+}/opinion", h_module_review),
        web.post("/api/modules/{fid:[a-z0-9_]+}/{act:approve|reject}", h_module_decide),
        web.get("/api/settings", h_settings), web.post("/api/settings/{sid}", h_setting_set), web.post("/api/settings/{sid}/run", h_setting_run),
        web.get("/api/update", h_update), web.post("/api/update/check", h_update_check),
        web.post("/api/update/install", h_update_install), web.post("/api/update/settings", h_update_settings),
        web.get("/api/setup/status", h_setup_status), web.post("/api/setup/install", h_setup_install),
        web.post("/api/setup/fix", h_setup_fix), web.post("/api/setup/provider", h_setup_provider),
        web.post("/api/setup/pull", h_setup_pull), web.post("/api/setup/first-run-done", h_setup_first_run_done), web.post("/api/setup/test", h_setup_test),
        web.get("/api/setup/job/{jid}", h_setup_job), web.post("/api/setup/job/{jid}/input", h_setup_job_input),
        web.post("/api/setup/job/{jid}/cancel", h_setup_job_cancel),
        web.get("/api/events", h_events), web.post("/api/confirm", h_confirm),
        web.route("*", "/api/m/{fid}/{tail:.*}", h_feature_http),
        web.get("/modules/{fid:[a-z0-9_]+}.js", h_feature_js),
    ])
    app.on_startup.append(_on_startup)
    app.on_startup.append(_update_startup)
    app.on_cleanup.append(_update_cleanup)
    app.on_cleanup.append(_on_cleanup)
    return app


def warmup():
    """Pre-load STT + TTS in the background so the first voice turn is quick."""
    def run():
        for name, fn in (("TTS", tts.ensure), ("STT", stt.load)):
            try:
                fn()
                print(f"[bridge] {name} ready", flush=True)
            except Exception as e:  # noqa
                print(f"[bridge] {name} not ready: {e}", flush=True)
    threading.Thread(target=run, daemon=True).start()


def prefetch():
    """Download/load the speech models once (used by the installers) and exit."""
    print(f"[bridge] prefetching speech models (STT '{CFG['stt']['model']}', TTS '{CFG['tts']['voice']}') ...", flush=True)
    ok = True
    try:
        stt.load()
        print(f"[bridge] STT ready ({stt.device})", flush=True)
    except Exception as e:  # noqa
        ok = False
        print(f"[bridge] STT failed: {e}", flush=True)
    try:
        tts.ensure()
        print(f"[bridge] TTS ready ({tts.engine})", flush=True)
    except Exception as e:  # noqa
        ok = False
        print(f"[bridge] TTS failed: {e}", flush=True)
    return 0 if ok else 1


def prefetch_wake():
    """Download the optional wake-word package + speech model now (the installer's 'Download wake-word model' option). Does NOT turn the feature on."""
    import importlib.util
    sub = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "vosk"], capture_output=True, text=True)
    if sub.returncode != 0:
        print("[bridge] could not install the wake-word package: " + (sub.stderr or sub.stdout)[-200:], flush=True)
        return 1
    spec = importlib.util.spec_from_file_location("wake_word", HERE / "modules" / "wake_word.py")
    ww = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ww)
    dest = Path(os.environ["ATLAS_WAKE_MODEL"]) if os.getenv("ATLAS_WAKE_MODEL") else DATA_DIR / "features" / "wake_word" / "model" / ww.MODEL_NAME
    if ww.model_ready(dest):
        print("[bridge] wake-word model already there", flush=True)
        return 0
    last = [-1]

    def prog(frac, text):
        pct = int(frac * 100)
        if pct != last[0] and pct % 10 == 0:
            last[0] = pct
            print(f"[bridge] {text} {pct}%", flush=True)
    try:
        ww.download_model(dest, prog, lambda: False)
    except Exception as e:  # noqa
        print(f"[bridge] wake-word model download failed: {e}", flush=True)
        return 1
    return 0


def main():
    if "--prefetch" in sys.argv:
        sys.exit(prefetch())
    if "--prefetch-wake" in sys.argv:
        sys.exit(prefetch_wake())
    if "--set-profile" in sys.argv:          # used by the installers: --set-profile [--name N] [--assistant A] [--city C] [--sport "Soccer|Serie A,Premier League|Arsenal,Arsenal"]...
        a = sys.argv
        body = {k: a[a.index("--" + k) + 1] for k in ("name", "city") if "--" + k in a and a.index("--" + k) + 1 < len(a)}
        if "--assistant" in a and a.index("--assistant") + 1 < len(a):
            try:
                body["assistant_name"] = clean_assistant_name(a[a.index("--assistant") + 1])
            except BadInput as e:
                print(f"[bridge] assistant name not changed: {e}", flush=True)
        sp = []
        for i, v in enumerate(a):
            if v == "--sport" and i + 1 < len(a):
                parts = (a[i + 1].split("|") + ["", ""])[:3]
                sp.append({"sport": parts[0], "leagues": parts[1], "teams": parts[2]})
        if sp:
            body["sports"] = sp
        try:
            prof, _, msg = update_profile(body)
            print("[bridge] profile saved" + (f" - {msg}" if msg else ""), flush=True)
            sys.exit(0)
        except (ValueError, RuntimeError) as e:
            body.pop("city", None)
            update_profile(body)
            print(f"[bridge] profile saved, but the city was not set: {e}", flush=True)
            sys.exit(0)
    if "--connect-hermes" in sys.argv:     # used by the installers: key + API server + start Hermes, no typing (same steps as the HUD's "Fix it")
        job, _ = HM.start_fix()
        seen = set()
        while True:
            j = HM.job(job["id"])
            for line in j["log"]:
                if line not in seen:
                    seen.add(line)
                    print("[hermes] " + line, flush=True)
            if j["status"] != "running":
                break
            time.sleep(1)
        if j["status"] == "done":
            print("[hermes] connected", flush=True)
            sys.exit(0)
        print("[hermes] " + (j["error"] or "not connected"), flush=True)
        sys.exit(1)
    if "--install-deps" in sys.argv:       # pip requirements of the features switched on in Settings (used by the installers)
        reqs = atlas_features.deps_for_enabled(FEATURES)
        if reqs:
            print("[bridge] installing feature packages:", " ".join(reqs), flush=True)
            sys.exit(subprocess.call([sys.executable, "-m", "pip", "install", "--quiet", *reqs]))
        print("[bridge] no extra feature packages needed", flush=True)
        sys.exit(0)
    load_state()
    host, port = CFG["host"], CFG["port"]
    if "--after-restart" in sys.argv:        # started by /api/restart: wait (up to 30 s) for the old bridge to free the port
        import socket
        for _ in range(60):
            with socket.socket() as so:
                try:
                    so.bind(("127.0.0.1", port))
                    break
                except OSError:
                    time.sleep(0.5)
    if host not in ("127.0.0.1", "localhost"):
        print("[bridge] Refusing to bind anything but 127.0.0.1 (the Hermes API can run commands).", flush=True)
        host = "127.0.0.1"
    print(f"[bridge] Jarvis HUD bridge  mode={CFG['mode']}  hermes={HM.api_url()}", flush=True)
    print(f"[bridge] Hermes key: {'found' if resolve_api_key() else 'not set yet - the HUD will offer to set it up'}", flush=True)
    warmup()
    print(f"[bridge] open http://127.0.0.1:{port}", flush=True)
    web.run_app(make_app(), host=host, port=port, print=None, shutdown_timeout=1.0)


if __name__ == "__main__":
    main()
