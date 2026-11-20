"""Wake word: say "Hey <assistant name>" (default "Hey MODI") and MODI starts listening - fully local, no account, no API key.

How it works
  * The HUD window opens the microphone (the browser asks permission and shows its own mic indicator), cuts the audio
    into 16 kHz mono chunks, and only streams chunks that contain speech to this module over a local WebSocket
    (/api/m/wake_word/ws).  Silence is never sent, so the CPU is idle most of the time.
  * Here the chunks go to Vosk's small English model (Kaldi, Apache-2.0, ~40 MB, downloaded once on first use into the
    feature's data folder, SHA-256 verified).  The model does free-form transcription of the short utterance, and the
    text is matched against ctx.wake_phrase ("Hey " + the assistant name).  Nothing is recorded or kept; transcripts are
    thrown away right after matching.  Any name works with no training: rename the assistant and the phrase follows.
  * On a match the module emits a "wake" event and tells the HUD window that is streaming, which plays a short tone and
    starts the normal listen -> /api/listen -> chat -> speak flow.

HTTP (all under /api/m/wake_word/):  GET status | POST mute {muted} | POST aliases {aliases:[...]} | POST retry | GET ws
Environment: MODI_WAKE_MODEL=<folder of an already-downloaded Vosk model> skips the download (used by tests / offline installs).
"""
import asyncio
import difflib
import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_URL = "https://alphacephei.com/vosk/models/" + MODEL_NAME + ".zip"
MODEL_SHA256 = "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498"
MODEL_MB = 41

META = {
    "title": "Wake word",
    "description": "Say \"Hey <name>\" to start talking, hands-free. Runs fully on this computer (Vosk, no account or key). "
                   "The HUD keeps your microphone open while it is armed and only processes short bursts of speech; "
                   "nothing is recorded or sent anywhere. First use downloads a ~41 MB offline speech model.",
    "consent": "Turn on the wake word?\n\nMODI will keep your microphone open while this window is open so it can hear "
               "\"Hey <name>\". Audio is analysed on this computer only, never saved or sent anywhere, and your browser "
               "shows its microphone indicator while it is armed. You can mute it any time. The first time, a ~41 MB "
               "offline speech model is downloaded.",
    "requirements": ["vosk"],
    "ui": True,
}

SAMPLE_RATE = 16000
HEY = {"hey", "hay", "he", "hei"}
DIGITS = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven",
          "8": "eight", "9": "nine"}


def tokens(text):
    out = []
    for t in re.findall(r"[a-z0-9']+", (text or "").lower()):
        t = t.strip("'")
        if t:
            out.append(DIGITS.get(t, t))
    return out


class Matcher:
    """Does a transcript start with the wake phrase?  Exact words; unknown (out-of-vocabulary) names also match fuzzily."""

    def __init__(self, phrase, aliases=(), oov=lambda w: False):
        self.variants = []
        for p in [phrase] + [("hey " + a) for a in aliases if a and a.strip()]:
            t = tokens(p)
            if t:
                self.variants.append(t)
        self.oov = oov
        self.max_skip = 2           # the phrase may start after up to two filler words ("um", "okay")

    def _tok(self, p, h, first):
        if first and p in HEY:
            return h in HEY
        if h == p:
            return True
        if len(p) >= 4 and self.oov(p):
            return difflib.SequenceMatcher(None, p, h).ratio() >= 0.75
        return False

    def match(self, text):
        h = tokens(text)
        for v in self.variants:
            for i in range(0, min(self.max_skip, max(0, len(h) - len(v))) + 1):
                if len(h) - i >= len(v) and all(self._tok(p, h[i + k], k == 0) for k, p in enumerate(v)):
                    return True
        return False


class Spotter:
    """One audio stream -> wake phrase.  feed() takes 16 kHz mono int16 PCM bytes; end() flushes at the end of an utterance."""

    def __init__(self, model, phrase_fn, aliases_fn=lambda: ()):
        from vosk import KaldiRecognizer
        self._KR = KaldiRecognizer
        self.model, self.phrase_fn, self.aliases_fn = model, phrase_fn, aliases_fn
        self.rec = self._new()
        self.audio_s = 0.0
        self.cpu_s = 0.0

    def _new(self):
        r = self._KR(self.model, SAMPLE_RATE)
        r.SetMaxAlternatives(5)             # final results also carry runner-up transcripts ("pay nova" / "hey nova")
        return r

    @staticmethod
    def _texts(raw):
        j = json.loads(raw)
        alts = [a.get("text", "") for a in j.get("alternatives", [])]
        return alts or [j.get("text", "")]

    def _any(self, texts):
        m = self._matcher()
        return any(t and m.match(t) for t in texts)

    def _matcher(self):
        return Matcher(self.phrase_fn(), self.aliases_fn(), oov=lambda w: self.model.vosk_model_find_word(w) < 0)

    def reset(self):
        self.rec = self._new()

    def feed(self, pcm):
        t0 = time.thread_time()
        try:
            if self.rec.AcceptWaveform(pcm):
                hit = self._any(self._texts(self.rec.Result()))
            else:
                hit = self._any([json.loads(self.rec.PartialResult()).get("partial", "")])
        finally:
            self.audio_s += len(pcm) / 2 / SAMPLE_RATE
            self.cpu_s += time.thread_time() - t0
        if hit:
            self.reset()
        return hit

    def end(self):
        t0 = time.thread_time()
        try:
            hit = self._any(self._texts(self.rec.FinalResult()))
        finally:
            self.cpu_s += time.thread_time() - t0
        self.reset()
        return hit


