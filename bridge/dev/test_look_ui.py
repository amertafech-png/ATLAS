"""Dev-only: skins (theme.skin) and the task-based centre animations in the real HUD against the real bridge.
   /workspace/pwenv/bin/python bridge/dev/test_look_ui.py [--shots]"""
import json, subprocess, sys
from pathlib import Path
from playwright.sync_api import sync_playwright
HERE = Path(__file__).resolve().parent; DOCS = HERE.parent.parent / "docs"; SHOTS = "--shots" in sys.argv
src = (HERE / "test_setup_e2e.py").read_text().split("# ---------------------------------------------------------------- 1.")[0]
exec(compile(src, "test_setup_e2e_prefix", "exec"))
PY = "/workspace/bridge-venv/bin/python"; URL = "http://127.0.0.1:%d/" % BR_PORT
WAV = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
SH = Path("/tmp/atlas-look-shots"); SH.mkdir(exist_ok=True)

e = Env("look", env_text="OPENROUTER_API_KEY=placeholder-not-a-secret\n")
def get(path): return req("GET", path)[1]
try:
    e.up()
    post = lambda path, body: req("POST", path, body)[1]
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
        def chat(text, wait=700):
            n = len(pg.evaluate("__hud.state().msgs"))
            pg.fill("#cmdin", text); pg.press("#cmdin", "Enter")
            pg.wait_for_function("__hud.state().msgs.length>=%d" % (n + 2), timeout=30000); pg.wait_for_timeout(wait)
            m = pg.evaluate("__hud.state().msgs.slice(-1)[0]"); return m.get("text") or m.get("t") or json.dumps(m)
        filt = lambda: pg.evaluate("getComputedStyle(document.body).filter")
        check("default skin is cyan with no filter", pg.evaluate("document.documentElement.dataset.skin") == "cyan" and filt() == "none", filt())
        for sk, word in (("ember", "change the skin to ember"), ("violet", "use the violet theme"), ("emerald", "set the theme to green"), ("cyan", "go back to the original skin")):
            r = chat(word, 900)
            ok = pg.evaluate("document.documentElement.dataset.skin") == sk and ((filt() == "none") if sk == "cyan" else ("hue-rotate" in filt()))
            check("skin by voice: %r -> %s (HUD recoloured)" % (word, sk), ok, (r, pg.evaluate("document.documentElement.dataset.skin"), filt()))
            if SHOTS and sk != "cyan" or SHOTS and sk == "cyan" and False:
                closebox(); pg.screenshot(path=str(SH / ("skin-%s.png" % sk)))
        if SHOTS:
            chat("go back to the original skin"); pg.screenshot(path=str(SH / "skin-cyan.png"))
        check("the skin is saved (survives a reload)", True)
        chat("change the skin to ember", 600); pg.reload(); pg.wait_for_function("window.__hud&&__hud.state().online===true"); pg.wait_for_timeout(1500)
        check("...after a reload the skin is still ember", pg.evaluate("document.documentElement.dataset.skin") == "ember", pg.evaluate("document.documentElement.dataset.skin"))
        chat("go back to the original skin", 500)
        closebox()
        # ---- scenes
        check("idle: no scene", pg.evaluate("__hud.scene().scene") == "", pg.evaluate("__hud.scene()"))
        sf = pg.evaluate("['whats the weather in Dallas','play some music','search the web for ramen','tell me a joke','who is the latest champion'].map(__hud.sceneFor)")
        check("requests map to scenes (weather / music / searching / none)", sf == ["weather", "music", "searching", "", "searching"], sf)
        for sc in ("searching", "weather", "music"):
            pg.evaluate("__hud.setScene('%s')" % sc); pg.wait_for_timeout(700)
            check("scene %s shows in the HUD" % sc, pg.evaluate("document.body.dataset.scene") == sc, pg.evaluate("__hud.scene()"))
            if SHOTS: pg.screenshot(path=str(SH / ("scene-%s.png" % sc)), clip={"x": 480, "y": 60, "width": 640, "height": 560})
        pg.evaluate("__hud.setScene('')")
        r = chat("what's the weather", 400)
        check("a weather question shows the weather scene while ATLAS answers", pg.evaluate("__hud.scene().scene") == "weather", pg.evaluate("__hud.scene()"))
        pg.wait_for_function("__hud.scene().scene===''", timeout=20000)
        check("...and it clears a few seconds after the reply", pg.evaluate("__hud.scene().scene") == "")
        chat("turn off task animations", 800)
        pg.evaluate("__hud.setScene('music')"); pg.wait_for_timeout(300)
        check("task animations off: scenes stay off", pg.evaluate("__hud.scene().scene") == "" and pg.evaluate("__hud.scene().on") is False, pg.evaluate("__hud.scene()"))
        chat("turn on task animations", 800); pg.evaluate("__hud.setScene('music')")
        check("...and back on", pg.evaluate("__hud.scene().scene") == "music"); pg.evaluate("__hud.setScene('')")
        # settings page lists both
        pg.evaluate("ATLAS.openSettings('general')"); pg.wait_for_timeout(900)
        body = pg.evaluate("document.body.innerText")
        check("Settings shows the Look group: skin picker and task animations", "Skin (color theme)" in body and "Task animations" in body, "")
        check("no page errors", not errs, errs)
        b.close()
finally: e.down()
if SHOTS and not fails:
    subprocess.call([PY, "-c", """
from PIL import Image
a=[Image.open('/tmp/atlas-look-shots/skin-'+s+'.png').resize((800,450)) for s in ('cyan','ember','violet','emerald') for s in [s]]
m=Image.new('RGB',(1600,900));[m.paste(im,((i%2)*800,(i//2)*450)) for i,im in enumerate(a)];m.save('OUT1')
b=[Image.open('/tmp/atlas-look-shots/scene-'+s+'.png').resize((427,373)) for s in ('searching','weather','music') for s in [s]]
n=Image.new('RGB',(1281,373));[n.paste(im,(i*427,0)) for i,im in enumerate(b)];n.save('OUT2')
""".replace("OUT1", str(DOCS / "skins.png")).replace("OUT2", str(DOCS / "center-scenes.png"))])
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
