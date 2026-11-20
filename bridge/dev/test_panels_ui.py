"""Dev-only: Calendar panel, Sports live scores, Daily brief, the slot commands that bring them on screen - in the real HUD against the real bridge.
   /workspace/pwenv/bin/python bridge/dev/test_panels_ui.py [--shots]"""
import json, sys, tempfile, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright
HERE = Path(__file__).resolve().parent; DOCS = HERE.parent.parent / "docs"; SHOTS = "--shots" in sys.argv
src = (HERE / "test_setup_e2e.py").read_text().split("# ---------------------------------------------------------------- 1.")[0]
exec(compile(src, "test_setup_e2e_prefix", "exec"))
PY = "/workspace/bridge-venv/bin/python"; URL = "http://127.0.0.1:%d/" % BR_PORT

def z(d): return d.strftime("%Y%m%dT%H%M%SZ")
now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
ics = Path(tempfile.mkdtemp(prefix="atlas-cal-")) / "family.ics"
ics.write_text("\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0",
    "BEGIN:VEVENT", "UID:p1", "DTSTART:" + z(now - timedelta(minutes=20)), "DTEND:" + z(now + timedelta(minutes=40)), "SUMMARY:Planning call", "LOCATION:Room 4", "END:VEVENT",
    "BEGIN:VEVENT", "UID:p2", "DTSTART:" + z(now + timedelta(minutes=90)), "DTEND:" + z(now + timedelta(minutes=150)), "SUMMARY:Dentist", "END:VEVENT",
    "BEGIN:VEVENT", "UID:p3", "DTSTART:" + z(now + timedelta(hours=27)), "DTEND:" + z(now + timedelta(hours=28)), "SUMMARY:Soccer practice", "END:VEVENT",
    "END:VCALENDAR", ""]))
GAMES = {"ok": True, "live": True, "followed": True, "error": "", "unsupported": [], "checked": 2, "games": [
    {"id": "1", "league": "Serie A", "sport": "Soccer", "state": "in", "detail": "62'", "start": "", "mine": "Juventus", "home": {"name": "Juventus", "score": "2"}, "away": {"name": "Torino", "score": "1"}, "completed": False},
    {"id": "2", "league": "NBA", "sport": "NBA", "state": "pre", "detail": "7:30 PM", "start": (now + timedelta(hours=5)).isoformat(), "mine": "Lakers", "home": {"name": "Los Angeles Lakers", "score": ""}, "away": {"name": "Sacramento Kings", "score": ""}, "completed": False}]}

