"""Dev-only: settings by voice/chat + the Settings screen + sports removal, in the real HUD (headless Chrome) against the real bridge.
   /workspace/pwenv/bin/python bridge/dev/test_settings_ui.py [--shots]"""
import json, sys, tempfile, time
from pathlib import Path
from playwright.sync_api import sync_playwright
HERE = Path(__file__).resolve().parent; DOCS = HERE.parent.parent / "docs"; SHOTS = "--shots" in sys.argv
src = (HERE / "test_setup_e2e.py").read_text().split("# ---------------------------------------------------------------- 1.")[0]
exec(compile(src, "test_setup_e2e_prefix", "exec"))
PY = "/workspace/bridge-venv/bin/python"; URL = "http://127.0.0.1:%d/" % BR_PORT

e = Env("setui", env_text="OPENROUTER_API_KEY=placeholder-not-a-secret\n")
mods = e.data / "modules"; mods.mkdir()
for fid in ("computer_control", "sample_tool"):     # tiny drop-in modules so "turn on <module>" has something real to start
    (mods / (fid + ".py")).write_text('META={"title":"%s","description":"test module","requirements":[]}\nasync def start(ctx):\n    pass\nasync def stop(ctx):\n    pass\n' % fid.replace("_", " ").title())
startup = Path(tempfile.mkdtemp(prefix="atlas-startup-"))
e.env["ATLAS_PLATFORM"] = "windows"; e.env["ATLAS_STARTUP_DIR"] = str(startup)

def get(path): return req("GET", path)[1]
def prefs(): return get("/api/settings")["prefs"]
def profile(): return get("/api/profile")["profile"]

