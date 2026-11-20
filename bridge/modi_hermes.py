"""MODI <-> Hermes connection manager.

Takes a non-technical user from "just installed MODI" to "chat works":
  * find the Hermes CLI even when it is not on PATH (fresh Windows installs only add it to the *user* PATH
    of future terminals; MODI is often started from a shortcut that never sees that PATH)
  * find (or create) the Hermes API-server key - Hermes keeps it in <HERMES_HOME>/.env as API_SERVER_KEY, or in
    config.yaml under gateway.api_server.key.  Never printed, never shown in the HUD, never in the repo.
  * enable the API server, start / restart the Hermes gateway, check /health on 127.0.0.1:8642
  * install Hermes with its official installer when it is missing
  * sign in to an AI provider by driving Hermes' own commands (`hermes auth add ...`, `hermes config set ...`)
  * send a test message and report "Connected to X"

Everything here is plain blocking code (subprocess / urllib); bridge.py runs it in worker threads.
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
PLATFORM = "win" if IS_WIN else "mac" if IS_MAC else "linux"

INSTALL_PS1 = "https://hermes-agent.nousresearch.com/install.ps1"
INSTALL_SH = "https://hermes-agent.nousresearch.com/install.sh"
OLLAMA_URL = "http://127.0.0.1:11434"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
URL_RE = re.compile(r"https?://[^\s\"'<>\x1b]+")
USERCODE_RE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4,5})\b")
PLACEHOLDER_KEYS = ("change-me", "changeme", "your-secret", "your_secret", "example")

# provider id (what MODI's HUD sends) -> how to connect it through Hermes
PROVIDERS = {
    "chatgpt":     {"label": "ChatGPT",        "kind": "oauth", "hermes": "openai-codex",
                    "auth": ["auth", "add", "openai-codex", "--browser"], "auth_fallback": ["auth", "add", "openai-codex", "--type", "oauth"],
                    "model": "gpt-6-sol"},
    "grok":        {"label": "Grok (SuperGrok / X Premium)", "kind": "oauth", "hermes": "xai-oauth",
                    "auth": ["auth", "add", "xai-oauth"], "model": ""},
    "grok_key":    {"label": "Grok (xAI API key)", "kind": "key", "hermes": "xai", "env": "XAI_API_KEY", "model": ""},
    "claude":      {"label": "Claude",         "kind": "key", "hermes": "anthropic", "env": "ANTHROPIC_API_KEY", "model": "claude-sonnet-4-6"},
    "claude_oauth": {"label": "Claude (Max plan sign-in)", "kind": "oauth", "hermes": "anthropic",
                     "auth": ["auth", "add", "anthropic", "--type", "oauth"], "model": "claude-sonnet-4-6"},
    "openrouter":  {"label": "OpenRouter",     "kind": "key", "hermes": "openrouter", "env": "OPENROUTER_API_KEY", "model": "anthropic/claude-sonnet-4.6"},
    "ollama":      {"label": "Local model (Ollama)", "kind": "local", "hermes": "custom", "model": ""},
}
PROVIDER_LABELS = {"openai-codex": "ChatGPT", "xai-oauth": "Grok", "xai": "Grok", "anthropic": "Claude", "openrouter": "OpenRouter",
                   "custom": "a local model", "nous": "Nous Portal", "ollama": "a local model"}
KEY_RE = re.compile(r"^[A-Za-z0-9._~+/=:\-]{8,512}$")


def _flags(detached=False):
    if not IS_WIN:
        return {"start_new_session": True} if detached else {}
    f = 0x08000000                                    # CREATE_NO_WINDOW
    if detached:
        f |= 0x00000008 | 0x00000200                  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return {"creationflags": f}


def clean(text):
    return ANSI.sub("", text or "").replace("\r", "")


# --------------------------------------------------------------------------- files: .env / config.yaml
def read_env_file(path):
    """Parse KEY=VALUE lines (last one wins, like python-dotenv).  Returns {} if unreadable."""
    out = {}
    try:
        for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if not m or line.lstrip().startswith("#"):
                continue
            v = m.group(2)
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            else:
                v = re.sub(r"\s+#.*$", "", v)
            out[m.group(1)] = v
    except OSError:
        pass
    return out


def set_env_values(path, updates, keep_backups=5):
    """Set KEY=VALUE pairs in a .env file: back it up first (timestamped copy), keep every other line
    untouched, replace an existing entry in place (extra duplicates removed), append new ones, write
    atomically, owner-only permissions.  Values are validated so they cannot inject extra lines."""
    path = Path(path)
    for k, v in updates.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", k) or not re.fullmatch(r"[A-Za-z0-9._~+/=:\-]{1,512}", str(v)):
            raise ValueError("refusing to write an unsafe value for " + k)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    mode = 0o600                                          # the file holds secrets: owner-only, always
    if path.exists():
        existing = path.read_bytes().decode("utf-8", errors="ignore")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        bak = path.with_name(path.name + ".modi-backup-" + stamp)
        shutil.copy2(path, bak)
        baks = sorted(path.parent.glob(path.name + ".modi-backup-*"))
        for old in baks[:-keep_backups]:
            try:
                old.unlink()
            except OSError:
                pass
    nl = "\r\n" if "\r\n" in existing else "\n"
    lines = existing.splitlines()
    done = set()
    out = []
    for line in lines:
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m and m.group(1) in updates and not line.lstrip().startswith("#"):
            if m.group(1) in done:
                continue                                 # drop duplicate definitions
            out.append(f"{m.group(1)}={updates[m.group(1)]}")
            done.add(m.group(1))
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in done:
            out.append(f"{k}={v}")
    tmp = path.with_name(path.name + ".modi-tmp")
    tmp.write_bytes((nl.join(out) + nl).encode("utf-8"))
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, path)


def yaml_lookup(text, wanted):
    """Tiny indentation-aware lookup of a scalar in a YAML text, e.g. ["gateway","api_server","key"].
    Good enough for Hermes' config.yaml (plain nested mappings); returns None when absent."""
    stack = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith(("#", "-")):
            continue
        m = re.match(r"^(\s*)([A-Za-z0-9_.\-]+)\s*:\s*(.*)$", raw)
        if not m:
            continue
        indent, key, rest = len(m.group(1).replace("\t", "    ")), m.group(2), m.group(3)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        if [k for _, k in stack] == wanted:
            vm = re.match(r"""^(?:"([^"]*)"|'([^']*)'|([^#]*?))\s*(?:#.*)?$""", rest)
            if vm:
                v = next((g for g in vm.groups() if g is not None), "")
                return v.strip()
    return None


