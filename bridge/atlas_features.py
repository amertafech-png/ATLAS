"""ATLAS feature framework: optional feature modules with settings toggles.

A feature is a Python file  bridge/modules/<id>.py  (shipped with ATLAS)  or  <ATLAS data dir>/modules/<id>.py
(drop-in, survives app updates).  Files starting with "_" are ignored.  A module may define:

    META = {"title": "...", "description": "...", "requirements": ["pip-package", ...], "ui": False, "consent": "optional text the HUD asks the user to confirm before the feature is switched on"}
    async def start(ctx): ...          # called when the feature is enabled (and at bridge start if enabled)
    async def stop(ctx): ...           # called when disabled / bridge shuts down
    async def handle(ctx, request): .. # optional HTTP handler, reachable at  /api/m/<id>/<anything>
If META["ui"] is true, the HUD also loads  bridge/modules/<id>.js  (same folder rules) which calls ATLAS.register(...).

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

# Optional add-on modules.  They show up in Settings as "not installed yet" until a module file exists.
# (The daily brief, calendar and reminders are built into ATLAS itself, not add-ons.)
CATALOG = {
    "wake_word": {"title": "Wake word",
                  "description": "Say \"Hey <name>\" to start talking, hands-free. Fully local (Vosk).",
                  "requirements": ["vosk"], "ui": True},
    "computer_control": {"title": "Computer control",
                         "description": "Say \"open Steam\" or \"launch Hades\". Opens only apps and games found installed on this computer; asks first; cannot run commands or change files.",
                         "requirements": []},
    "gestures": {"title": "Hand gestures",
                 "description": "Wave / point / pinch controls from the webcam, own implementation.",
                 "requirements": [], "ui": True},
    "telegram": {"title": "Telegram phone access",
                 "description": "Talk to ATLAS from your phone through your own Telegram bot (outbound polling only).",
                 "requirements": []},
}


def _load_file(path: Path, fid: str):
    spec = importlib.util.spec_from_file_location(f"atlas_feature_{fid}", path)
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
        """What the user calls the assistant (default ATLAS). Wake word modules listen for ctx.wake_phrase ("Hey <name>")."""
        return ((self._fw.cfg.get("assistant") or {}).get("name") or "ATLAS")

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
    def __init__(self, app_dir: Path, data_dir: Path, cfg: dict, ask, user_config_path: Path, gate=None):
        """gate (atlas_modules.Modules): decides which module files may be imported and run.  A module that is not allowed is never imported."""
        self.gate = gate
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
        for fid in list(self.mods):
            if fid not in self.running and not self.permitted(fid):
                self.mods.pop(fid, None)               # master switch off, or the file changed since it was approved: forget the loaded copy
        ids = set(CATALOG)
        for d in self.dirs:
            if d.is_dir():
                ids.update(p.stem for p in d.glob("*.py") if not p.name.startswith("_"))
        for fid in sorted(ids):
            p = self.find(fid)
            if p and fid not in self.mods and self.permitted(fid):
                try:
                    self.mods[fid] = _load_file(p, fid)
                    self.errors.pop(fid, None)
                except Exception as e:  # noqa
                    self.errors[fid] = f"{type(e).__name__}: {e}"
        self.ids = sorted(ids)

    def permitted(self, fid):
        """May this module's code be imported/run right now?  (master switch on, and shipped-and-unchanged or approved by the user)"""
        return self.gate is None or (self.gate.master_on() and self.gate.allowed(fid))

    def approval_state(self, fid):
        return self.gate.state(fid) if self.gate else "trusted"

    def meta(self, fid):
        m = dict(CATALOG.get(fid, {}))
        mod = self.mods.get(fid)
        if mod and isinstance(getattr(mod, "META", None), dict):
            m.update(mod.META)
        elif self.gate and self.find(fid):
            m.update(self.gate.meta(fid))              # read from the file text without running it
        m.setdefault("title", fid)
        return m

    def describe(self):
        out = []
        for fid in self.ids:
            m = self.meta(fid)
            has_file = self.find(fid) is not None
            installed = fid in self.mods or has_file
            ast_ = self.approval_state(fid) if has_file else "missing"
            missing = _missing(m.get("requirements")) if fid in self.mods else []
            if fid in self.errors:
                status = "error"
            elif not has_file:
                status = "not_installed"
            elif ast_ in ("pending", "changed", "rejected"):
                status = "needs_approval"
            elif not self.permitted(fid):
                status = "addons_off"
            elif missing and self.enabled(fid):
                status = "needs_install"
            elif fid in self.running:
                status = "running"
            else:
                status = "off" if not self.enabled(fid) else "starting"
            out.append({"id": fid, "title": m["title"], "description": m.get("description", ""), "installed": installed,
                        "enabled": self.enabled(fid), "status": status, "missing": missing, "error": self.errors.get(fid, ""),
                        "ui": bool(m.get("ui")) and bool(self.find(fid, ".js")) and self.permitted(fid), "consent": m.get("consent", ""), "settings": self.settings(fid),
                        "approval": ast_})
        return out

    # ---- lifecycle ----
    async def start_one(self, fid):
        mod = self.mods.get(fid)
        if not mod or fid in self.running or not self.permitted(fid):
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

    async def master_changed(self):
        """The add-ons master switch was flipped: stop everything, or load and start what is switched on."""
        if self.gate and not self.gate.master_on():
            await self.stop_all()
        else:
            self.discover()
            await self.start_enabled()
        self.emit("features", features=self.describe())

    async def start_enabled(self):
        for fid in self.ids:
            if self.enabled(fid):
                await self.start_one(fid)

    def pending_intent(self):
        """True while an enabled module is waiting for the user's yes/no or choice by voice."""
        for fid in list(self.running):
            fn = getattr(self.mods.get(fid), "has_pending", None)
            try:
                if fn and fn(self.ctxs[fid]):
                    return True
            except Exception:  # noqa
                pass
        return False

    def clear_pending_intent(self):
        for fid in list(self.running):
            fn = getattr(self.mods.get(fid), "reset", None)
            try:
                if fn:
                    fn(self.ctxs[fid])
            except Exception:  # noqa
                pass

    async def run_intents(self, text):
        """Voice/chat requests an enabled module wants to answer (async def intent(ctx, text) -> {reply,...} | None).
        A module that is waiting for a yes/no goes first."""
        order = sorted(self.running, key=lambda f: 0 if self._has_pending(f) else 1)
        for fid in order:
            fn = getattr(self.mods.get(fid), "intent", None)
            if not fn:
                continue
            try:
                r = fn(self.ctxs[fid], text)
                if asyncio.iscoroutine(r):
                    r = await r
            except Exception as e:  # noqa
                print(f"[bridge] feature '{fid}' intent failed: {type(e).__name__}: {e}", flush=True)
                continue
            if r:
                return r
        return None

    def _has_pending(self, fid):
        fn = getattr(self.mods.get(fid), "has_pending", None)
        try:
            return bool(fn and fn(self.ctxs[fid]))
        except Exception:  # noqa
            return False

    async def stop_all(self):
        for fid in list(self.running):
            await self.stop_one(fid)

    async def set_enabled(self, fid, enabled, settings=None):
        if fid not in self.ids:
            raise KeyError(fid)
        if enabled and self.gate and self.find(fid):
            if not self.gate.master_on():
                raise PermissionError("Add-on modules are switched off. Turn them on in Settings first.")
            if not self.gate.allowed(fid):
                raise PermissionError("This add-on is new and hasn't been approved yet. Review it in Settings, Add-on modules.")
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
