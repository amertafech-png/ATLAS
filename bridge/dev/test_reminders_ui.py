"""Dev-only: spoken reminders and timers in the real HUD against the real bridge - voice/chat in, panel, countdown, chime + speech out, restart persistence.
   /workspace/pwenv/bin/python bridge/dev/test_reminders_ui.py [--shots]"""
import json, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright
HERE = Path(__file__).resolve().parent; DOCS = HERE.parent.parent / "docs"; SHOTS = "--shots" in sys.argv
src = (HERE / "test_setup_e2e.py").read_text().split("# ---------------------------------------------------------------- 1.")[0]
exec(compile(src, "test_setup_e2e_prefix", "exec"))
PY = "/workspace/bridge-venv/bin/python"; URL = "http://127.0.0.1:%d/" % BR_PORT
WAV = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"

e = Env("reminders", env_text="OPENROUTER_API_KEY=placeholder-not-a-secret\n")
def get(path): return req("GET", path)[1]
def post(path, body): return req("POST", path, body)[1]
try:
    e.up()
    post("/api/profile", {"name": "Adam", "city": "Dallas"})
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
        ctx = b.new_context(viewport={"width": 1600, "height": 900}); pg = ctx.new_page(); errs = []; pg.on("pageerror", lambda x: errs.append(str(x)))
        speaks = []
        pg.route("**/api/speak", lambda r: (speaks.append(r.request.post_data), r.fulfill(status=200, content_type="audio/wav", body=WAV)))
        pg.add_init_script("window.__chimes=0;(function(){const A=window.AudioContext||window.webkitAudioContext;if(!A)return;const o=A.prototype.createOscillator;A.prototype.createOscillator=function(){window.__chimes++;return o.apply(this,arguments)}})()")
        pg.goto(URL)
        try: pg.wait_for_function("window.__hud&&__hud.state().online===true", timeout=20000)
        except Exception: print("ERRS", errs); raise
        pg.wait_for_timeout(1600)
        pg.evaluate("__hud.SU.dismissed=true;__hud.closeSetup()"); pg.wait_for_timeout(300)
        def closebox():
            if pg.evaluate("!!document.querySelector('#modbox.open')"): pg.click("#mb-close"); pg.wait_for_timeout(300)
        closebox()
        def chat(text, wait=700):
            n = len(pg.evaluate("__hud.state().msgs"))
            pg.fill("#cmdin", text); pg.press("#cmdin", "Enter")
            pg.wait_for_function("__hud.state().msgs.length>=%d" % (n + 2), timeout=30000); pg.wait_for_timeout(wait)
            m = pg.evaluate("__hud.state().msgs.slice(-1)[0]"); return m.get("text") or m.get("t") or json.dumps(m)
        txt = lambda sel: pg.evaluate("(s=>{const e=document.querySelector(s);return e?e.innerText:''})('%s')" % sel)
        vis = lambda sel: pg.evaluate("(s=>{const e=document.querySelector(s);return !!e&&e.offsetParent!==null})('%s')" % sel)

        r = chat("replace the scanner with my reminders")
        check("'replace the scanner with my reminders' shows the Reminders panel", vis("#t-reminders") and not vis("#t-scan"), r)
        check("the panel starts empty and says what to say", "NOTHING SET" in txt("#rembody").upper(), txt("#rembody"))
        r = chat("remind me in 2 hours to call Mike")
        check("a spoken reminder is set and read back", "call Mike" in r, r)
        check("...and appears in the panel", "call Mike" in txt("#rembody"), txt("#rembody"))
        r = chat("set a 10 minute timer")
        check("a timer is set", "10-minute timer" in r, r)
        pg.wait_for_timeout(1200)
        t1 = txt("[data-rleft]:not(:empty)"); lab = pg.evaluate("[...document.querySelectorAll('[data-rleft]')].map(e=>e.textContent)")
        check("the timer shows a live countdown", any(x.startswith("9:") for x in lab), lab)
        pg.wait_for_timeout(2100); lab2 = pg.evaluate("[...document.querySelectorAll('[data-rleft]')].map(e=>e.textContent)")
        check("...which ticks down", lab2 != lab, (lab, lab2))
        r = chat("what reminders and timers do I have")
        check("'what reminders do I have' lists them", "call Mike" in r and "timer" in r.lower(), r)
        r = chat("how much time is left on my timer");  check("time left on the timer", "minute" in r.lower() or "second" in r.lower(), r)
        # the panel quick-add form
        closebox(); pg.fill("#rem-in", "water the plants in 3 hours"); pg.click("#rem-add"); pg.wait_for_timeout(900)
        check("the panel's form adds a reminder too", "water the plants" in txt("#rembody"), txt("#rembody"))
        # persistence across restarts
        ids = [i["id"] for i in get("/api/reminders")["items"]]
        e.down(); e.up(); pg.wait_for_function("__hud.state().online===true", timeout=30000); pg.wait_for_timeout(1500)
        got = get("/api/reminders")["items"]
        check("reminders and the timer survive a bridge restart", [i["id"] for i in got] == ids and len(ids) == 3, ids)
        # cancel by voice
        r = chat("cancel the timer"); check("'cancel the timer' cancels it", not any(i["kind"] == "timer" for i in get("/api/reminders")["items"]), r)
        r = chat("cancel the reminder about the plants"); check("cancel a reminder by what it says", not any("plants" in i["text"] for i in get("/api/reminders")["items"]), r)
        # alert: a 2-second timer
        n0 = len(speaks); c0 = pg.evaluate("window.__chimes")
        chat("set a 2 second timer"); pg.wait_for_function("document.querySelector('.rem.fired')", timeout=12000); pg.wait_for_timeout(2200)
        check("a due timer shows 'Due now', chimes, and speaks", txt("#remtag").upper() == "DUE NOW" and pg.evaluate("window.__chimes") - c0 >= 3 and len(speaks) > n0, (txt("#remtag"), pg.evaluate("window.__chimes") - c0, len(speaks) - n0))
        say = json.loads(speaks[-1])["text"] if speaks else ""
        check("...and the spoken text names it", "timer" in say.lower() and "done" in say.lower(), say)
        if SHOTS:
            chat("remind me at 6 pm to pick up the kids", wait=300); pg.wait_for_timeout(500)
            pg.screenshot(path=str(DOCS / "reminders-panel.png"))
        closebox(); pg.click("[data-rdis]"); pg.wait_for_timeout(600)
        check("Done dismisses the alert", not vis(".rem.fired") and not get("/api/reminders")["alerts"], txt("#rembody"))
        # chime preference
        r = chat("turn off the reminder chime"); check("the chime can be turned off by voice", get("/api/settings")["prefs"]["reminder_chime"] is False, r)
        c0 = pg.evaluate("window.__chimes"); chat("set a 2 second timer"); pg.wait_for_function("document.querySelector('.rem.fired')", timeout=12000); pg.wait_for_timeout(1500)
        check("with the chime off, a due timer is spoken but silent of chime", pg.evaluate("window.__chimes") == c0, (c0, pg.evaluate("window.__chimes")))
        # a reminder that came due while the HUD was closed arrives marked late on reconnect
        closebox(); pg.click("[data-rdis]")
        post("/api/reminders/quick", {"text": "stretch in 3 seconds"})
        pg.goto("about:blank"); time.sleep(5)
        pg.goto(URL); pg.wait_for_function("window.__hud&&__hud.state().online===true", timeout=20000); pg.wait_for_function("document.querySelector('.rem.fired')", timeout=15000)
        check("a reminder missed while the HUD was closed shows up when it reopens", "stretch" in txt(".rem.fired"), txt("#rembody"))
        check("no page errors", not errs, errs)
        b.close()
finally: e.down()
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