e = Env("panels", env_text="OPENROUTER_API_KEY=placeholder-not-a-secret\n")
def get(path): return req("GET", path)[1]
def prefs(): return get("/api/settings")["prefs"]
def post(path, body): return req("POST", path, body)[1]
try:
    e.up()
    post("/api/profile", {"name": "Adam", "city": "Dallas", "sports": [{"sport": "Soccer", "leagues": ["Serie A"], "teams": ["Juventus"]}, {"sport": "NBA", "leagues": [], "teams": ["Lakers"]}]})
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
        ctx = b.new_context(viewport={"width": 1600, "height": 900}); pg = ctx.new_page(); errs = []; pg.on("pageerror", lambda x: errs.append(str(x)))
        pg.on("dialog", lambda d: d.accept())
        speaks = []
        pg.route("**/api/speak", lambda r: (speaks.append(r.request.post_data), r.fulfill(status=200, content_type="audio/wav", body=b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00")))
        pg.route("**/api/scores", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(GAMES)))
        pg.goto(URL); pg.wait_for_function("window.__hud&&__hud.state().online===true", timeout=20000); pg.wait_for_timeout(1600)
        pg.evaluate("__hud.SU.dismissed=true;__hud.closeSetup()"); pg.wait_for_timeout(300)
        def chat(text, wait=900):
            n = len(pg.evaluate("__hud.state().msgs"))
            pg.fill("#cmdin", text); pg.press("#cmdin", "Enter")
            pg.wait_for_function("__hud.state().msgs.length>=%d" % (n + 2), timeout=30000); pg.wait_for_timeout(wait)
            m = pg.evaluate("__hud.state().msgs.slice(-1)[0]"); return m.get("text") or m.get("t") or json.dumps(m)
        txt = lambda sel: pg.evaluate("(s=>{const e=document.querySelector(s);return e?e.innerText:''})('%s')" % sel)
        vis = lambda sel: pg.evaluate("(s=>{const e=document.querySelector(s);return !!e&&e.offsetParent!==null})('%s')" % sel)

        # ---- calendar: not connected yet
        r = chat("what's on my calendar")
        check("no calendar yet: ATLAS says how to connect one", "haven't connected a calendar" in r, r)
        r = chat("replace the scanner with my calendar")
        check("'replace the scanner with my calendar' puts the Calendar panel on screen", vis("#t-calendar") and not vis("#t-scan") and "calendar" in r.lower(), r)
        check("...the panel invites you to add a calendar", "NO CALENDAR YET" in txt("#calbody").upper() and vis("#cal-add"), txt("#calbody"))
        # ---- add a calendar in Settings (file + refused links)
        pg.evaluate("ATLAS.openSettings('general')"); pg.wait_for_selector("#cal-do")
        pg.fill("#cal-name", "Family"); pg.fill("#cal-val", "http://insecure.example.com/a.ics"); pg.click("#cal-do"); pg.wait_for_timeout(500)
        check("an http:// link is refused with a plain reason", "https" in txt("#cal-msg").lower(), txt("#cal-msg"))
        pg.fill("#cal-val", "https://192.168.1.10/private.ics"); pg.click("#cal-do"); pg.wait_for_timeout(500)
        check("a link to a private network address is refused", "network" in txt("#cal-msg").lower(), txt("#cal-msg"))
        pg.fill("#cal-name", "Family"); pg.fill("#cal-val", str(ics)); pg.click("#cal-do"); pg.wait_for_selector(".calsrc"); pg.wait_for_timeout(600)
        check("an .ics file is added and read (3 events)", "Family" in txt("#calblk") and "3 events" in txt("#calblk"), txt("#calblk"))
        check("the API never exposes a secret value", "value" not in json.dumps(get("/api/calendar/sources")) and str(ics) not in json.dumps(get("/api/calendar/sources")))
        if SHOTS:
            pg.wait_for_timeout(300); pg.screenshot(path=str(DOCS / "settings-calendar.png"))
        pg.evaluate("ATLAS.closeSettings&&ATLAS.closeSettings()") ; pg.keyboard.press("Escape"); pg.wait_for_timeout(500)
        pg.evaluate("__hud.loadCalendar(true)"); pg.wait_for_timeout(900)
        body = txt("#calbody")
        check("Calendar panel lists today's and tomorrow's events", "Planning call" in body and "Dentist" in body and "Soccer practice" in body and "Room 4" in body, body)
        check("...the event happening now is highlighted", pg.evaluate("!!document.querySelector('#calbody .ev.cur')"))
        r = chat("what's my next event")
        check("'what's my next event' by chat", "Dentist" in r or "Planning call" in r or "Soccer practice" in r, r)
        r = chat("what do I have tomorrow"); check("'what do I have tomorrow' by chat", "Soccer practice" in r, r)
        r = chat("what calendars do I have"); check("'what calendars do I have'", "Family" in r, r)
        # ---- sports live scores
        r = chat("put the sports where the calendar is")
        check("'put the sports where the calendar is' swaps them", vis("#t-football") and pg.evaluate("window.__layout.R1") in ("sports", "calendar"), (r, pg.evaluate("window.__layout")))
        pg.evaluate("__hud.loadScores()"); pg.wait_for_timeout(700)
        sb = txt("#teamsbody")
        check("Sports panel shows the live game and the upcoming one", "JUVENTUS" in sb.upper() and "2–1" in sb and "LIVE" in sb and "SACRAMENTO KINGS" in sb.upper(), sb)
        check("...and the tag says Live", txt("#sptag").strip().lower() == "live", txt("#sptag"))
        chat("turn off live scores")
        check("'turn off live scores' is saved", prefs()["live_scores"] is False)
        chat("turn on live scores")
        # ---- brief
        r = chat("what panels can I use"); check("'what panels can I use' lists the new panels", "calendar" in r.lower() and "daily brief" in r.lower(), r)
        r = chat("put the daily brief where hermes is")
        check("daily brief goes into the Hermes slot", vis("#t-brief"), (r, pg.evaluate("window.__layout")))
        n = len(speaks); r = chat("give me my brief", wait=1800)
        check("'give me my brief' answers with weather/calendar text and speaks it", "Good" in r and "calendar" in r.lower() and len(speaks) > n, r)
        pg.wait_for_timeout(500); bb = txt("#briefbody")
        check("the Daily brief panel shows Calendar section + buttons", "CALENDAR" in bb.upper() and "Planning call" in bb and vis("#br-say") and vis("#br-ref"), bb)
        if SHOTS:
            pg.screenshot(path=str(DOCS / "panels-calendar-sports-brief.png"))
        # ---- morning brief
        r = chat("turn on the morning brief"); check("morning brief switch via chat", prefs()["brief_morning"] is True and prefs()["brief_last"] == "", r)
        hr = datetime.now().hour
        d1 = req("POST", "/api/brief/morning", {})[1]; d2 = req("POST", "/api/brief/morning", {})[1]
        if 4 <= hr < 13:
            check("morning brief is due once, then not again today", d1["due"] is True and "text" in d1 and d2["due"] is False, (d1.get("due"), d2))
        else:
            check("morning brief is not offered in the afternoon", d1["due"] is False and d2["due"] is False, d1)
        chat("turn off the morning brief")
        # ---- persistence
        pg.reload(); pg.wait_for_function("window.__hud&&__hud.state().online===true"); pg.wait_for_timeout(1800); pg.evaluate("__hud.SU.dismissed=true;__hud.closeSetup()")
        lay = pg.evaluate("window.__layout"); check("layout with the new panels survives a reload", "brief" in lay.values() and "sports" in lay.values(), lay)
        pg.wait_for_timeout(800); check("...and the Calendar/Brief panel content is restored", vis("#t-brief"))
        r = chat("remove the family calendar"); check("remove a calendar by voice", "disconnected" in r and get("/api/calendar/sources")["sources"] == [], r)
        r = chat("reset my layout"); check("'reset my layout' puts the original six back (new panels go to the shelf)", not vis("#t-brief") and vis("#t-scan"), r)
        check("no page errors", not errs, errs)
        b.close()
finally: e.down()
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