try:
    e.up()
    for fid in ("computer_control", "sample_tool"):        # drop-ins are PENDING until approved: approve these two the way the HUD does (scan, second opinion, approve - without switching on)
        req("GET", "/api/modules/%s/review" % fid); req("POST", "/api/modules/%s/opinion" % fid, {}); req("POST", "/api/modules/%s/approve" % fid, {"enable": False})
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
        ctx = b.new_context(viewport={"width": 1600, "height": 900}); pg = ctx.new_page(); errs = []; pg.on("pageerror", lambda x: errs.append(str(x)))
        pg.on("dialog", lambda d: d.accept())
        speaks = []
        pg.route("**/api/speak", lambda r: (speaks.append(1), r.fulfill(status=200, content_type="audio/wav", body=b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00")))
        pg.goto(URL); pg.wait_for_function("window.__hud&&__hud.state().online===true", timeout=20000); pg.wait_for_timeout(1600)
        pg.evaluate("__hud.SU.dismissed=true;__hud.closeSetup()"); pg.wait_for_timeout(300)
        def chat(text, wait=900):
            n = len(pg.evaluate("__hud.state().msgs"))
            pg.fill("#cmdin", text); pg.press("#cmdin", "Enter")
            pg.wait_for_function("__hud.state().msgs.length>=%d" % (n + 2), timeout=15000); pg.wait_for_timeout(wait)
            return pg.evaluate("__hud.state().msgs.slice(-1)[0]")
        def reply(m): return m.get("text") or m.get("t") or json.dumps(m)

        # ---- 1. conversation mode, by chat
        m = chat("turn off conversation mode")
        check("chat 'turn off conversation mode' -> HUD + bridge both off", pg.evaluate("__hud.CONV.on") is False and prefs()["conversation_on"] is False, reply(m))
        pg.reload(); pg.wait_for_function("window.__hud&&__hud.state().online===true"); pg.wait_for_timeout(1500)
        check("...and it survives a reload (bridge is the source of truth)", pg.evaluate("__hud.CONV.on") is False)
        chat("turn on conversation mode"); chat("set the listening window to 11 seconds")
        check("window length by chat", pg.evaluate("__hud.CONV.secs") == 11 and prefs()["conversation_secs"] == 11 and pg.evaluate("__hud.CONV.on") is True)
        # ---- 2. mute / unmute
        pg.evaluate("__hud.SU.dismissed=true;__hud.closeSetup()")
        n0 = len(speaks); chat("mute")
        check("chat 'mute' -> not spoken, mute saved", pg.evaluate("__hud.speakOn()") is False and prefs()["mute"] is True and len(speaks) == n0, (len(speaks), n0))
        n1 = len(speaks); chat("unmute")
        check("chat 'unmute' -> the confirmation IS spoken", pg.evaluate("__hud.speakOn()") is True and prefs()["mute"] is False and len(speaks) > n1)
        # ---- 3. assistant name, city
        chat("change your name to Nova")
        check("'change your name to Nova' -> the whole HUD renames", pg.evaluate("__hud.an()") == "Nova" and get("/api/profile")["assistant"]["name"] == "Nova")
        chat("call me Adam"); check("'call me Adam'", profile()["name"] == "Adam" and pg.evaluate("__hud.prof().name") == "Adam")
        m = chat("set my city to Dallas")
        check("'set my city to Dallas' answers (saved, or a friendly 'are you online' if the box has no internet)", ("Dallas" in reply(m)) or ("online" in reply(m)), reply(m))
        chat("change your name to ATLAS")
        # ---- 4. panel slots (voice/chat)
        vis = lambda sel: pg.evaluate("(s=>{const e=document.querySelector(s);return !!e&&e.offsetParent!==null})('%s')" % sel)
        parent = lambda sel: pg.evaluate("document.querySelector('%s').parentElement.id" % sel)
        chat("hide the weather panel")
        check("'hide the weather panel' empties its slot (tile gone, saved)", not vis("#t-weather") and prefs()["layout"]["L2"] is None)
        chat("show the weather panel"); check("'show the weather panel' brings it back", vis("#t-weather") and prefs()["layout"]["L2"] == "weather")
        m = chat("put sports where weather is")
        check("'put sports where weather is' moves the real tiles (sports into the left column, weather to the right)", parent("#t-football") == "colL" and parent("#t-weather") == "colR" and prefs()["layout"]["L2"] == "sports", (reply(m), prefs()["layout"]))
        check("...the reply says what changed", "sports" in reply(m).lower() and "reset my layout" in reply(m))
        pg.reload(); pg.wait_for_function("window.__hud&&__hud.state().online===true"); pg.wait_for_timeout(1500); pg.evaluate("__hud.SU.dismissed=true;__hud.closeSetup()")
        check("the layout persists across a reload", parent("#t-football") == "colL" and vis("#t-football"))
        m = chat("what panels can I use"); check("'what panels can I use' answers locally", "You can use these panels" in reply(m), reply(m))
        chat("reset my layout"); check("'reset my layout' restores it", parent("#t-football") == "colR" and parent("#t-weather") == "colL" and prefs()["layout"]["L2"] == "weather")
        # ---- 5. confirmations through the real chat endpoint
        m = chat("turn on computer control"); check("add-on ON asks first (nothing enabled yet)", "Say yes" in reply(m) and not any(f["enabled"] for f in get("/api/features")["features"] if f["id"] == "computer_control"), reply(m))
        chat("yes"); check("'yes' switches it on, the module really started", any(f["id"] == "computer_control" and f["enabled"] and f["status"] == "running" for f in get("/api/features")["features"]))
        chat("turn off computer control"); check("OFF needs no question", not any(f["id"] == "computer_control" and f["enabled"] for f in get("/api/features")["features"]))
        m = chat("turn on wake word"); check("wake word ON asks, and mentions the microphone", "microphone" in reply(m) and not any(f["id"] == "wake_word" and f["enabled"] for f in get("/api/features")["features"]), reply(m))
        chat("no"); check("'no' leaves it off", not any(f["id"] == "wake_word" and f["enabled"] for f in get("/api/features")["features"]))
        m = chat("turn off wake word"); check("'turn off wake word' when already off is still a clean answer (no Hermes)", "wake word" in reply(m).lower(), reply(m))
        m = chat("switch to Claude"); check("AI switch asks first", "Claude" in reply(m) and "Say yes" in reply(m) and not pg.evaluate("__hud.SU.open"), reply(m))
        chat("yes", wait=700); check("'yes' opens the AI sign-in screen on Claude", pg.evaluate("__hud.SU.open===true&&__hud.SU.showAI===true&&__hud.SU.sel==='claude'"), pg.evaluate("JSON.stringify([__hud.SU.open,__hud.SU.showAI,__hud.SU.sel])"))
        pg.evaluate("__hud.SU.dismissed=true;__hud.closeSetup()")
        chat("turn on start at login")
        check("'start at login' writes the Windows startup item", (startup / "ATLAS bridge.cmd").exists())
        chat("turn off start at login"); check("...and removes it", not (startup / "ATLAS bridge.cmd").exists())
        m = chat("what are my settings"); check("'what are my settings' reads them out", "Here are your settings" in reply(m) and "conversation mode" in reply(m), reply(m))
        check("no page errors (chat/voice settings)", not errs, errs)

        # ---- 6. every registry entry is on its Settings tab
        sets = get("/api/settings")["settings"]
        missing = []
        tabs = {"profile": "profile", "features": "features", "general": "general", "ai": "ai"}
        pg.evaluate("ATLAS.openSettings('profile')")
        for tab in tabs:
            pg.evaluate("ATLAS.openSettings('%s')" % tab); pg.wait_for_timeout(500)
            for s in sets:
                if s["ui"] == tab and pg.query_selector('#ovb [data-setting="%s"]' % s["id"]) is None and pg.query_selector('[data-setting="%s"]' % s["id"]) is None:
                    missing.append((tab, s["id"]))
        check("every registry setting has a control on its Settings tab (%d settings)" % len(sets), not missing, missing)
        pg.evaluate("ATLAS.openSettings('general')"); pg.wait_for_selector('[data-setting="mute"]')
        if SHOTS: pg.wait_for_timeout(300); pg.screenshot(path=str(DOCS / "settings-general.png"))
        # ---- 7. General tab controls
        pg.click('[data-gset="mute"]'); pg.wait_for_timeout(500)
        check("General tab: mute switch -> saved + HUD muted", prefs()["mute"] is True and pg.evaluate("__hud.speakOn()") is False)
        pg.click('[data-gset="mute"]'); pg.wait_for_timeout(500)
        pg.select_option('[data-gsel="voice.speed"]', "fast"); pg.wait_for_timeout(500)
        check("General tab: speed select -> bridge", get("/api/settings")["settings"] and next(s for s in get("/api/settings")["settings"] if s["id"] == "voice.speed")["value"] == "fast")
        pg.select_option('[data-gsel="layout.L3"]', "sports"); pg.wait_for_timeout(600)
        check("General tab slot picker: bottom-left = sports swaps the tiles", prefs()["layout"]["L3"] == "sports" and prefs()["layout"]["R1"] == "scanner" and pg.evaluate("document.querySelector('#t-football').parentElement.id") == "colL")
        pg.select_option('[data-gsel="layout.R2"]', "empty"); pg.wait_for_timeout(500)
        check("slot picker 'empty' leaves the slot empty", prefs()["layout"]["R2"] is None and not vis("#t-system"))
        if SHOTS: pg.wait_for_selector('[data-gsel="layout.L1"]'); pg.evaluate("document.querySelector('[data-gsel=\"layout.L1\"]').scrollIntoView()"); pg.wait_for_timeout(300); pg.screenshot(path=str(DOCS / "settings-slot-picker.png"))
        pg.click('[data-gact="layout.reset"]'); pg.wait_for_timeout(600)
        check("General tab 'Reset' restores the layout", prefs()["layout"] == {"L1": "clock", "L2": "weather", "L3": "scanner", "R1": "sports", "R2": "system", "R3": "hermes"})
        pg.click('[data-gact="updates.check"]'); pg.wait_for_function("document.getElementById('gmsg').textContent.length>0", timeout=15000)
        check("General tab: 'Check now' reports", True)

        # ---- 8. sports: removal in Settings -> Profile -> Sports, and by voice
        post = lambda path, body: req("POST", path, body)
        post("/api/profile", {"sports": [{"sport": "Soccer", "leagues": ["Serie A"], "teams": ["Juventus", "Arsenal"]}, {"sport": "NBA", "leagues": [], "teams": ["Los Angeles Lakers"]}]})
        pg.reload(); pg.wait_for_function("window.__hud&&__hud.state().online===true"); pg.wait_for_timeout(1500); pg.evaluate("__hud.SU.dismissed=true;__hud.closeSetup()")
        panel = lambda: pg.inner_text("#teamsbody")
        check("Sports panel shows the teams", "Juventus" in panel() or "Soccer" in panel() or len(panel()) > 20, panel()[:80])
        pg.evaluate("ATLAS.openSettings('profile')"); pg.wait_for_selector('.sportbox[data-sport="Soccer"]')
        if SHOTS: pg.wait_for_timeout(300); pg.screenshot(path=str(DOCS / "settings-sports-remove.png"))
        pg.click('.sportbox[data-sport="Soccer"] .tg:has-text("Juventus") .tx'); pg.wait_for_timeout(700)
        sp = {x["sport"]: x for x in profile()["sports"]}
        check("X on a team chip removes it and PERSISTS immediately (profile.sports)", sp["Soccer"]["teams"] == ["Arsenal"] and "NBA" in sp, sp)
        check("...the Sports panel updated at once", "Juventus" not in panel(), panel())
        pg.click('.sportbox[data-sport="Soccer"] .tg:has-text("Serie A") .tx'); pg.wait_for_timeout(600)
        check("X on a league chip removes it too", {x["sport"]: x for x in profile()["sports"]}["Soccer"]["leagues"] == [])
        pg.click('.sportbox[data-sport="Soccer"] .sx'); pg.wait_for_selector('.sportbox[data-sport="Soccer"] .conf')
        check("removing a whole sport asks first (inline confirm, nothing removed yet)", len(profile()["sports"]) == 2 and "Remove Soccer" in pg.inner_text('.sportbox[data-sport="Soccer"] .conf'))
        pg.click('.sportbox[data-sport="Soccer"] .conf [data-confirm="no"]'); pg.wait_for_timeout(300)
        check("'Keep' leaves it", len(profile()["sports"]) == 2 and pg.query_selector('.sportbox[data-sport="Soccer"] .conf') is None)
        pg.click('.chip.sel[data-sport="Soccer"]'); pg.wait_for_selector('.sportbox[data-sport="Soccer"] .conf')
        check("the X on the selected sport chip asks too", len(profile()["sports"]) == 2)
        pg.click('.sportbox[data-sport="Soccer"] .conf [data-confirm="yes"]'); pg.wait_for_timeout(700)
        check("confirm removes the sport, persisted", [x["sport"] for x in profile()["sports"]] == ["NBA"], profile()["sports"])
        pg.click('.sportbox[data-sport="NBA"] .tg:has-text("Lakers") .tl'); pg.wait_for_timeout(300)
        check("clicking a team's name edits it (text goes back into the box)", pg.input_value('.sportbox[data-sport="NBA"] .tags input') == "Los Angeles Lakers")
        pg.fill('.sportbox[data-sport="NBA"] .tags input', "Boston Celtics"); pg.press('.sportbox[data-sport="NBA"] .tags input', "Enter"); pg.click("#pf-save"); pg.wait_for_timeout(800)
        check("...edited team saved", {x["sport"]: x for x in profile()["sports"]}["NBA"]["teams"] == ["Boston Celtics"], profile()["sports"])
        pg.click('.sportbox[data-sport="NBA"] .sx'); pg.click('.sportbox[data-sport="NBA"] .conf [data-confirm="yes"]'); pg.wait_for_timeout(800)
        check("last sport removed -> empty state, profile.sports == []", profile()["sports"] == [] and pg.query_selector("#sportsempty") is not None, profile())
        check("...Sports panel shows its empty state at once", "NO SPORTS YET" in panel().upper(), panel())
        if SHOTS: pg.wait_for_timeout(300); pg.screenshot(path=str(DOCS / "settings-sports-empty.png"))
        pg.reload(); pg.wait_for_function("window.__hud&&__hud.state().online===true"); pg.wait_for_timeout(1500); pg.evaluate("__hud.SU.dismissed=true;__hud.closeSetup()")
        check("...and it stays empty after a reload", "NO SPORTS YET" in panel().upper())
        # by voice/chat
        post("/api/profile", {"sports": [{"sport": "Soccer", "leagues": [], "teams": ["Juventus", "Arsenal"]}, {"sport": "NBA", "leagues": [], "teams": []}]})
        pg.reload(); pg.wait_for_function("window.__hud&&__hud.state().online===true"); pg.wait_for_timeout(1500); pg.evaluate("__hud.SU.dismissed=true;__hud.closeSetup()")
        chat("stop following Juventus")
        check("chat 'stop following Juventus' -> removed, panel updated", profile()["sports"][0]["teams"] == ["Arsenal"] and "Juventus" not in panel(), (profile()["sports"], panel()))
        m = chat("remove soccer"); check("chat 'remove soccer' asks first", "Remove Soccer" in reply(m) and len(profile()["sports"]) == 2, reply(m))
        chat("yes"); check("'yes' removes it everywhere", [x["sport"] for x in profile()["sports"]] == ["NBA"] and pg.evaluate("__hud.prof().sports.length") == 1)
        m = chat("follow Juventus in soccer"); check("follow by voice adds it back", {x["sport"]: x for x in profile()["sports"]}["Soccer"]["teams"] == ["Juventus"], reply(m))
        pg.evaluate("ATLAS.openSettings('profile')"); pg.wait_for_selector('.sportbox[data-sport="Soccer"]')
        chat("stop following Juventus")
        pg.wait_for_timeout(500)
        check("a voice change updates an open Settings -> Profile -> Sports at once", pg.query_selector('.sportbox[data-sport="Soccer"] .tg:has-text("Juventus")') is None)
        # ---- 9. reset from the General tab
        pg.evaluate("ATLAS.openSettings('general')"); pg.wait_for_selector('[data-gact="reset"]'); pg.click('[data-gact="reset"]'); pg.wait_for_timeout(1000)
        check("General tab 'Reset…' (confirmed) clears profile + prefs", profile()["sports"] == [] and profile()["name"] == "" and prefs()["mute"] is False)
        check("no page errors (Settings screen)", not errs, errs)
        b.close()
finally: e.down()
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