# ------------------------------------------------------------------ model download
def model_dir(ctx):
    env = os.environ.get("MODI_WAKE_MODEL")
    return Path(env) if env else ctx.data_dir / "model" / MODEL_NAME


def model_ready(path):
    return (path / "am" / "final.mdl").exists() and (path / "conf" / "model.conf").exists()


def download_model(dest, progress, cancelled):
    """Download + verify + unpack the Vosk model into dest.  progress(fraction, text) is called while it runs."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / (MODEL_NAME + ".part")
    zpath = dest.parent / (MODEL_NAME + ".zip.part")
    shutil.rmtree(tmp, ignore_errors=True)
    req = urllib.request.Request(MODEL_URL, headers={"User-Agent": "MODI-wake-word"})
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=30) as r, open(zpath, "wb") as f:
        total = int(r.headers.get("Content-Length") or MODEL_MB * 1024 * 1024)
        got = 0
        while True:
            if cancelled():
                raise RuntimeError("cancelled")
            b = r.read(256 * 1024)
            if not b:
                break
            f.write(b)
            h.update(b)
            got += len(b)
            progress(min(0.95, got / total * 0.95), "Downloading speech model")
    if h.hexdigest() != MODEL_SHA256:
        zpath.unlink(missing_ok=True)
        raise RuntimeError("The downloaded speech model failed its SHA-256 check and was discarded.")
    progress(0.96, "Unpacking speech model")
    with zipfile.ZipFile(zpath) as z:
        root = tmp.resolve()
        for n in z.namelist():
            if not (root / n).resolve().is_relative_to(root):
                raise RuntimeError("Unsafe path in the speech model archive.")
        z.extractall(tmp)
    inner = tmp / MODEL_NAME
    shutil.rmtree(dest, ignore_errors=True)
    shutil.move(str(inner if inner.exists() else tmp), str(dest))
    shutil.rmtree(tmp, ignore_errors=True)
    zpath.unlink(missing_ok=True)
    progress(1.0, "Speech model ready")


# ------------------------------------------------------------------ module state
S = {"state": "off", "progress": 0.0, "text": "", "error": "", "muted": False, "model": None, "pool": None,
     "task": None, "stop": False, "sockets": set(), "last_wake": 0.0, "wakes": 0, "audio_s": 0.0, "cpu_s": 0.0}


def _aliases(ctx):
    a = ctx.settings.get("aliases") or []
    return [str(x).strip() for x in a if str(x).strip()][:8]


def status(ctx):
    return {"ok": True, "state": S["state"], "progress": round(S["progress"], 3), "text": S["text"], "error": S["error"],
            "muted": S["muted"], "phrase": ctx.wake_phrase, "aliases": _aliases(ctx), "listeners": len(S["sockets"]),
            "engine": "Vosk " + MODEL_NAME.replace("vosk-model-", ""), "model_mb": MODEL_MB, "wakes": S["wakes"],
            "audio_s": round(S["audio_s"], 1), "cpu_s": round(S["cpu_s"], 2)}


def _push(ctx):
    ctx.emit("wake_state", **{k: v for k, v in status(ctx).items() if k != "ok"})


def _set(ctx, state=None, progress=None, text=None, error=None):
    if state is not None:
        S["state"] = state
    if progress is not None:
        S["progress"] = progress
    if text is not None:
        S["text"] = text
    if error is not None:
        S["error"] = error
    _push(ctx)


async def _prepare(ctx):
    """Make sure the model is on disk (download with progress) and loaded."""
    loop = asyncio.get_running_loop()
    path = model_dir(ctx)
    try:
        if not model_ready(path):
            _set(ctx, "downloading", 0.0, "Downloading speech model (~%d MB, one time)" % MODEL_MB, "")
            last = {"v": -1.0}

            def prog(f, text):
                S["progress"], S["text"] = f, text
                if f - last["v"] >= 0.02 or f >= 1:
                    last["v"] = f
                    loop.call_soon_threadsafe(_push, ctx)
            await loop.run_in_executor(None, download_model, path, prog, lambda: S["stop"])
        _set(ctx, "loading", 0.97, "Loading speech model", "")

        def load():
            from vosk import Model, SetLogLevel
            SetLogLevel(-1)
            return Model(str(path))
        S["model"] = await loop.run_in_executor(None, load)
        _set(ctx, "ready", 1.0, "Armed" if not S["muted"] else "Muted", "")
        ctx.log("ready (model %s)" % path)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa
        if S["stop"]:
            return
        msg = ("Couldn't download the speech model - check your internet connection and try again."
               if isinstance(e, OSError) else f"{type(e).__name__}: {e}")
        _set(ctx, "error", 0.0, "", msg)
        ctx.log("error: " + msg)


async def start(ctx):
    S.update(stop=False, model=None, state="loading", error="", progress=0.0, wakes=0, audio_s=0.0, cpu_s=0.0)
    S["muted"] = bool(ctx.settings.get("muted"))
    S["pool"] = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wake")
    S["task"] = asyncio.create_task(_prepare(ctx))


async def stop(ctx):
    S["stop"] = True
    if S["task"]:
        S["task"].cancel()
    for ws in list(S["sockets"]):
        try:
            await ws.close()
        except Exception:  # noqa
            pass
    S["sockets"].clear()
    if S["pool"]:
        S["pool"].shutdown(wait=False)
    S.update(model=None, state="off", pool=None, task=None)


def _json(data, status_=200):
    from aiohttp import web
    return web.json_response(data, status=status_)


async def handle(ctx, request):
    from aiohttp import web
    tail = request.match_info.get("tail", "")
    if tail == "status":
        return _json(status(ctx))
    if tail in ("mute", "aliases", "retry") and request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            body = {}
        if tail == "mute":
            S["muted"] = bool(body.get("muted"))
            ctx.save_settings({**ctx.settings, "muted": S["muted"]})
            if S["state"] == "ready":
                S["text"] = "Muted" if S["muted"] else "Armed"
            _push(ctx)
        elif tail == "aliases":
            raw = body.get("aliases") or []
            if isinstance(raw, str):
                raw = raw.split(",")
            al = []
            for a in raw:
                a = re.sub(r"[^\w .'-]", "", str(a)).strip()[:20]
                if a and a.lower() not in [x.lower() for x in al]:
                    al.append(a)
            ctx.save_settings({**ctx.settings, "aliases": al[:8]})
            _push(ctx)
        elif tail == "retry" and S["state"] == "error":
            S["task"] = asyncio.create_task(_prepare(ctx))
        return _json(status(ctx))
    if tail == "ws":
        return await _ws(ctx, request)
    return _json({"ok": False, "error": "unknown endpoint"}, 404)


async def _ws(ctx, request):
    from aiohttp import web
    ws = web.WebSocketResponse(max_msg_size=1 << 20, heartbeat=20)
    await ws.prepare(request)
    if S["state"] != "ready" or S["model"] is None:
        await ws.send_json({"type": "state", "state": S["state"]})
        await ws.close()
        return ws
    loop = asyncio.get_running_loop()
    sp = Spotter(S["model"], lambda: ctx.wake_phrase, lambda: _aliases(ctx))
    S["sockets"].add(ws)
    await ws.send_json({"type": "ready", "phrase": ctx.wake_phrase})

    async def hit():
        now = time.time()
        if now - S["last_wake"] < 2.5:          # one trigger per utterance / short cooldown
            return
        S["last_wake"] = now
        S["wakes"] += 1
        ctx.log("wake phrase heard")
        ctx.emit("wake", phrase=ctx.wake_phrase)
        await ws.send_json({"type": "wake", "phrase": ctx.wake_phrase})

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.BINARY:
                if S["muted"] or S["pool"] is None:
                    continue
                if await loop.run_in_executor(S["pool"], sp.feed, msg.data):
                    await hit()
            elif msg.type == web.WSMsgType.TEXT:
                try:
                    j = json.loads(msg.data)
                except Exception:
                    continue
                if j.get("end") and not S["muted"] and S["pool"] is not None:
                    if await loop.run_in_executor(S["pool"], sp.end):
                        await hit()
                elif j.get("reset"):
                    sp.reset()
            elif msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE):
                break
    finally:
        S["audio_s"] += sp.audio_s
        S["cpu_s"] += sp.cpu_s
        S["sockets"].discard(ws)
    return ws
