"""Dev-only (v1.4.3): ATLAS can always be reset/reloaded, even when the HUD's start-up partly fails.
  * Reset button next to the ATLAS title (independent of the bottom bar), Ctrl+Shift+R, Settings -> Reset, "reset ATLAS" by chat with Hermes down
  * Reset layout to default (with confirmation) keeps everything else in config.json
  * a script error during start-up shows "Part of the screen didn't load - Reload?" and is written to bridge.log via /api/client-error
  * one failing start-up step (settings request) doesn't stop the rest
  * Restart ATLAS services: a new bridge takes over the port and the screen reloads
   /workspace/pwenv/bin/python bridge/dev/test_hud_resilience.py"""
import json, subprocess, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright
HERE = Path(__file__).resolve().parent
src = (HERE / "test_setup_e2e.py").read_text().split("# ---------------------------------------------------------------- 1.")[0]
exec(compile(src, "test_setup_e2e_prefix", "exec"))
PY = "/workspace/bridge-venv/bin/python"; URL = "http://127.0.0.1:%d/" % BR_PORT
LAY = {"L1": "clock", "L2": "weather", "L3": "calendar", "R1": "sports", "R2": "system", "R3": "hermes"}
e = Env("resil", mock_up=False, shim=False)            # Hermes is not running: everything here must work without it
e.env["ATLAS_NO_BOOT"] = "1"
(e.data / "config.json").write_text(json.dumps({"prefs": {"layout": LAY, "skin": "cyan", "brief_morning": True},
                                                "calendar": {"sources": [{"id": "work", "name": "Work", "kind": "url", "value": "https://calendar.google.com/calendar/ical/x/private-y/basic.ics"}]},
                                                "profile": {"name": "Adam", "sports": []}}))
def ready(pg):
    pg.wait_for_function("window.__hud&&__hud.state().online===true&&window.__hudReady===true", timeout=25000); pg.wait_for_timeout(400)
def close_setup(pg):
    pg.evaluate("__hud.SU.dismissed=true;__hud.SU.open=false;document.getElementById('setup')&&document.getElementById('setup').classList.remove('open')")
def click_past_setup(pg, sel, tries=4):
    """Hermes is down in this test, so a status poll can re-open the Connect Hermes card between steps; close it and click again."""
    for i in range(tries):
        close_setup(pg)
        try:
            pg.click(sel, timeout=3000); return
        except Exception:
            if i == tries - 1: raise