def valid_key(k):
    k = (k or "").strip()
    return len(k) >= 8 and not k.lower().startswith(PLACEHOLDER_KEYS)


def redact(text, secrets_list=()):
    t = text or ""
    for s in secrets_list:
        if s and len(s) >= 6:
            t = t.replace(s, "***")
    t = re.sub(r"(?i)(bearer\s+)\S+", r"\1***", t)
    t = re.sub(r"\b(sk|xai|gsk|sk-ant|sk-or)[-_][A-Za-z0-9_\-]{12,}", "***", t)
    return t


# --------------------------------------------------------------------------- jobs
class Job:
    def __init__(self, kind, steps):
        self.id = secrets.token_hex(6)
        self.kind = kind
        self.status = "running"            # running | done | error | cancelled
        self.steps = [{"id": i, "label": l, "state": "pending"} for i, l in steps]
        self.log = deque(maxlen=40)
        self.link = ""
        self.code = ""
        self.awaiting_input = False
        self.error = ""
        self.error_code = ""
        self.result = {}
        self.progress = None               # 0..100 or None
        self.proc = None
        self.cancelled = False
        self.t0 = time.time()
        self._lock = threading.Lock()

    def step(self, sid, state, label=None):
        with self._lock:
            for s in self.steps:
                if s["id"] == sid:
                    s["state"] = state
                    if label:
                        s["label"] = label

    def add_log(self, line, secrets_list=()):
        line = redact(clean(line).rstrip(), secrets_list)
        if line.strip():
            with self._lock:
                self.log.append(line[:300])

    def public(self):
        with self._lock:
            return {"id": self.id, "kind": self.kind, "status": self.status, "steps": [dict(s) for s in self.steps],
                    "log": list(self.log)[-8:], "link": self.link, "code": self.code, "awaiting_input": self.awaiting_input,
                    "error": self.error, "error_code": self.error_code, "result": self.result, "progress": self.progress}


class SetupError(Exception):
    def __init__(self, msg, code="error"):
        super().__init__(msg)
        self.code = code


