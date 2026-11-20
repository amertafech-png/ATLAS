"""Dev-only regression (v1.4.3): a reply with one very long web address (an OAuth link, no spaces) pushed the right column, the orb
and the bottom bar off screen (the centre grid track grew to the width of the word) - and it stayed that way after a reload because the
caption/history show the last reply.  Uses Adam's layout (calendar in L3, sports with no teams, system, hermes).
   /workspace/pwenv/bin/python bridge/dev/test_long_reply_layout.py"""
import json, sys
from pathlib import Path
from playwright.sync_api import sync_playwright
HERE = Path(__file__).resolve().parent
src = (HERE / "test_setup_e2e.py").read_text().split("# ---------------------------------------------------------------- 1.")[0]
exec(compile(src, "test_setup_e2e_prefix", "exec"))
PY = "/workspace/bridge-venv/bin/python"; URL = "http://127.0.0.1:%d/" % BR_PORT
e = Env("longurl", shim=False, env_text="API_SERVER_ENABLED=true\nAPI_SERVER_KEY=test-key-0123456789abcdef\nOPENROUTER_API_KEY=placeholder-not-a-secret\n")
(e.data / "config.json").write_text(json.dumps({"prefs": {"layout": {"L1": "clock", "L2": "weather", "L3": "calendar", "R1": "sports", "R2": "system", "R3": "hermes"}, "skin": "cyan", "brief_morning": True},
                                                "profile": {"name": "Adam", "sports": []}}))
GEO = """(()=>{const g=id=>{const b=document.getElementById(id).getBoundingClientRect();return [b.left,b.right]};const d=document.querySelector('.dk[data-m=settings]').getBoundingClientRect();
  const o=document.getElementById('orb');const ob=o?o.getBoundingClientRect():{left:0,right:0};
  return {R:g('colR'),vw:innerWidth,dock:[d.left,d.right],orb:[ob.left,ob.right]}})()"""
def on_screen(g):
    return g["R"][1] <= g["vw"] + 1 and g["dock"][1] <= g["vw"] + 1 and g["orb"][1] <= g["vw"] + 1
try:
    e.up()
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        for vw, vh in ((1536, 730), (1920, 1080), (1280, 720)):
            pg = b.new_page(viewport={"width": vw, "height": vh}); errs = []; pg.on("pageerror", lambda x: errs.append(str(x)))
            pg.goto(URL); pg.wait_for_function("window.__hud&&__hud.state().online===true", timeout=20000); pg.wait_for_timeout(800)
            check("%dx%d: right column, orb and dock on screen at start" % (vw, vh), on_screen(pg.evaluate(GEO)), pg.evaluate(GEO))
            pg.evaluate("__hud.SU.dismissed=true;__hud.SU.open=false;document.getElementById('setup').classList.remove('open')")
            n0 = pg.evaluate("__hud.state().msgs.length")
            pg.fill("#cmdin", "please explain this sign-in link longurl"); pg.press("#cmdin", "Enter")
            pg.wait_for_function("n=>{const m=__hud.state().msgs;return m.length>=n+2&&/oauth2/.test(m[m.length-1].text)}", arg=n0, timeout=40000); pg.wait_for_timeout(600)
            g = pg.evaluate(GEO)
            check("%dx%d: after a reply with a 3000-character link everything stays on screen" % (vw, vh), on_screen(g), g)
            cap = pg.evaluate("document.getElementById('caption').innerText")
            check("%dx%d: the caption shows the link shortened" % (vw, vh), "…" in cap and len(cap) < 400, cap[:200])
            pg.reload(); pg.wait_for_function("window.__hud&&__hud.state().online===true", timeout=20000); pg.wait_for_timeout(800)
            check("%dx%d: still on screen after a reload (history has the long reply)" % (vw, vh), on_screen(pg.evaluate(GEO)), pg.evaluate(GEO))
            check("%dx%d: no page errors" % (vw, vh), not errs, errs)
            pg.close()
        pg = b.new_page(viewport={"width": 1536, "height": 730}); pg.goto(URL); pg.wait_for_function("window.__hud&&__hud.state().online===true", timeout=20000)
        pg.wait_for_timeout(1500); pg.evaluate("document.querySelector('.dk[data-m=chat]').click()"); pg.wait_for_timeout(800)
        a = pg.evaluate("(()=>{const a=[...document.querySelectorAll('#msgs a.url')].pop();if(!a)return null;const m=document.getElementById('msgs').getBoundingClientRect(),r=a.getBoundingClientRect();return {t:a.textContent,h:a.getAttribute('href').length,fits:r.right<=m.right+1}})()")
        check("chat bubble: the link is a short clickable label with the full address behind it, inside the bubble", a and a["h"] > 1000 and len(a["t"]) < 60 and a["fits"], a)
        b.close()
finally: e.down()
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
