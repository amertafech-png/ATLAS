"""MODI feature framework: optional feature modules with settings toggles.

A feature is a Python file  bridge/modules/<id>.py  (shipped with MODI)  or  <MODI data dir>/modules/<id>.py
(drop-in, survives app updates).  Files starting with "_" are ignored.  A module may define:

    META = {"title": "...", "description": "...", "requirements": ["pip-package", ...], "ui": False, "consent": "optional text the HUD asks the user to confirm before the feature is switched on"}
    async def start(ctx): ...          # called when the feature is enabled (and at bridge start if enabled)
    async def stop(ctx): ...           # called when disabled / bridge shuts down
    async def handle(ctx, request): .. # optional HTTP handler, reachable at  /api/m/<id>/<anything>
If META["ui"] is true, the HUD also loads  bridge/modules/<id>.js  (same folder rules) which calls MODI.register(...).

ctx gives a module everything it may use, so features never import bridge.py internals:
    ctx.settings     dict of this feature's saved settings          ctx.save_settings(dict)
    ctx.data_dir     private folder for this feature                 ctx.config   read-only copy of the bridge config
    ctx.emit(type, **payload)   push an event to every open HUD (SSE /api/events)
    ctx.say(text)               HUD speaks text with the normal TTS         ctx.notify(text, level="info")
    await ctx.ask(text)         send text to Hermes, get the reply text      ctx.log(msg)
    await ctx.confirm(prompt, detail="")  ask the user in the HUD (Yes/No), returns bool; False on timeout.
                                Anything destructive (delete/move files, close apps, send messages) MUST use this.
"""
import asyncio
import importlib.util
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

# Features planned for MODI.  They show up in Settings as "not installed yet" until a module file exists.
CATALOG = {
    "wake_word": {"title": "Wake word",
                  "description": "Say \"Hey <name>\" to start talking, hands-free. Fully local (Vosk).",
                  "requirements": ["vosk"], "ui": True},
    "daily_brief": {"title": "Daily brief",
                    "description": "Weather, calendar, sports and news panel, built by Hermes on a schedule.",
                    "requirements": [], "ui": True},
    "reminders": {"title": "Reminders & timers",
                  "description": "Set reminders and timers by voice; MODI speaks them when due.", "requirements": []},
    "computer_control": {"title": "Computer control",
                         "description": "Open apps, play music, find files. Destructive actions always ask first.",
                         "requirements": []},
    "gestures": {"title": "Hand gestures",
                 "description": "Wave / point / pinch controls from the webcam, own implementation.",
                 "requirements": [], "ui": True},
    "telegram": {"title": "Telegram phone access",
                 "description": "Talk to MODI from your phone through your own Telegram bot (outbound polling only).",
                 "requirements": []},
}