try:
    e.up()
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        ctx = b.new_context(viewport={"width": 1536, "height": 730}); pg = ctx.new_page(); errs = []; pg.on("pageerror", lambda x: errs.append(str(x)))
        pg.on("dialog", lambda d: d.accept())
        pg.goto(URL); ready(pg)
        btn = pg.evaluate("(()=>{const b=document.getElementById('rsbtn');if(!b)return null;const r=b.getBoundingClientRect();return {in_top:Math.abs(r.top-document.querySelector('header.top>div').getBoundingClientRect().top)<30,vis:r.width>0&&r.top<60}})()")
        check("Reset button next to the ATLAS title", btn and btn["in_top"] and btn["vis"], btn)
        pg.click("#rsbtn"); pg.wait_for_timeout(300)
        check("Reset menu: Reload screen / Reset layout / Restart services", pg.evaluate("[...document.querySelectorAll('#rs-menu [data-rs]')].map(x=>x.dataset.rs).join(',')") == "reload,layout,restart,close")
        pg.click("[data-rs=close]"); pg.keyboard.press("Control+Shift+R"); pg.wait_for_timeout(300)
        check("Ctrl+Shift+R opens the Reset menu (and does not reload)", pg.evaluate("!!document.getElementById('rs-menu')"))
        pg.evaluate("window.__marker=1"); pg.click("[data-rs=reload]"); ready(pg)
        check("Reload screen reloads the page", pg.evaluate("window.__marker===undefined"))
        # chat with Hermes down
        close_setup(pg); pg.fill("#cmdin", "reset ATLAS"); pg.press("#cmdin", "Enter"); pg.wait_for_timeout(1500)
        check("'reset ATLAS' by chat opens the Reset menu with Hermes down", pg.evaluate("!!document.getElementById('rs-menu')"), pg.evaluate("__hud.state().msgs.slice(-1)"))
        # reset layout
        check("Adam's layout is on screen (calendar bottom left)", pg.evaluate("document.querySelector('.panel[data-panel=calendar]').dataset.slot") == "L3")
        pg.click("[data-rs=layout]"); ready(pg)
        cfg = json.loads((e.data / "config.json").read_text())
        check("Reset layout: default panels back (scanner bottom left, calendar on the shelf)",
              pg.evaluate("document.querySelector('.panel[data-panel=scanner]').dataset.slot") == "L3" and pg.evaluate("!document.querySelector('.panel[data-panel=calendar]').dataset.slot"))
        check("Reset layout keeps the calendar link, profile and skin", "layout" not in cfg["prefs"] and cfg["calendar"]["sources"][0]["id"] == "work" and cfg["profile"]["name"] == "Adam" and cfg["prefs"]["skin"] == "cyan", cfg)
        # Settings -> Reset tab
        close_setup(pg); pg.keyboard.press("Control+Comma"); pg.wait_for_timeout(400); click_past_setup(pg, "[data-tab=reset]"); pg.wait_for_timeout(300)
        check("Settings has a Reset tab with the same three actions", pg.evaluate("[...document.querySelectorAll('[data-rst]')].map(x=>x.dataset.rst).join(',')") == "reload,layout,restart")
        check("no page errors so far", not errs, errs)
        pg.close()
        # a crash during start-up
        pg = ctx.new_page(); errs2 = []; pg.on("pageerror", lambda x: errs2.append(str(x)))
        def broken(route):
            r = route.fetch(); html = r.text()
            html = html.replace("function applyLayout(lay){", "throw new Error('boom-test: a panel failed to start');\nfunction applyLayout(lay){", 1)
            route.fulfill(response=r, body=html)
        pg.route(URL, broken); pg.goto(URL); pg.wait_for_timeout(2500)
        check("start-up crash: banner 'Part of the screen didn't load' with Reload", pg.evaluate("(document.getElementById('rs-banner')||{}).innerText||''").startswith("Part of the screen didn"), pg.evaluate("document.body.innerText.slice(0,200)"))
        check("start-up crash: the Reset button is still there and works", pg.evaluate("!!document.getElementById('rsbtn')") and (pg.click("#rsbtn") or pg.evaluate("!!document.getElementById('rs-menu')")))
        time.sleep(0.5); log = (e.t / "bridge.log").read_text()
        check("start-up crash is written to bridge.log", "[hud] script error (startup" in log and "boom-test" in log, log[-600:])
        pg.unroute(URL); pg.close()
        # one start-up step failing does not stop the others
        pg = ctx.new_page(); pg.route("**/api/settings", lambda r: r.fulfill(status=500, body="nope"))
        pg.goto(URL); pg.wait_for_function("window.__hudReady===true", timeout=25000); pg.wait_for_timeout(1500)
        check("a failing settings request: start-up still finishes, dock and right column present",
              pg.evaluate("!!document.querySelector('.dk[data-m=settings]')&&document.getElementById('colR').children.length>0&&__hud.state().online===true"))
        pg.unroute("**/api/settings"); pg.close()
        # restart services
        pg = ctx.new_page(); pg.on("dialog", lambda d: d.accept()); pg.goto(URL); ready(pg)
        old = e.bridge.pid
        pg.click("#rsbtn"); pg.click("[data-rs=restart]")
        pg.wait_for_function("window.__hudReady===true&&!document.getElementById('rs-menu')", timeout=90000)
        new = subprocess.run(["pgrep", "-f", "bridge.py --after-restart"], capture_output=True, text=True).stdout.split()
        check("Restart ATLAS services: the old bridge exited, a new one answers and the screen reloaded", e.bridge.poll() is not None and new, (old, e.bridge.poll(), new))
        b.close()
finally:
    e.down(); subprocess.run(["pkill", "-f", "bridge.py --after-restart"])
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