# --------------------------------------------------------------------------- manager
class HermesManager:
    def __init__(self, cfg, data_dir, say=print):
        self.cfg = cfg                       # the live CFG dict of bridge.py
        self.data = Path(data_dir)
        self.say = say
        self._cli = (0.0, None)
        self._jobs = {}
        self._cur = None
        self._lock = threading.Lock()
        self._st = (0.0, None)
        self.chat_state = ""                 # "", "no_provider": what the last real chat attempt learned
        self._probe = {"t": 0.0, "ok": None, "busy": False}   # background test message when config alone can't tell
        self.saved_key_file = self.data / "hermes_api_key"

    # ---- locations -------------------------------------------------------
    def home_candidates(self):
        c = []
        if os.getenv("HERMES_HOME"):
            c.append(Path(os.environ["HERMES_HOME"]))
        if IS_WIN and os.getenv("LOCALAPPDATA"):
            c.append(Path(os.environ["LOCALAPPDATA"]) / "hermes")
        c.append(Path.home() / ".hermes")
        return c

    def home(self):
        cands = self.home_candidates()
        for h in cands:
            if (h / "config.yaml").exists() or (h / ".env").exists() or (h / "auth.json").exists():
                return h
        for h in cands:
            if h.exists():
                return h
        return cands[0]

    def env_path(self):
        return self.home() / ".env"

    def extra_bin_dirs(self):
        d = []
        for h in self.home_candidates():
            d += [h / "bin", h / "hermes-agent" / ".hermes" / "bin", h / "hermes-agent" / "venv" / "Scripts", h / "hermes-agent" / "venv" / "bin"]
        d += [Path.home() / ".local" / "bin", Path.home() / ".hermes" / "bin"]
        if IS_WIN:
            la = os.environ.get("LOCALAPPDATA")
            if la:
                d.append(Path(la) / "Microsoft" / "WindowsApps")
            d += [Path(p) for p in self._windows_registry_path()]
        elif IS_MAC:
            d += [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
        else:
            d += [Path("/usr/local/bin")]
        return d

    @staticmethod
    def _windows_registry_path():
        """PATH as stored in the registry (a freshly installed Hermes is only there, not in this process' PATH)."""
        out = []
        try:
            import winreg  # type: ignore
            for hive, sub in ((winreg.HKEY_CURRENT_USER, "Environment"),
                              (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
                try:
                    with winreg.OpenKey(hive, sub) as k:
                        v, _ = winreg.QueryValueEx(k, "Path")
                        out += [os.path.expandvars(p) for p in str(v).split(";") if p.strip()]
                except OSError:
                    pass
        except ImportError:
            pass
        return out

    def find_cli(self, force=False):
        now = time.time()
        if not force and now - self._cli[0] < 15 and self._cli[1] is not None:
            return self._cli[1] or None
        name = (self.cfg["hermes"].get("cli_command") or "hermes").strip() or "hermes"
        found = ""
        if os.path.isabs(name) and os.path.exists(name):
            found = name
        else:
            search = os.pathsep.join([os.environ.get("PATH", "")] + [str(p) for p in self.extra_bin_dirs()])
            found = shutil.which(name, path=search) or ""
            if not found and IS_WIN:
                for p in self.extra_bin_dirs():
                    for ext in (".exe", ".cmd", ".bat"):
                        if (p / (name + ext)).exists():
                            found = str(p / (name + ext))
                            break
                    if found:
                        break
        self._cli = (now, found)
        return found or None

    def cli_env(self):
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join([os.environ.get("PATH", "")] + [str(p) for p in self.extra_bin_dirs() if p.exists()])
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["NO_COLOR"] = "1"
        if os.getenv("HERMES_HOME"):
            env["HERMES_HOME"] = os.environ["HERMES_HOME"]
        return env

    def run_cli(self, args, timeout=120, stdin_text=None):
        """Run `hermes <args>`; returns (returncode, combined output).  Never raises for a normal failure."""
        exe = self.find_cli()
        if not exe:
            return 127, "hermes was not found"
        try:
            p = subprocess.run([exe] + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, env=self.cli_env(), input=stdin_text if stdin_text is not None else "",
                               **_flags())
            return p.returncode, clean((p.stdout or "") + (p.stderr or ""))
        except subprocess.TimeoutExpired:
            return 124, "timed out after %ds" % timeout
        except OSError as e:
            return 126, str(e)

    # ---- keys ------------------------------------------------------------
    def live_key(self):
        """The key Hermes itself is configured with: (key, source) or ("", "")."""
        k = os.environ.get("API_SERVER_KEY", "").strip()
        if valid_key(k):
            return k, "environment"
        for h in self.home_candidates():
            k = read_env_file(h / ".env").get("API_SERVER_KEY", "").strip()
            if valid_key(k):
                return k, ".env"
            try:
                txt = (h / "config.yaml").read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for path in (["gateway", "api_server", "key"], ["api_server", "key"]):
                k = (yaml_lookup(txt, path) or "").strip()
                if valid_key(k):
                    return k, "config.yaml"
        return "", ""

    def saved_key(self):
        try:
            k = self.saved_key_file.read_text(encoding="utf-8").strip()
            return k if valid_key(k) else ""
        except OSError:
            return ""

    def resolve_api_key(self):
        """The key Hermes itself is configured with wins (it is the one the gateway checks, and it follows any rotation);
        then a key typed into MODI's config.json (e.g. Hermes on another machine); then MODI's saved copy.
        Set hermes.read_key_from_hermes_env=false to make the typed key win instead."""
        h = self.cfg["hermes"]
        typed = (h.get("api_key") or "").strip()
        if h.get("read_key_from_hermes_env", True):
            k, _ = self.live_key()
            if k:
                return k
        return typed or self.saved_key()

    def save_key_copy(self, key):
        try:
            self.data.mkdir(parents=True, exist_ok=True)
            tmp = self.saved_key_file.with_name(self.saved_key_file.name + ".tmp")
            tmp.write_text(key, encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.saved_key_file)
        except OSError:
            pass

    def adopt_key(self):
        """If Hermes has a key and MODI has no copy of it yet, remember it.  Silent; returns the source or ''."""
        k, src = self.live_key()
        if k and k != self.saved_key():
            self.save_key_copy(k)
            return src
        return ""

    def api_enabled(self):
        for h in self.home_candidates():
            v = read_env_file(h / ".env").get("API_SERVER_ENABLED", "")
            if v:
                return v.strip().lower() in ("1", "true", "yes", "on")
            try:
                txt = (h / "config.yaml").read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for path in (["gateway", "api_server", "enabled"], ["api_server", "enabled"]):
                v = yaml_lookup(txt, path)
                if v is not None:
                    return v.strip().lower() in ("1", "true", "yes", "on")
        v = os.environ.get("API_SERVER_ENABLED", "")
        return v.strip().lower() in ("1", "true", "yes", "on")

    def api_url(self):
        """Explicit api_url from MODI's config if it differs from the default, else follow Hermes' own port setting."""
        url = (self.cfg["hermes"].get("api_url") or "http://127.0.0.1:8642").rstrip("/")
        if url != "http://127.0.0.1:8642":
            return url
        port, host = None, None
        for h in self.home_candidates():
            e = read_env_file(h / ".env")
            port = port or e.get("API_SERVER_PORT")
            host = host or e.get("API_SERVER_HOST")
        port = port or os.environ.get("API_SERVER_PORT")
        if port and str(port).isdigit():
            return "http://127.0.0.1:%d" % int(port)
        return url

    # ---- probes ----------------------------------------------------------
    def _http(self, method, url, headers=None, body=None, timeout=3):
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.read()
            except Exception:  # noqa
                return e.code, b""
        except Exception:  # noqa
            return 0, b""

    def healthy(self):
        code, _ = self._http("GET", self.api_url() + "/health", timeout=2)
        return 0 < code < 500

    def auth_ok(self, key=None):
        """True / False (rejected) / None (unreachable)."""
        key = key or self.resolve_api_key()
        if not key:
            return False
        code, _ = self._http("GET", self.api_url() + "/v1/models", {"Authorization": "Bearer " + key}, timeout=4)
        if code == 0:
            return None
        return code == 200

    def model_config(self):
        """provider / default model as written in Hermes' config.yaml."""
        for h in self.home_candidates():
            try:
                txt = (h / "config.yaml").read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            prov = yaml_lookup(txt, ["model", "provider"])
            name = yaml_lookup(txt, ["model", "default"]) or yaml_lookup(txt, ["model", "model"])
            if prov or name:
                return (prov or "").strip(), (name or "").strip()
        return "", ""

    def provider_hint(self):
        """Does Hermes look like it has a way to reach an AI?  (Cheap guess; the test message is the real proof.)"""
        prov, name = self.model_config()
        home = self.home()
        creds = False
        try:
            a = json.loads((home / "auth.json").read_text(encoding="utf-8"))
            creds = bool(a) and bool(json.dumps(a).strip("{} \n"))
        except (OSError, ValueError):
            pass
        env = read_env_file(home / ".env")
        if any(re.search(r"(API_KEY|_TOKEN)$", k) and k != "API_SERVER_KEY" and v for k, v in env.items()):
            creds = True
        local = prov in ("custom", "ollama") and bool(name)
        return {"provider": prov, "model": name, "configured": bool((prov or name) and (creds or local)) or (creds and bool(name))}

    def _provider_works(self, hint):
        """True when Hermes looks signed in to an AI.  If its files don't say so, send one tiny test message in the
        background (never blocks the status call) - an unsigned Hermes answers instantly with an error."""
        if hint["configured"]:
            return True
        pr = self._probe
        age = time.time() - pr["t"]
        if not pr["busy"] and (pr["ok"] is None or age > (300 if pr["ok"] else 8)):
            pr["busy"] = True

            def run():
                try:
                    pr["ok"] = bool(self.test_chat()["ok"])
                finally:
                    pr["t"], pr["busy"] = time.time(), False
            threading.Thread(target=run, daemon=True, name="modi-ai-probe").start()
        return pr["ok"] is not False

    def provider_label(self):
        prov, name = self.model_config()
        lab = PROVIDER_LABELS.get(prov, prov.title() if prov else "")
        return lab, name

    def ollama(self):
        code, body = self._http("GET", OLLAMA_URL + "/api/tags", timeout=1.5)
        models = []
        if code == 200:
            try:
                models = sorted(m.get("name", "") for m in json.loads(body).get("models", []) if m.get("name"))
            except ValueError:
                pass
        exe = shutil.which("ollama", path=os.pathsep.join([os.environ.get("PATH", "")] + [
            "/usr/local/bin", "/opt/homebrew/bin", str(Path.home() / ".local" / "bin")] +
            ([os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama")] if IS_WIN else [])))
        return {"running": code == 200, "installed": bool(exe) or code == 200, "models": models}

    # ---- status ----------------------------------------------------------
    def status(self, fresh=False):
        now = time.time()
        if not fresh and self._st[1] is not None and now - self._st[0] < 2.5:
            return self._st[1]
        cli = self.find_cli(force=fresh)
        key, src = self.live_key()
        have_key = bool(key or self.cfg["hermes"].get("api_key") or self.saved_key())
        enabled = self.api_enabled()
        up = self.healthy()
        auth = self.auth_ok() if up else None
        hint = self.provider_hint()
        lab, model = self.provider_label()
        if not cli and not up:
            state = "needs_install"
        elif not have_key or (not enabled and not up):
            state = "needs_fix"
        elif not up:
            state = "needs_start"
        elif auth is False:
            state = "needs_fix"
        elif self.chat_state == "no_provider" or not self._provider_works(hint):
            state = "needs_ai"
        else:
            state = "ready"
        st = {"state": state, "platform": PLATFORM, "installed": bool(cli), "running": up, "key": have_key, "key_source": src,
              "api_enabled": enabled, "auth_ok": auth, "provider": lab, "model": model,
              "provider_id": hint["provider"], "ollama": self.ollama() if state in ("needs_ai", "ready") else None,
              "busy": bool(self._cur and self._jobs.get(self._cur) and self._jobs[self._cur].status == "running"),
              "job": self._cur}
        self._st = (now, st)
        return st

    def note_chat_ok(self):
        self.chat_state = ""
        self._probe.update(ok=True, t=time.time())

    def invalidate(self):
        self._st = (0.0, None)
        self._cli = (0.0, self._cli[1])

    # ---- jobs ------------------------------------------------------------
    def job(self, jid):
        j = self._jobs.get(jid)
        return j.public() if j else None

    def _start(self, kind, steps, fn):
        with self._lock:
            cur = self._jobs.get(self._cur) if self._cur else None
            if cur and cur.status == "running":
                return cur.public(), False
            j = Job(kind, steps)
            self._jobs[j.id] = j
            self._cur = j.id
            for old in list(self._jobs)[:-6]:
                self._jobs.pop(old, None)

        def run():
            try:
                fn(j)
                if j.status == "running":
                    j.status = "done"
            except SetupError as e:
                j.error, j.error_code, j.status = str(e), e.code, "error"
            except Exception as e:  # noqa
                j.error, j.error_code, j.status = "Unexpected problem: %s: %s" % (type(e).__name__, e), "unexpected", "error"
            if j.cancelled:
                j.status = "cancelled"
            for s in j.steps:
                if s["state"] == "run":
                    s["state"] = "fail" if j.status == "error" else "pending"
            self.invalidate()
        threading.Thread(target=run, daemon=True, name="modi-setup-" + kind).start()
        return j.public(), True

    def cancel(self, jid):
        j = self._jobs.get(jid)
        if not j or j.status != "running":
            return False
        j.cancelled = True
        p = j.proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass
        return True

    def send_input(self, jid, text):
        j = self._jobs.get(jid)
        if not j or j.status != "running" or not j.proc or j.proc.poll() is not None or not j.proc.stdin:
            return False
        try:
            j.proc.stdin.write((text.strip() + "\n"))
            j.proc.stdin.flush()
            j.awaiting_input = False
            return True
        except OSError:
            return False

    def _stream(self, job, cmd, timeout, secret_values=(), watch=None, env=None):
        """Run a command, copy its output into the job log line by line, return the exit code."""
        job.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    encoding="utf-8", errors="replace", bufsize=1, env=env or self.cli_env(), **_flags())
        deadline = time.time() + timeout

        def pump():
            for line in job.proc.stdout:
                line = clean(line)
                job.add_log(line, secret_values)
                if watch:
                    watch(line)
        t = threading.Thread(target=pump, daemon=True)
        t.start()
        while job.proc.poll() is None:
            if time.time() > deadline or job.cancelled:
                try:
                    job.proc.terminate()
                    time.sleep(1)
                    if job.proc.poll() is None:
                        job.proc.kill()
                except OSError:
                    pass
                break
            time.sleep(0.3)
        t.join(timeout=3)
        rc = job.proc.returncode
        job.proc = None
        return -1 if rc is None else rc

    # ---- 1. install Hermes -----------------------------------------------
    def start_install(self):
        return self._start("install", [("download", "Downloading the official Hermes installer"),
                                       ("install", "Installing Hermes (a few minutes)"),
                                       ("verify", "Checking the installation")], self._do_install)

    def _do_install(self, job):
        job.step("download", "run")
        tmpd = Path(tempfile.mkdtemp(prefix="modi-hermes-"))
        try:
            script = tmpd / ("install.ps1" if IS_WIN else "install.sh")
            url = os.environ.get("MODI_HERMES_INSTALL_URL") or (INSTALL_PS1 if IS_WIN else INSTALL_SH)   # env hook: tests only
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "MODI"}), timeout=60) as r:
                    data = r.read()
            except Exception as e:  # noqa
                raise SetupError("Could not download the Hermes installer from %s (%s). Check your internet connection and try again." % (url, type(e).__name__), "download")
            if len(data) < 500 or b"hermes" not in data.lower():
                raise SetupError("The Hermes installer download looks wrong. Try again in a minute.", "download")
            script.write_bytes(data)
            job.step("download", "ok")
            job.step("install", "run")
            if IS_WIN:
                ps = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
                cmd = [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), "-NonInteractive"]
            else:
                cmd = ["bash", str(script), "--non-interactive", "--skip-browser"]
            self.say("[hermes-setup] installing Hermes with the official installer")
            rc = self._stream(job, cmd, 1800)
            if job.cancelled:
                raise SetupError("Cancelled.", "cancelled")
            self.invalidate()
            exe = self.find_cli(force=True)
            if rc != 0 and not exe:
                tail = " ".join(list(job.log)[-3:])
                hint = ""
                if re.search(r"(?i)\bgit\b.*(not found|missing|install)", tail) and IS_MAC:
                    hint = " Install Apple's Command Line Tools first (open Terminal and run: xcode-select --install), then try again."
                raise SetupError("The Hermes installer stopped (exit code %s). %s%s" % (rc, tail[-220:], hint), "install")
            job.step("install", "ok")
            job.step("verify", "run")
            if not exe:
                raise SetupError("The installer finished but MODI still cannot find Hermes. Open a new MODI window or restart the computer, then press Fix it.", "not_found")
            job.step("verify", "ok")
            if rc != 0:
                job.result["warning"] = "Hermes installed, but its installer reported a problem with an optional part. That's OK for MODI."
            job.result["cli"] = "found"
        finally:
            shutil.rmtree(tmpd, ignore_errors=True)

    # ---- 2. fix: key + API server + gateway ------------------------------
    def start_fix(self):
        return self._start("fix", [("find", "Finding Hermes on this computer"), ("key", "Setting up the secure link to Hermes"),
                                   ("start", "Starting Hermes"), ("check", "Checking that Hermes answers")], self._do_fix)

    def new_key(self):
        return "modi-" + secrets.token_hex(24)

    def ensure_key_and_api(self):
        """Make sure Hermes has an API key and has the API server enabled.  Returns what changed (a set)."""
        changed = set()
        key, _ = self.live_key()
        upd = {}
        if not key:
            key = self.new_key()
            upd["API_SERVER_KEY"] = key
            changed.add("key")
        env = read_env_file(self.env_path())
        if not self.api_enabled() or env.get("API_SERVER_ENABLED", "").strip().lower() in ("0", "false", "no", "off"):
            upd["API_SERVER_ENABLED"] = "true"
            changed.add("enabled")
        if upd:
            set_env_values(self.env_path(), upd)
        self.save_key_copy(key)
        return changed

    def _gateway_up(self, wait):
        end = time.time() + wait
        while time.time() < end:
            if self.healthy():
                return True
            time.sleep(1)
        return self.healthy()

    def start_gateway(self, job=None, restart=False):
        """Start (or restart) Hermes' gateway so its API server is listening.  Returns True when /health answers."""
        exe = self.find_cli()
        if not exe:
            return False
        log = job.add_log if job else (lambda s: None)
        if restart and self.healthy():
            log("Restarting Hermes so it picks up the new settings...")
            self.run_cli(["gateway", "restart", "--detach", "--yes"], timeout=120)
            time.sleep(2)
            if self._gateway_up(45):
                return True
        if not self.healthy():
            log("Starting the Hermes gateway...")
            self.run_cli(["gateway", "ensure", "--json", "--timeout", "60"], timeout=120)
            if self._gateway_up(30):
                return True
            log("Trying the direct start...")
            try:
                logd = self.home() / "logs"
                logd.mkdir(parents=True, exist_ok=True)
                lf = open(logd / "modi-gateway.log", "ab")
                subprocess.Popen([exe, "gateway", "run"], stdin=subprocess.DEVNULL, stdout=lf, stderr=lf, env=self.cli_env(), **_flags(detached=True))
            except OSError as e:
                log("Could not start Hermes: %s" % e)
                return False
        return self._gateway_up(75)

    def _do_fix(self, job):
        job.step("find", "run")
        if not self.find_cli(force=True):
            job.step("find", "fail")
            raise SetupError("Hermes isn't installed on this computer yet.", "not_installed")
        job.step("find", "ok")
        job.step("key", "run")
        changed = self.ensure_key_and_api()
        job.add_log("Secure key: " + ("created and saved in Hermes' settings (existing settings were backed up first)" if "key" in changed else "found in Hermes' settings"))
        if "enabled" in changed:
            job.add_log("Turned on Hermes' local API server")
        job.step("key", "ok")
        job.step("start", "run")
        up = self.healthy()
        need_restart = bool(changed) and up
        if up and not need_restart and self.auth_ok() is False:
            need_restart = True                        # running with an older key than the one on disk
            job.add_log("Hermes is running with different settings; restarting it")
        if not up or need_restart:
            if not self.start_gateway(job, restart=need_restart):
                raise SetupError("Hermes did not start. Look at %s for details, or run  hermes doctor  in a terminal." % (self.home() / "logs"), "start")
        job.step("start", "ok")
        job.step("check", "run")
        ok = self.auth_ok()
        if not ok:
            raise SetupError("Hermes is running but doesn't accept MODI's key" if ok is False else "Hermes started but isn't answering yet. Wait a moment and press Fix it again.", "auth" if ok is False else "start")
        job.step("check", "ok")
        self.adopt_key()
        self.chat_state = ""

    # ---- 3. AI provider --------------------------------------------------
    def start_provider(self, body):
        pid = str(body.get("provider") or "")
        p = PROVIDERS.get(pid)
        if not p:
            raise SetupError("Unknown provider.", "bad_input")
        key = str(body.get("api_key") or "").strip()
        model = str(body.get("model") or "").strip()
        if model and not re.fullmatch(r"[A-Za-z0-9._:/\-]{1,100}", model):
            raise SetupError("That model name has characters Hermes does not accept.", "bad_input")
        if p["kind"] == "key":
            if not KEY_RE.fullmatch(key):
                raise SetupError("Paste the API key exactly as your provider shows it (no spaces).", "bad_input")
        if p["kind"] == "local" and not model:
            raise SetupError("Choose which local model to use.", "bad_input")
        steps = [("auth", "Signing in" if p["kind"] == "oauth" else "Saving your key" if p["kind"] == "key" else "Preparing the local model"),
                 ("config", "Telling Hermes to use " + p["label"]), ("test", "Sending a test message")]
        return self._start("provider", steps, lambda j: self._do_provider(j, pid, p, key, model))

    def _cfg_set(self, k, v):
        rc, out = self.run_cli(["config", "set", k, str(v)], timeout=90)
        if rc != 0:
            raise SetupError("Hermes would not save the setting %s: %s" % (k, out.strip()[-200:]), "config")

    def _do_provider(self, job, pid, p, key, model):
        if not self.find_cli():
            raise SetupError("Hermes isn't installed yet.", "not_installed")
        secret_vals = [key] if key else []
        job.step("auth", "run")
        use_model = model or p.get("model", "")
        if p["kind"] == "oauth":
            seen = {"url": False}

            def watch(line):
                m = URL_RE.search(line)
                if m and "localhost" not in m.group(0).split("?")[0] and not seen["url"]:
                    seen["url"] = True
                    job.link = m.group(0).rstrip(".,)")
                elif m and not job.link:
                    job.link = m.group(0).rstrip(".,)")
                c = USERCODE_RE.search(line)
                if c and not job.code and "code" in line.lower():
                    job.code = c.group(1)
                if re.search(r"(?i)authorization code|paste it", line):
                    job.awaiting_input = True
            exe = self.find_cli()
            rc = self._stream(job, [exe] + p["auth"], 660, watch=watch)
            if rc != 0 and not job.cancelled and p.get("auth_fallback") and not job.link and not job.code:
                job.add_log("Trying the other sign-in method...")
                rc = self._stream(job, [exe] + p["auth_fallback"], 660, watch=watch)
            job.awaiting_input = False
            if job.cancelled:
                raise SetupError("Sign-in cancelled.", "cancelled")
            if rc != 0:
                raise SetupError("The sign-in did not finish. %s" % (" ".join(list(job.log)[-2:])[-200:] or "Try again."), "auth")
        elif p["kind"] == "key":
            set_env_values(self.env_path(), {p["env"]: key})
        else:                                             # local model through Ollama
            self._prepare_ollama(job, model)
            use_model = job.result.get("model", model)
        job.link, job.code = "", ""
        job.step("auth", "ok")
        job.step("config", "run")
        if p["kind"] != "local":                          # a leftover local endpoint would silently swallow the requests
            for stale in ("model.base_url", "model.api_mode", "model.context_length"):
                self.run_cli(["config", "unset", stale], timeout=60)
        self._cfg_set("model.provider", p["hermes"])
        if p["kind"] == "local":
            self._cfg_set("model.base_url", OLLAMA_URL + "/v1")
            self._cfg_set("model.context_length", 64000)
        if use_model:
            self._cfg_set("model.default", use_model)
        else:
            self.run_cli(["config", "unset", "model.default"], timeout=60)
        job.step("config", "ok")
        # a running gateway may keep the old provider; restart it quietly so the change is live
        self.invalidate()
        if self.healthy():
            self.start_gateway(job, restart=True)
        job.step("test", "run")
        res = self.test_chat()
        job.result["test"] = res
        if not res["ok"]:
            self.chat_state = "no_provider"              # keep the wizard on the AI step until a test message works
            self._probe.update(ok=False, t=time.time())
            raise SetupError(res["error"], res.get("kind", "test"))
        lab, mdl = self.provider_label()
        job.result["connected"] = lab or p["label"]
        job.result["model"] = mdl
        job.step("test", "ok")
        self.note_chat_ok()

    def _prepare_ollama(self, job, model):
        """Make sure Ollama is up, the model is there, and a 64k-context copy exists (Hermes needs >= 64k)."""
        o = self.ollama()
        if not o["running"]:
            exe = shutil.which("ollama")
            if not exe:
                raise SetupError("Ollama isn't installed. Get it from https://ollama.com/download, then come back and press Connect.", "no_ollama")
            job.add_log("Starting Ollama...")
            try:
                subprocess.Popen([exe, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self.cli_env(), **_flags(detached=True))
            except OSError as e:
                raise SetupError("Could not start Ollama: %s" % e, "no_ollama")
            for _ in range(20):
                if self.ollama()["running"]:
                    break
                time.sleep(1)
            o = self.ollama()
            if not o["running"]:
                raise SetupError("Ollama did not start.", "no_ollama")
        if model not in o["models"] and (model + ":latest") not in o["models"]:
            self._ollama_pull(job, model)
        big = model if model.endswith("-modi") else re.sub(r"[:/]", "-", model) + "-modi"
        job.add_log("Preparing %s with a larger memory window (Hermes needs 64k)..." % model)
        body = json.dumps({"model": big, "from": model, "parameters": {"num_ctx": 65536}, "stream": False}).encode()
        code, out = self._http("POST", OLLAMA_URL + "/api/create", {"Content-Type": "application/json"}, body, timeout=180)
        if code == 200:
            job.result["model"] = big
        else:
            job.add_log("Could not make the larger copy - using %s as it is" % model)
            job.result["model"] = model
            job.result["warning"] = "The model may be limited to a small context window; if Hermes complains, set OLLAMA_CONTEXT_LENGTH=64000 and restart Ollama."

    def _ollama_pull(self, job, model):
        job.add_log("Downloading %s (this can take several minutes)..." % model)
        req = urllib.request.Request(OLLAMA_URL + "/api/pull", data=json.dumps({"model": model, "stream": True}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=3600) as r:
                for raw in r:
                    if job.cancelled:
                        raise SetupError("Cancelled.", "cancelled")
                    try:
                        d = json.loads(raw)
                    except ValueError:
                        continue
                    if d.get("error"):
                        raise SetupError("Ollama could not download %s: %s" % (model, d["error"]), "pull")
                    if d.get("total") and d.get("completed") is not None:
                        job.progress = int(100 * d["completed"] / d["total"])
                    job.add_log(d.get("status", ""))
        except urllib.error.URLError as e:
            raise SetupError("Could not reach Ollama (%s)." % e, "no_ollama")
        job.progress = None

    def start_pull(self, model):
        if not re.fullmatch(r"[A-Za-z0-9._:/\-]{1,100}", model or ""):
            raise SetupError("That model name is not valid.", "bad_input")
        return self._start("pull", [("pull", "Downloading " + model)], lambda j: (j.step("pull", "run"), self._ollama_pull(j, model), j.step("pull", "ok")))

    # ---- 4. test message ---------------------------------------------------
    def test_chat(self, text="Reply with the single word: ready"):
        key = self.resolve_api_key()
        if not key:
            return {"ok": False, "kind": "no_key", "error": "MODI has no key for Hermes yet."}
        body = json.dumps({"model": self.cfg["hermes"].get("model", "hermes-agent"), "stream": False,
                           "messages": [{"role": "user", "content": text}]}).encode()
        code, out = self._http("POST", self.api_url() + "/v1/chat/completions",
                               {"Authorization": "Bearer " + key, "Content-Type": "application/json", "X-Hermes-Session-Id": "modi-setup-test"},
                               body, timeout=150)
        return interpret_chat(code, out, key)


def interpret_chat(code, out, key=""):
    """Turn a chat/completions response into {ok, reply} or {ok False, kind, error}."""
    if code == 0:
        return {"ok": False, "kind": "unreachable", "error": "Hermes isn't answering."}
    if code == 401:
        return {"ok": False, "kind": "auth", "error": "Hermes rejected MODI's key."}
    try:
        d = json.loads(out)
    except ValueError:
        return {"ok": False, "kind": "bad", "error": "Hermes answered with something MODI did not understand (HTTP %s)." % code}
    h = d.get("hermes") or {}
    ch = (d.get("choices") or [{}])[0]
    if code >= 400 or h.get("failed") or ch.get("finish_reason") == "error":
        err = d.get("error")
        msg = h.get("error") or (err.get("message") if isinstance(err, dict) else err) or ""
        msg = str(msg)
        low = msg.lower()
        if "not connected to any ai provider" in low or "no provider" in low or "/login" in low:
            return {"ok": False, "kind": "no_provider", "error": "Hermes isn't signed in to an AI yet."}
        if re.search(r"(?i)\b(401|403)\b|invalid.{0,20}(key|token)|unauthori[sz]ed|authentication|incorrect api key", msg):
            return {"ok": False, "kind": "provider_auth", "error": "The AI service did not accept that sign-in or key. Check it and try again. (" + redact(msg, [key])[:120] + ")"}
        return {"ok": False, "kind": "provider", "error": redact(msg or "The AI did not answer.", [key])[:300]}
    reply = ((ch.get("message") or {}).get("content") or "")
    if isinstance(reply, list):
        reply = "".join(p.get("text", "") for p in reply if isinstance(p, dict))
    return {"ok": True, "reply": reply.strip()[:300]}