def _load_file(path: Path, fid: str):
    spec = importlib.util.spec_from_file_location(f"modi_feature_{fid}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _missing(reqs):
    out = []
    for r in reqs or []:
        name = re.split(r"[<>=!~\[ ]", r, 1)[0].replace("-", "_")
        if importlib.util.find_spec(name) is None:
            out.append(r)
    return out


class Ctx:
    def __init__(self, fw, fid):
        self._fw, self.id = fw, fid
        self.data_dir = fw.data_dir / "features" / fid
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    @property
    def settings(self):
        return self._fw.settings(self.id)

    @property
    def config(self):
        return self._fw.cfg_view()

    def save_settings(self, new):
        self._fw.set_settings(self.id, new)

    def emit(self, type_, **payload):
        self._fw.emit(type_, feature=self.id, **payload)

    @property
    def assistant_name(self):
        """What the user calls the assistant (default MODI). Wake word modules listen for ctx.wake_phrase ("Hey <name>")."""
        return ((self._fw.cfg.get("assistant") or {}).get("name") or "MODI")

    @property
    def wake_phrase(self):
        return ((self._fw.cfg.get("assistant") or {}).get("wake_phrase") or "Hey " + self.assistant_name)

    def say(self, text):
        self.emit("say", text=str(text))

    def notify(self, text, level="info"):
        self.emit("notify", text=str(text), level=level)

    def log(self, msg):
        print(f"[feature:{self.id}] {msg}", flush=True)

    async def ask(self, text):
        return await self._fw.ask(text)

    async def confirm(self, prompt, detail="", timeout=60):
        return await self._fw.confirm(self.id, prompt, detail, timeout)


class Features:
    def __init__(self, app_dir: Path, data_dir: Path, cfg: dict, ask, user_config_path: Path):
        self.app_dir, self.data_dir, self.cfg = app_dir, data_dir, cfg
        self.ask, self.user_config_path = ask, user_config_path
        self.dirs = [app_dir / "modules", data_dir / "modules"]
        self.mods, self.ctxs, self.errors, self.running = {}, {}, {}, set()
        self.clients, self.pending = set(), {}
        self.discover()

    # ---- config ----
    def entry(self, fid):
        return self.cfg.setdefault("features", {}).setdefault(fid, {})

    def enabled(self, fid):
        return bool(self.entry(fid).get("enabled"))

    def settings(self, fid):
        return self.entry(fid).setdefault("settings", {})

    def cfg_view(self):
        return json.loads(json.dumps({k: v for k, v in self.cfg.items() if k != "hermes"}))

    def _persist(self):
        try:
            cur = json.loads(self.user_config_path.read_text(encoding="utf-8-sig")) if self.user_config_path.exists() else {}
        except Exception:
            cur = {}
        cur["features"] = self.cfg.get("features", {})
        self.user_config_path.write_text(json.dumps(cur, indent=2), encoding="utf-8")

    def set_settings(self, fid, new):
        self.entry(fid)["settings"] = dict(new)
        self._persist()

    # ---- discovery ----
    def find(self, fid, ext=".py"):
        for d in reversed(self.dirs):          # user drop-in wins over the bundled copy
            p = d / f"{fid}{ext}"
            if p.exists():
                return p
        return None

    def discover(self):
        ids = set(CATALOG)
        for d in self.dirs:
            if d.is_dir():
                ids.update(p.stem for p in d.glob("*.py") if not p.name.startswith("_"))
        for fid in sorted(ids):
            p = self.find(fid)
            if p and fid not in self.mods:
                try:
                    self.mods[fid] = _load_file(p, fid)
                    self.errors.pop(fid, None)
                except Exception as e:  # noqa
                    self.errors[fid] = f"{type(e).__name__}: {e}"
        self.ids = sorted(ids)

    def meta(self, fid):
        m = dict(CATALOG.get(fid, {}))
        mod = self.mods.get(fid)
        if mod and isinstance(getattr(mod, "META", None), dict):
            m.update(mod.META)
        m.setdefault("title", fid)
        return m

    def describe(self):
        out = []
        for fid in self.ids:
            m = self.meta(fid)
            installed = fid in self.mods
            missing = _missing(m.get("requirements")) if installed else []
            if fid in self.errors:
                status = "error"
            elif not installed:
                status = "not_installed"
            elif missing and self.enabled(fid):
                status = "needs_install"
            elif fid in self.running:
                status = "running"
            else:
                status = "off" if not self.enabled(fid) else "starting"
            out.append({"id": fid, "title": m["title"], "description": m.get("description", ""), "installed": installed,
                        "enabled": self.enabled(fid), "status": status, "missing": missing, "error": self.errors.get(fid, ""),
                        "ui": bool(m.get("ui")) and bool(self.find(fid, ".js")), "consent": m.get("consent", ""), "settings": self.settings(fid)})
        return out

    # ---- lifecycle ----
    async def start_one(self, fid):
        mod = self.mods.get(fid)
        if not mod or fid in self.running:
            return
        if _missing(self.meta(fid).get("requirements")):
            return
        ctx = self.ctxs.setdefault(fid, Ctx(self, fid))
        try:
            fn = getattr(mod, "start", None)
            if fn:
                r = fn(ctx)
                if asyncio.iscoroutine(r):
                    await r
            self.running.add(fid)
            self.errors.pop(fid, None)
            print(f"[bridge] feature '{fid}' started", flush=True)
        except Exception as e:  # noqa
            self.errors[fid] = f"{type(e).__name__}: {e}"
            print(f"[bridge] feature '{fid}' failed to start: {e}", flush=True)

    async def stop_one(self, fid):
        mod = self.mods.get(fid)
        if fid not in self.running:
            return
        try:
            fn = getattr(mod, "stop", None)
            if fn:
                r = fn(self.ctxs[fid])
                if asyncio.iscoroutine(r):
                    await r
        except Exception as e:  # noqa
            print(f"[bridge] feature '{fid}' stop error: {e}", flush=True)
        self.running.discard(fid)

    async def start_enabled(self):
        for fid in self.ids:
            if self.enabled(fid):
                await self.start_one(fid)

    async def stop_all(self):
        for fid in list(self.running):
            await self.stop_one(fid)

    async def set_enabled(self, fid, enabled, settings=None):
        if fid not in self.ids:
            raise KeyError(fid)
        self.entry(fid)["enabled"] = bool(enabled)
        if settings is not None:
            self.entry(fid)["settings"] = dict(settings)
        self._persist()
        if enabled:
            await self.start_one(fid)
        else:
            await self.stop_one(fid)
        self.emit("features", features=self.describe())

    async def install_deps(self, fid):
        reqs = self.meta(fid).get("requirements") or []
        if not reqs:
            return True, ""
        loop = asyncio.get_running_loop()

        def run():
            p = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", *reqs], capture_output=True, text=True)
            return p.returncode == 0, (p.stderr or p.stdout)[-600:]
        ok, out = await loop.run_in_executor(None, run)
        if ok and self.enabled(fid):
            await self.start_one(fid)
        self.emit("features", features=self.describe())
        return ok, out

    # ---- events / confirmations ----
    def emit(self, type_, **payload):
        msg = {"type": type_, "t": time.time(), **payload}
        for q in list(self.clients):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    async def confirm(self, fid, prompt, detail, timeout):
        if not self.clients:
            return False                      # nobody to ask -> never act
        cid = uuid.uuid4().hex[:10]
        fut = asyncio.get_running_loop().create_future()
        self.pending[cid] = fut
        self.emit("confirm", id=cid, prompt=prompt, detail=detail, feature=fid, timeout=timeout)
        try:
            return bool(await asyncio.wait_for(fut, timeout))
        except asyncio.TimeoutError:
            return False
        finally:
            self.pending.pop(cid, None)

    def answer(self, cid, ok):
        fut = self.pending.get(cid)
        if fut and not fut.done():
            fut.set_result(bool(ok))
            return True
        return False


def deps_for_enabled(fw: Features):
    """Used by `bridge.py --install-deps`: pip requirements of every enabled, installed feature."""
    reqs = []
    for fid in fw.ids:
        if fw.enabled(fid) and fid in fw.mods:
            reqs += _missing(fw.meta(fid).get("requirements"))
    return reqs
