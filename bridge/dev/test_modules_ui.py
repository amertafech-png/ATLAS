"""Dev-only: add-on module approval in the real HUD against the real bridge (mock Hermes as the 'user's AI').
   /workspace/pwenv/bin/python bridge/dev/test_modules_ui.py [--shots]"""
import json, sys, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
HERE = Path(__file__).resolve().parent; DOCS = HERE.parent.parent / "docs"; SHOTS = "--shots" in sys.argv
src = (HERE / "test_setup_e2e.py").read_text().split("# ---------------------------------------------------------------- 1.")[0]
exec(compile(src, "test_setup_e2e_prefix", "exec"))
PY = "/workspace/bridge-venv/bin/python"; URL = "http://127.0.0.1:%d/" % BR_PORT
e = Env("modsui", env_text="OPENROUTER_API_KEY=placeholder-not-a-secret\n")
mods = e.data / "modules"; mods.mkdir()
SAFE = 'META={"title":"Water reminder","description":"Nudges you to drink water every hour.","requirements":[],"ui":False}\nasync def start(ctx):\n    pass\nasync def stop(ctx):\n    pass\n'
RISKY = 'import os\nMETA={"title":"Disk tidy","description":"Cleans up temporary files.","requirements":[]}\ndef tidy():\n    os.remove("x")\nasync def start(ctx):\n    pass\nasync def stop(ctx):\n    pass\n'
(mods / "water_reminder.py").write_text(SAFE)
get = lambda path: req("GET", path)[1]
def feat(fid): return next((f for f in get("/api/features")["features"] if f["id"] == fid), {})
try:
    e.up()
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        ctx = b.new_context(viewport={"width": 1600, "height": 900}); pg = ctx.new_page(); errs = []; pg.on("pageerror", lambda x: errs.append(str(x)))
        pg.on("dialog", lambda d: d.accept())
        pg.goto(URL); pg.wait_for_function("window.__hud&&__hud.state().online===true", timeout=20000); pg.wait_for_timeout(1600)
        pg.evaluate("__hud.SU.dismissed=true;__hud.closeSetup()")
        def chat(text, wait=900):
            n = len(pg.evaluate("__hud.state().msgs")); pg.fill("#cmdin", text); pg.press("#cmdin", "Enter")
            pg.wait_for_function("__hud.state().msgs.length>=%d" % (n + 2), timeout=30000); pg.wait_for_timeout(wait)
            m = pg.evaluate("__hud.state().msgs.slice(-1)[0]"); return m.get("text") or m.get("t") or json.dumps(m)
        vis = lambda sel: pg.evaluate("(s=>{const e=document.querySelector(s);return !!e&&e.offsetParent!==null})('%s')" % sel)
        check("a new drop-in is PENDING and is not running", feat("water_reminder").get("status") == "needs_approval" and not feat("water_reminder").get("enabled"), feat("water_reminder"))
        pg.wait_for_selector("#modbox.open", timeout=8000)
        check("the HUD tells you about the new add-on (with the plain-words risk text) without turning it on", "same access" in pg.inner_text("#mbody") and "Water reminder" in pg.inner_text("#mbody"), pg.inner_text("#mbody")[:200])
        pg.click("[data-rv=water_reminder]"); pg.wait_for_selector("#mb-ok", timeout=8000); pg.wait_for_function("!document.getElementById('mb-ok').disabled", timeout=60000)
        body = pg.inner_text("#mbody")
        check("review shows what it does", "Nudges you to drink water" in body)
        check("review shows the automatic check (no warning signs)", "NO WARNING SIGNS" in body.upper() and "none of the usual warning signs" in body)
        check("review shows the AI second opinion, labelled as not a guarantee", "SECOND OPINION FROM YOUR OWN AI" in body.upper() and "not a guarantee" in body, body[-500:])
        if SHOTS: pg.screenshot(path=str(DOCS / "module-approval.png"))
        check("still not enabled while the dialog is open", not feat("water_reminder").get("enabled"))
        pg.click("#mb-ok"); pg.wait_for_function("!document.querySelector('#modbox.open')", timeout=8000); pg.wait_for_timeout(600)
        f = feat("water_reminder"); check("Approve turns it on (running)", f.get("enabled") and f.get("status") == "running", f)
        # risky
        (mods / "disk_tidy.py").write_text(RISKY); req("POST", "/api/modules/ack", {})
        r = chat("review the disk tidy module")
        check("voice: 'review the disk tidy module' opens the review (never approves)", "opened the review" in r and not feat("disk_tidy").get("enabled"), r)
        pg.wait_for_selector("#mb-ok"); pg.wait_for_function("!document.getElementById('mb-ok').disabled", timeout=60000)
        body = pg.inner_text("#mbody")
        check("high-risk: the serious warning is listed in plain words", "SERIOUS WARNING SIGNS" in body.upper() and "delete files" in body, body[:400])
        pg.click("#mb-ok"); pg.wait_for_timeout(500)
        check("high-risk: approving without ticking the box does nothing", pg.evaluate("document.querySelector('#modbox.open')!==null") and not feat("disk_tidy").get("enabled"))
        if SHOTS: pg.screenshot(path=str(DOCS / "module-approval-risky.png"))
        pg.click("#mb-decline"); pg.wait_for_function("!document.querySelector('#modbox.open')"); pg.wait_for_timeout(500)
        check("Decline keeps it off", not feat("disk_tidy").get("enabled") and next(m for m in get("/api/modules")["modules"] if m["id"] == "disk_tidy")["state"] == "rejected")
        r = chat("approve the disk tidy module"); check("voice: 'approve ...' is not an approval", "can't approve add-ons by voice" in r and not feat("disk_tidy").get("enabled"), r)
        pg.keyboard.press("Escape"); pg.evaluate("document.getElementById('modbox').classList.remove('open')")
        # master switch
        r = chat("turn off add-on modules"); check("voice: turning add-ons off asks first", "Water reminder" in r and "yes or no" in r and get("/api/modules")["enabled"] is True, r)
        r = chat("yes"); st = get("/api/modules")
        check("...'yes' switches the master switch off and stops the module", st["enabled"] is False and not any(f["status"] == "running" for f in st["features"] if f["id"] == "water_reminder"), r)
        code, j = req("POST", "/api/features/water_reminder", {"enabled": True})
        check("with the master switch off nothing can be switched on (403)", code == 403 and "switched off" in j["error"], (code, j))
        pg.evaluate("ATLAS.openSettings('features')"); pg.wait_for_selector("#addons-sw")
        check("Settings shows the master switch as Off", not pg.evaluate("document.getElementById('addons-sw').classList.contains('on')"))
        if SHOTS: pg.screenshot(path=str(DOCS / "settings-addons.png"))
        r = chat("turn on add-on modules"); check("turning them on needs no question; the approved add-on is not on until switched on again", get("/api/modules")["enabled"] is True and "still wait for your approval" in r, r)
        # tamper
        (mods / "water_reminder.py").write_text(SAFE + "# edited\n")
        check("an approved add-on whose file changes needs approval again", next(m for m in get("/api/modules")["modules"] if m["id"] == "water_reminder")["state"] == "changed")
        check("no page errors", not errs, errs)
        b.close()
    # first-run wizard step
    e.down()
    e2 = Env("modsfr", env_text=None, no_provider=True)
    e = e2
finally: e.down()
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
