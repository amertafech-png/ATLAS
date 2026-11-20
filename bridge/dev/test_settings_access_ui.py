"""Dev-only: Settings can always be opened - from the dock, from the Connect-Hermes card that covers the screen while Hermes is not
running, with Ctrl+, and by saying/typing "open settings" (handled locally, so it works with Hermes down).
   /workspace/pwenv/bin/python bridge/dev/test_settings_access_ui.py [--shots]"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
HERE = Path(__file__).resolve().parent; DOCS = HERE.parent.parent / "docs"; SHOTS = "--shots" in sys.argv
src = (HERE / "test_setup_e2e.py").read_text().split("# ---------------------------------------------------------------- 1.")[0]
exec(compile(src, "test_setup_e2e_prefix", "exec"))
PY = "/workspace/bridge-venv/bin/python"; URL = "http://127.0.0.1:%d/" % BR_PORT
e = Env("setacc", mock_up=False, shim=False)           # Hermes is NOT installed / not running
e.env["ATLAS_NO_BOOT"] = "1"
try:
    e.up()
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        for w, h in ((1600, 900), (1093, 560)):
            pg = b.new_page(viewport={"width": w, "height": h}); errs = []; pg.on("pageerror", lambda x: errs.append(str(x)))
            pg.goto(URL); pg.wait_for_function("window.__hud&&__hud.state().online===true", timeout=20000)
            pg.wait_for_selector("#setup.open", timeout=15000); pg.wait_for_timeout(500)
            dock = pg.evaluate("(()=>{const b=document.querySelector('.dk[data-m=settings]');const r=b.getBoundingClientRect();return r.width>0&&r.bottom<=innerHeight&&r.right<=innerWidth})()")
            check("%dx%d: the Settings button is in the dock and on screen" % (w, h), dock)
            if SHOTS and w == 1600: pg.screenshot(path=str(DOCS / "hermes-down-open-settings.png"))
            pg.click("[data-su=opensettings]"); pg.wait_for_timeout(600)
            open_ = lambda: pg.evaluate("document.getElementById('ov').classList.contains('open')&&/SETTINGS/i.test(document.getElementById('ov').innerText)")
            check("%dx%d: 'Open Settings' on the Connect-Hermes card opens Settings" % (w, h), open_() and not pg.evaluate("document.getElementById('setup').classList.contains('open')"))
            pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
            pg.keyboard.press("Control+Comma"); pg.wait_for_timeout(600)
            check("%dx%d: Ctrl+, opens Settings" % (w, h), open_())
            pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
            pg.fill("#cmdin", "open settings"); pg.press("#cmdin", "Enter"); pg.wait_for_timeout(1500)
            check("%dx%d: typing 'open settings' opens it even though Hermes is down" % (w, h), open_(), pg.evaluate("__hud.state().msgs.slice(-1)[0]"))
            check("%dx%d: no page errors" % (w, h), not errs, errs)
            pg.close()
        b.close()
finally: e.down()
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
