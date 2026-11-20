"""Dev-only: the Windows launcher (src/win/ATLAS.ps1) must not reuse an OLDER bridge that is already on its port (e.g. MODI 1.1.x,  # legacy-modi
whose HUD shows "Can't reach the Hermes API at ... ClientConnectorError").  Runs ATLAS.ps1 -Mode Bridge under pwsh with fake bridges
in a temp folder (fake HOME, nothing real touched).  Needs pwsh (PWSH env or /home/box/pwsh/pwsh); skipped if missing.
   python3 bridge/dev/test_launcher_version.py"""
import json, os, shutil, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
PWSH = os.environ.get("PWSH") or shutil.which("pwsh") or "/home/box/pwsh/pwsh"
fails = []
def check(n, ok, x=""):
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else " :: %s" % (x,)))
    if not ok: fails.append(n)
if not os.path.exists(PWSH):
    print("SKIP: no pwsh"); sys.exit(0)
PORT = 18799
FAKE = r'''
import json, sys, os
from http.server import BaseHTTPRequestHandler, HTTPServer
V = sys.argv[1]
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        b = json.dumps({"ok": True, "bridge": True, "version": V}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"{}"); self.wfile.flush()
        if self.path == "/api/quit": os._exit(0)
HTTPServer(("127.0.0.1", int(os.environ["JARVIS_PORT"])), H).serve_forever()
'''
t = Path(tempfile.mkdtemp(prefix="atlas-launcher-"))
app = t / "app"; (app / "win").mkdir(parents=True); (app / "bridge").mkdir()
shutil.copy(ROOT / "src" / "win" / "ATLAS.ps1", app / "win" / "ATLAS.ps1")
(app / "bridge" / "bridge.py").write_text(FAKE.replace('V = sys.argv[1]', 'V = "1.4.1"'))
(app / "bridge" / "atlas_meta.py").write_text('VERSION = "1.4.1"\n')
old = t / "old_bridge.py"; old.write_text(FAKE)
env = dict(os.environ, HOME=str(t / "home"), JARVIS_PORT=str(PORT), ATLAS_HOME=str(t / "data"), ATLAS_PY=sys.executable, LOCALAPPDATA=str(t / "lad"))
def ver():
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % PORT, timeout=2) as f: return json.loads(f.read())["version"]
    except Exception: return None
def run():
    return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(app / "win" / "ATLAS.ps1"), "-Mode", "Bridge"], env=env, capture_output=True, text=True, timeout=120)
procs = []
try:
    procs.append(subprocess.Popen([sys.executable, str(old), "1.1.0"], env=env)); time.sleep(1.5)
    check("(an old 1.1.0 bridge is answering on the port)", ver() == "1.1.0")
    r = run(); time.sleep(1)
    started = ver() == "1.4.1"
    if not started and os.name != "nt":          # pwsh on Linux has no Start-Process -WindowStyle, so the new bridge can't be started here;
        check("launcher stops the old bridge (starting the new one is Windows-only)", ver() is None and procs[0].poll() is not None, (ver(), r.stderr[-300:]))
        procs.append(subprocess.Popen([sys.executable, str(app / "bridge" / "bridge.py")], env=env)); time.sleep(1.5)
    else:
        check("launcher stops the old bridge and starts the installed one", started, (ver(), r.stdout[-300:], r.stderr[-300:]))
    check("...and says why", "different ATLAS bridge (version 1.1.0)" in r.stdout, r.stdout[-300:])
    r = run()
    check("a bridge of the right version is reused, not restarted", ver() == "1.4.1" and "different" not in r.stdout, r.stdout[-300:])
finally:
    try: urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d/api/quit" % PORT, method="POST"), timeout=2)
    except Exception: pass
    for p in procs: p.kill()
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
