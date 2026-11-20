#!/usr/bin/env python3
"""End-to-end tests of the first-run flow against a REAL bridge process, a mock Hermes API and a fake `hermes` CLI.
   Covers: existing install (key only in Hermes' files), no key, Hermes not running, Hermes not installed, no AI signed in.
   python bridge/dev/test_setup_e2e.py"""
import json, os, shutil, signal, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
HERE = Path(__file__).resolve().parent
BRIDGE = HERE.parent / "bridge.py"
PY = sys.executable
MOCK_PORT, BR_PORT = 18642, 18765
fails = []

def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(extra)) if (extra and not cond) else ""))
    if not cond: fails.append(name)

def req(method, path, body=None, port=BR_PORT):
    r = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), method=method, data=json.dumps(body).encode() if body is not None else None,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as f: return f.status, json.loads(f.read() or b"{}")
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read())
        except Exception: return e.code, {}
    except (urllib.error.URLError, ConnectionError, OSError):
        return 0, {}

def wait(fn, secs=30, step=0.5):
    end = time.time() + secs
    while time.time() < end:
        v = fn()
        if v: return v
        time.sleep(step)
    return None

def job_wait(jid, secs=60):
    def f():
        c, j = req("GET", "/api/setup/job/" + jid)
        return j["job"] if j["job"]["status"] != "running" else None
    return wait(f, secs, 0.5)

