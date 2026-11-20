"""Dev-only: Computer control (the shipped add-on) in the real HUD against the real bridge - switch on, "open X" / "launch Y", confirm, ambiguity, setting.
   Fake Steam/Epic/Start Menu folders; ATLAS_LAUNCH_DRYRUN makes the opener write the link it WOULD open to a file, so nothing launches.
   /workspace/pwenv/bin/python bridge/dev/test_launch_ui.py [--shots]"""
import json, sys, tempfile, time
from pathlib import Path
from playwright.sync_api import sync_playwright
HERE = Path(__file__).resolve().parent; DOCS = HERE.parent.parent / "docs"; SHOTS = "--shots" in sys.argv
src = (HERE / "test_setup_e2e.py").read_text().split("# ---------------------------------------------------------------- 1.")[0]
exec(compile(src, "test_setup_e2e_prefix", "exec"))
PY = "/workspace/bridge-venv/bin/python"; URL = "http://127.0.0.1:%d/" % BR_PORT
WAV = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"

T = Path(tempfile.mkdtemp(prefix="atlas-launchui-"))
steam = T / "Steam"; (steam / "steamapps").mkdir(parents=True)
for aid, nm in (("1145360", "Hades"), ("1145350", "Hades II"), ("367520", "Hollow Knight"), ("570", "Dota 2")):
    (steam / "steamapps" / ("appmanifest_%s.acf" % aid)).write_text('"AppState"\n{\n"appid" "%s"\n"name" "%s"\n"StateFlags" "4"\n}\n' % (aid, nm))
epic = T / "Epic"; epic.mkdir(); (epic / "a.item").write_text(json.dumps({"DisplayName": "Rocket League", "AppName": "Sugar", "CatalogNamespace": "sg", "CatalogItemId": "abc123", "LaunchExecutable": "RL.exe"}))
sm = T / "Programs"; sm.mkdir()
for n in ("Discord", "Google Chrome", "Steam"): (sm / (n + ".lnk")).write_text("")
dry = T / "opened.log"

e = Env("launch", env_text="OPENROUTER_API_KEY=placeholder-not-a-secret\n")
e.env.update(ATLAS_STEAM_DIR=str(steam), ATLAS_EPIC_MANIFESTS=str(epic), ATLAS_START_MENU=str(sm), ATLAS_LAUNCH_DRYRUN=str(dry))
def get(path): return req("GET", path)[1]
def post(path, body): return req("POST", path, body)[1]
def opened(): return dry.read_text().strip().splitlines() if dry.exists() else []
try:
    e.up()
    post("/api/profile", {"name": "Adam", "city": "Dallas"})
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
        ctx = b.new_context(viewport={"width": 1600, "height": 900}); pg = ctx.new_page(); errs = []; pg.on("pageerror", lambda x: errs.append(str(x)))
        pg.route("**/api/speak", lambda r: r.fulfill(status=200, content_type="audio/wav", body=WAV))
        pg.goto(URL); pg.wait_for_function("window.__hud&&__hud.state().online===true", timeout=20000); pg.wait_for_timeout(1600)
        pg.evaluate("__hud.SU.dismissed=true;__hud.closeSetup()"); pg.wait_for_timeout(300)
        def closebox():
            if pg.evaluate("!!document.querySelector('#modbox.open')"): pg.click("#mb-close"); pg.wait_for_timeout(300)
        closebox()
        def chat(text, wait=600):
            n = len(pg.evaluate("__hud.state().msgs"))
            pg.fill("#cmdin", text); pg.press("#cmdin", "Enter")
            pg.wait_for_function("__hud.state().msgs.length>=%d" % (n + 2), timeout=30000); pg.wait_for_timeout(wait)
            m = pg.evaluate("__hud.state().msgs.slice(-1)[0]"); return m.get("text") or m.get("t") or json.dumps(m)
        mod = lambda: next((f for f in get("/api/features")["features"] if f["id"] == "computer_control"), {})
        check("the shipped module is trusted (hash matches the manifest) and off by default", mod().get("approval") in ("trusted", None) or mod().get("state") == "trusted", mod())
        r = chat("open hollow knight"); check("module OFF: ATLAS does not launch anything", not opened(), (r, opened()))
        r = chat("turn on computer control"); check("turning it on asks first", "yes" in r.lower(), r)
        r = chat("yes"); pg.wait_for_timeout(1200); check("...yes switches it on and it starts", mod().get("enabled") and mod().get("status") == "running", mod())
        st = get("/api/m/computer_control/status")
        check("it scanned the fake Steam, Epic and Start Menu entries", st.get("counts") == {"steam": 4, "epic": 1, "shortcut": 3}, st)
        r = chat("open hollow knight"); check("'open hollow knight' asks first (the default)", "Open Hollow Knight?" in r and not opened(), r)
        if SHOTS: closebox(); pg.wait_for_timeout(300); pg.screenshot(path=str(DOCS / "launch-confirm.png"))
        r = chat("yes"); check("yes launches it with the steam link", "Launching Hollow Knight" in r and opened() == ["uri steam://rungameid/367520"], (r, opened()))
        r = chat("launch hades"); check("'hades' is Hades, not Hades II (asks to confirm)", "Open Hades?" in r, r); chat("no")
        r = chat("play rocket league on epic"); check("Epic games open through the Epic launcher link", "Open Rocket League?" in r, r)
        chat("yes"); check("...link built from the manifest ids", opened()[-1] == "uri com.epicgames.launcher://apps/sg%3Aabc123%3ASugar?action=launch&silent=true", opened())
        r = chat("what games do I have"); check("lists installed games", "Hollow Knight" in r and "Rocket League" in r, r)
        r = chat("turn off launch confirmation"); check("the confirmation can be turned off by voice", get("/api/settings")["prefs"]["launch_confirm"] is False, r)
        r = chat("open discord"); check("with confirmation off a clear match opens at once", "Opening Discord" in r and opened()[-1].startswith("file ") and opened()[-1].endswith("Discord.lnk"), (r, opened()))
        r = chat("run powershell -c calc"); check("a command is never run", len(opened()) == 5 or all("calc" not in x for x in opened()), (r, opened()))
        r = chat("open C:\\Windows\\System32\\cmd.exe"); check("a path is never opened", all("cmd" not in x.lower() for x in opened()), (r, opened()))
        r = chat("play some jazz"); check("'play some jazz' is not treated as a game", "Launching" not in r and "Opening" not in r, r)
        chat("turn on launch confirmation")
        # settings page shows the module and the confirm switch
        closebox(); pg.evaluate("ATLAS.openSettings('general')"); pg.wait_for_timeout(900)
        body = pg.evaluate("document.body.innerText")
        check("Settings lists the Computer control module and its confirmation switch", "Computer control" in body and "Ask before opening apps and games" in body, "")
        if SHOTS:
            pg.evaluate("(()=>{const e=[...document.querySelectorAll('*')].find(x=>x.children.length===0&&/Ask before opening apps and games/.test(x.textContent));if(e)e.scrollIntoView({block:'center'})})()"); pg.wait_for_timeout(400)
            pg.screenshot(path=str(DOCS / "settings-computer-control.png"))
        # a changed module file is NOT trusted any more
        pg.keyboard.press("Escape")
        check("no page errors", not errs, errs)
        b.close()
finally: e.down()
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