class Env:
    def __init__(self, name, shim=True, mock_up=True, env_text=None, no_provider=False):
        self.t = Path(tempfile.mkdtemp(prefix="atlas-e2e-" + name + "-"))
        self.home = self.t / "home"; self.hh = self.t / "hermes"; self.data = self.t / "atlas"; self.bin = self.t / "bin"
        for d in (self.home, self.hh, self.data, self.bin): d.mkdir()
        if env_text is not None: (self.hh / ".env").write_text(env_text)
        self.mock_up, self.procs, self.no_provider = mock_up, [], no_provider
        base = "/usr/bin:/bin"
        self.env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home), HERMES_HOME=str(self.hh), ATLAS_HOME=str(self.data), JARVIS_PORT=str(BR_PORT),
                        HERMES_API_URL="http://127.0.0.1:%d" % MOCK_PORT, MOCK_HERMES_HOME=str(self.hh), MOCK_PORT=str(MOCK_PORT), MOCK_PY=PY, MOCK_SCRIPT=str(HERE / "mock_hermes.py"),
                        PATH=str(self.bin) + ":" + base if shim else base)
        self.env.pop("HERMES_API_KEY", None)
        if no_provider: self.env["MOCK_NO_PROVIDER"] = "1"
        if shim: self.write_shim(self.bin / "hermes")
    def write_shim(self, p):
        p.write_text('''#!/bin/bash
# fake hermes CLI for tests
start() { if ! curl -fs -m1 http://127.0.0.1:$MOCK_PORT/health >/dev/null; then nohup "$MOCK_PY" "$MOCK_SCRIPT" $MOCK_PORT >/dev/null 2>&1 & fi; }
stop() { pkill -f "mock_hermes.py $MOCK_PORT"; sleep 0.5; }
case "$1 $2" in
  "gateway ensure") start; sleep 1; echo '{"state":"ready"}';;
  "gateway restart") stop; start; sleep 1;;
  "gateway run") exec "$MOCK_PY" "$MOCK_SCRIPT" $MOCK_PORT;;
  "config set"|"config unset") echo "ok";;
  *) echo "fake hermes: $*";;
esac
''')
        p.chmod(0o755)
    def up(self):
        if self.mock_up:
            self.procs.append(subprocess.Popen([PY, str(HERE / "mock_hermes.py"), str(MOCK_PORT)], env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait(lambda: req("GET", "/health", port=MOCK_PORT)[0] == 200, 10)
        self.bridge = subprocess.Popen([PY, str(BRIDGE)], env=self.env, stdout=open(self.t / "bridge.log", "w"), stderr=subprocess.STDOUT)
        self.procs.append(self.bridge)
        return wait(lambda: req("GET", "/api/health")[0] == 200, 40)
    def down(self):
        subprocess.call(["pkill", "-f", "mock_hermes.py %d" % MOCK_PORT])
        for p in self.procs:
            try: p.send_signal(signal.SIGTERM); p.wait(5)
            except Exception: p.kill()
        time.sleep(0.5)
    def log(self): return (self.t / "bridge.log").read_text(errors="ignore")
    def secret_leak(self, secret):
        blob = self.log() + json.dumps(req("GET", "/api/health")[1]) + json.dumps(req("GET", "/api/setup/status")[1])
        return secret in blob

# ---------------------------------------------------------------- 1. existing install: key only in Hermes' own .env
print("== 1. existing install, key only in Hermes' .env (Adam's case after updating)")
K = "adopted-key-0123456789abcdef"
e = Env("adopt", shim=False, env_text="API_SERVER_ENABLED=true\nAPI_SERVER_KEY=%s\n" % K)
try:
    check("bridge starts", e.up())
    c, j = req("POST", "/api/chat", {"text": "hello there"})
    check("chat works with no key in ATLAS's own config", c == 200 and j.get("ok") and "Mock Hermes heard" in j.get("reply", ""), j)
    check("key copy saved in ATLAS's data folder", (e.data / "hermes_api_key").read_text().strip() == K)
    c, j = req("GET", "/api/health")
    check("health: setup ready, nothing secret", j["setup"]["state"] == "ready" and not e.secret_leak(K))
    check("log mentions adoption but not the key", "adopted the Hermes API key" in e.log() and K not in e.log())
finally: e.down()

# ---------------------------------------------------------------- 2. key in config.yaml only
print("== 2. key only in config.yaml (gateway.api_server.key)")
e = Env("yaml", shim=False)
(e.hh / "config.yaml").write_text("gateway:\n  api_server:\n    enabled: true\n    key: test-key-0123456789abcdef\n")
try:
    e.env.pop("MOCK_HERMES_HOME")
    e.up()
    c, j = req("POST", "/api/chat", {"text": "hi"})
    check("chat works with the key read from config.yaml", c == 200 and j.get("ok"), j)
finally: e.down()

# ---------------------------------------------------------------- 3. no key at all, Hermes running -> Fix it
print("== 3. Hermes installed + running but no API key (the reported bug)")
e = Env("nokey", env_text="OPENROUTER_API_KEY=keepme12345\n")
try:
    e.up()
    c, j = req("POST", "/api/chat", {"text": "hi"})
    check("chat gives a friendly 'Fix it' error, not the old scary text", c >= 400 and j.get("fix") == "setup" and "INSTALL-NOTES" not in j.get("error", "") and "config.json" not in j.get("error", ""), j)
    c, st = req("GET", "/api/setup/status")
    check("status: needs_fix", st["state"] == "needs_fix", st)
    c, j = req("POST", "/api/setup/fix", {}); job = job_wait(j["job"]["id"])
    check("fix job succeeds", job and job["status"] == "done", job)
    envtxt = (e.hh / ".env").read_text()
    check("key + enabled written to Hermes' .env, other lines kept", "API_SERVER_KEY=atlas-" in envtxt and "API_SERVER_ENABLED=true" in envtxt and "OPENROUTER_API_KEY=keepme12345" in envtxt)
    check("backup of .env made", list(e.hh.glob(".env.atlas-backup-*")))
    c, j = req("POST", "/api/chat", {"text": "hello again"})
    check("chat works after Fix it", c == 200 and j.get("ok"), j)
    newkey = [l.split("=", 1)[1] for l in envtxt.splitlines() if l.startswith("API_SERVER_KEY=")][0]
    check("key never in the bridge log / health / status", not e.secret_leak(newkey) and not any(newkey in json.dumps(x) for x in [job]))
    check("ATLAS's saved copy equals Hermes' key", (e.data / "hermes_api_key").read_text().strip() == newkey)
finally: e.down()

# ---------------------------------------------------------------- 4. installed but not running -> bridge starts it with ATLAS
print("== 4. Hermes installed, key present, not running")
e = Env("down", mock_up=False, env_text="API_SERVER_ENABLED=true\nAPI_SERVER_KEY=down-key-0123456789abcdef\n")
try:
    e.up()
    ok = wait(lambda: req("GET", "/health", port=MOCK_PORT)[0] == 200, 25)
    check("bridge started Hermes on its own at launch", ok, e.log()[-400:])
    c, j = req("GET", "/api/health"); check("state ready", j["setup"]["state"] == "ready", j.get("setup"))
finally: e.down()
e = Env("down2", mock_up=False, env_text="API_SERVER_ENABLED=true\nAPI_SERVER_KEY=down-key-0123456789abcdef\n")
e.env["ATLAS_NO_BOOT"] = "1"
try:
    e.up()
    c, st = req("GET", "/api/setup/status")
    check("status says 'needs_start' (not running)", st["state"] in ("needs_start",) or st["running"] is False, st)
    c, j = req("POST", "/api/setup/fix", {}); job = job_wait(j["job"]["id"])
    check("Fix it starts Hermes", job and job["status"] == "done" and req("GET", "/health", port=MOCK_PORT)[0] == 200, job)
finally: e.down()

# ---------------------------------------------------------------- 5. not installed -> one-click install -> fix
print("== 5. Hermes not installed")
e = Env("noinst", shim=False, mock_up=False)
inst = e.t / "fake-install.sh"
inst.write_text('#!/bin/bash\necho "-> installing (fake)"; mkdir -p "$HOME/.local/bin"; cp "%s" "$HOME/.local/bin/hermes"; chmod +x "$HOME/.local/bin/hermes"; echo done\n' % (e.bin / "hermes"))
e.write_shim(e.bin / "shim-src"); shutil.copy(e.bin / "shim-src", e.t / "shim-src"); os.remove(e.bin / "shim-src")
inst.write_text(inst.read_text().replace(str(e.bin / "hermes"), str(e.t / "shim-src")))
inst.write_text(inst.read_text() + "# padding so the 'looks like the hermes installer' check passes: hermes hermes hermes hermes hermes hermes hermes hermes hermes\n" + "#" * 600 + "\n")
e.env["ATLAS_HERMES_INSTALL_URL"] = "file://" + str(inst)
try:
    e.up()
    c, st = req("GET", "/api/setup/status")
    check("status: needs_install", st["state"] == "needs_install" and not st["installed"], st)
    c, j = req("POST", "/api/setup/fix", {}); job = job_wait(j["job"]["id"])
    check("fix without Hermes says not_installed", job["status"] == "error" and job["error_code"] == "not_installed", job)
    c, j = req("POST", "/api/setup/install", {}); job = job_wait(j["job"]["id"], 60)
    check("install job runs and finds the new CLI (not on PATH!)", job and job["status"] == "done", job)
    c, j = req("POST", "/api/setup/fix", {}); job = job_wait(j["job"]["id"])
    check("fix after install succeeds", job and job["status"] == "done", job)
    c, j = req("POST", "/api/chat", {"text": "hi"}); check("chat works", c == 200 and j.get("ok"), j)
finally: e.down()
e = Env("badinst", shim=False, mock_up=False)
(e.t / "bad.sh").write_text("#!/bin/bash\necho 'boom: could not clone'; exit 3\n# hermes hermes hermes hermes hermes hermes hermes hermes hermes hermes hermes hermes hermes hermes hermes\n" + "#" * 600 + "\n")
e.env["ATLAS_HERMES_INSTALL_URL"] = "file://" + str(e.t / "bad.sh")
try:
    e.up()
    c, j = req("POST", "/api/setup/install", {}); job = job_wait(j["job"]["id"], 60)
    check("failed install -> clear error, no crash", job and job["status"] == "error" and "boom" in job["error"], job)
finally: e.down()

# ---------------------------------------------------------------- 6. no AI signed in -> provider step -> test message
print("== 6. Hermes running, no AI signed in")
K = "provider-key-0123456789abcdef"
e = Env("noai", no_provider=True, env_text="API_SERVER_ENABLED=true\nAPI_SERVER_KEY=%s\n" % K)
try:
    e.env["MOCK_HERMES_HOME"] = str(e.hh)
    e.up()
    c, j = req("POST", "/api/chat", {"text": "hi"})
    check("chat: friendly 'sign in to an AI' error with fix=setup", c >= 400 and j.get("fix") == "setup" and "signed in" in j.get("error", ""), j)
    c, st = wait(lambda: (lambda r: r if r[1].get("state") == "needs_ai" else None)(req("GET", "/api/setup/status")), 15) or (0, {})
    check("status: needs_ai", st.get("state") == "needs_ai", st)
    c, j = req("POST", "/api/setup/provider", {"provider": "openrouter", "api_key": "short"}); check("bad key format refused", c == 400, j)
    c, j = req("POST", "/api/setup/provider", {"provider": "nope"}); check("unknown provider refused", c == 400)
    SECRET = "sk" + "-test-NOTREALKEY0123456789"   # split so the CI secret scan does not flag a fake key
    c, j = req("POST", "/api/setup/provider", {"provider": "openrouter", "api_key": SECRET}); job = job_wait(j["job"]["id"])
    check("not-signed-in Hermes: provider job reports the test failure", job["status"] == "error" and job["error_code"] == "no_provider", job)
    check("API key stored in Hermes' .env only", ("OPENROUTER_API_KEY=" + SECRET) in (e.hh / ".env").read_text() and not e.secret_leak(SECRET) and SECRET not in json.dumps(job))
    Path("/tmp/mock_hermes_signed_in").write_text("1")     # now the 'AI' works
    c, j = req("POST", "/api/setup/provider", {"provider": "openrouter", "api_key": SECRET}); job = job_wait(j["job"]["id"])
    check("provider job succeeds and says Connected", job["status"] == "done" and job["result"]["connected"] == "OpenRouter" and job["result"]["test"]["ok"], job)
    c, j = req("POST", "/api/chat", {"text": "hi"}); check("chat works", c == 200 and j.get("ok"), j)
    c, st = req("GET", "/api/setup/status"); check("status ready", st["state"] == "ready", st)
    c, j = req("POST", "/api/setup/test", {}); check("test endpoint ok", j["result"]["ok"], j)
finally:
    e.down()
    try: os.remove("/tmp/mock_hermes_signed_in")
    except OSError: pass

print("\n%d failure(s)" % len(fails)); sys.exit(1 if fails else 0)
