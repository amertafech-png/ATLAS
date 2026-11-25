"""Google Calendar via Hermes' existing sign-in. Fake HTTP only: no Google, no network.

Scratch directories come from tempfile.mkdtemp under the system temp directory.
Cleanup deletes only that directory, and only after checking it is still inside
the temp directory and outside the home directory. Nothing here shells out.
"""
import asyncio
import io
import json
import os
import stat
import sys
import tempfile
import urllib.parse
import urllib.request
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import atlas_calendar as C  # noqa: E402
import atlas_google as G  # noqa: E402
import atlas_info as I  # noqa: E402
import atlas_settings as S  # noqa: E402

fails = []


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " :: %s" % (info,)))
    if not ok:
        fails.append(name)


def scratch():
    root = Path(tempfile.gettempdir()).resolve()
    home = Path.home().resolve()
    d = Path(tempfile.mkdtemp(prefix="atlas-gcal-")).resolve()
    if root not in d.parents or d == home or home in d.parents:
        raise SystemExit("refusing scratch dir")
    return d


def cleanup(d):
    d = Path(d).resolve()
    root = Path(tempfile.gettempdir()).resolve()
    home = Path.home().resolve()
    if root not in d.parents or d == home or home in d.parents:
        raise SystemExit("refusing to clean this path")
    for p in sorted(d.rglob("*"), reverse=True):
        if p.is_symlink() or p.is_file():
            p.unlink()
        elif p.is_dir():
            p.rmdir()
    d.rmdir()


SECRET = "atlas-test-client-secret"
ACCESS = "atlas-test-access"
REFRESH = "atlas-test-refresh"
CLIENT = "atlas-test-client.apps.googleusercontent.com"
CAL = "https://www.googleapis.com/auth/calendar.readonly"
GMAIL = "https://www.googleapis.com/auth/gmail.readonly"
TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 11, 20, 8, 0, tzinfo=TZ)


def token(scopes, expiry="2099-01-01T00:00:00Z", access=ACCESS):
    return {
        "type": "authorized_user", "client_id": CLIENT, "client_secret": SECRET,
        "refresh_token": REFRESH, "token": access, "token_uri": G.TOKEN_URL,
        "scopes": list(scopes), "expiry": expiry,
    }


def write_token(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class Http:
    def __init__(self, events=None, status=200):
        self.events = events if events is not None else []
        self.status = status
        self.calls = []
        self.posts = 0
        self.deny_once = False

    def __call__(self, method, url, headers, body, timeout=20):
        parts = urllib.parse.urlsplit(url)
        self.calls.append((method, parts.hostname, parts.path))
        if method == "POST":
            self.posts += 1
            return 200, {"access_token": "atlas-test-access-2", "expires_in": 3600, "scope": CAL + " " + GMAIL, "refresh_token": "atlas-test-refresh-2"}
        if self.deny_once:
            self.deny_once = False
            return 401, {"error": {"message": SECRET}}
        if self.status != 200:
            return self.status, {"error": {"status": "PERMISSION_DENIED", "message": SECRET}}
        if parts.path.endswith("/calendarList"):
            return 200, {"items": [{"id": "primary", "summary": "Adam", "selected": True}]}
        return 200, {"items": self.events}


EVENTS = [
    {"status": "confirmed", "summary": "Dentist", "location": "Room 4",
     "start": {"dateTime": "2026-11-20T09:00:00-06:00"}, "end": {"dateTime": "2026-11-20T09:30:00-06:00"}},
    {"status": "confirmed", "summary": "Holiday", "start": {"date": "2026-11-20"}, "end": {"date": "2026-11-21"}},
    {"status": "confirmed", "summary": "Tomorrow thing", "start": {"dateTime": "2026-11-21T15:00:00-06:00"}, "end": {"dateTime": "2026-11-21T16:00:00-06:00"}},
    {"status": "cancelled", "summary": "Cancelled", "start": {"dateTime": "2026-11-20T11:00:00-06:00"}, "end": {"dateTime": "2026-11-20T12:00:00-06:00"}},
]


def quiet(fn):
    buf = io.StringIO()
    with redirect_stdout(buf):
        out = fn()
    return out, buf.getvalue()


# ---- where Hermes keeps the Google sign-in (no files are created on those paths)
homes = G.candidate_homes({"HERMES_HOME": r"D:\hermes-home", "LOCALAPPDATA": r"C:\Users\Adam\AppData\Local"}, home=Path(r"C:\Users\Adam"), windows=True)
check("windows homes: HERMES_HOME, then LOCALAPPDATA\\hermes, then .hermes",
      [str(p).replace("/", "\\") for p in homes] == [r"D:\hermes-home", r"C:\Users\Adam\AppData\Local\hermes", r"C:\Users\Adam\.hermes"], homes)
homes = G.candidate_homes({}, home=Path("/Users/adam"), windows=False)
check("mac/linux home is ~/.hermes when HERMES_HOME is unset", homes == [Path("/Users/adam/.hermes")], homes)

try:
    G.default_http("GET", "https://evil.example/x?" + ACCESS, {"Authorization": "Bearer " + ACCESS}, None)
    check("non-google host is refused", False)
except G.GoogleError as e:
    check("non-google host is refused before any request", "isn't to Google" in str(e) and ACCESS not in str(e) and SECRET not in str(e), str(e))
try:
    G.default_http("GET", "http://www.googleapis.com/calendar/v3/users/me/calendarList", {}, None)
    check("plain http to Google is refused", False)
except G.GoogleError as e:
    check("plain http to Google is refused", "isn't to Google" in str(e), str(e))

d = scratch()
try:
    hermes = d / "hermes"
    atlas = d / "atlas"
    write_token(hermes / "google_token.json", token([GMAIL]))
    before = (hermes / "google_token.json").read_bytes()
    http = Http()
    g = G.GoogleCalendar(atlas, homes=[hermes], http=http)
    st, log = quiet(lambda: g.status())
    check("mail-only token is not a calendar connection", st["email"] and not st["connected"] and st["needs_connect"] and not http.calls, st)
    check("status never includes the secret or the token", SECRET not in json.dumps(st) and ACCESS not in json.dumps(st) and REFRESH not in json.dumps(st) and SECRET not in log, log)
    check("hermes token file is not rewritten just by looking", (hermes / "google_token.json").read_bytes() == before)

    # calendar scope: read events, refresh in memory only
    write_token(hermes / "google_token.json", token([GMAIL, CAL], expiry="2000-01-01T00:00:00Z"))
    before = (hermes / "google_token.json").read_bytes()
    http = Http(EVENTS)
    g = G.GoogleCalendar(atlas, homes=[hermes], http=http)
    start = datetime(2026, 11, 20, tzinfo=TZ)
    evs, log = quiet(lambda: g.events(start, start + timedelta(days=1)))
    titles = [e["title"] for e in evs]
    check("today's Google events, cancelled dropped, tomorrow excluded", titles == ["Holiday", "Dentist"] and evs[1]["location"] == "Room 4" and evs[0]["all_day"] and not evs[1]["all_day"], titles)
    check("expired token is refreshed once, and the log stays empty", http.posts == 1 and http.calls[0][1] == "oauth2.googleapis.com" and SECRET not in log and ACCESS not in log, log)
    check("Hermes' token file is left untouched (refresh stays in memory)", (hermes / "google_token.json").read_bytes() == before)
    n = len(http.calls)
    g.events(start, start + timedelta(days=1))
    check("a second read uses the cache", len(http.calls) == n)
    g.events(start, start + timedelta(days=1), force=True)
    check("refresh=force asks Google again", len(http.calls) > n)

    http = Http(status=403)
    g = G.GoogleCalendar(atlas, homes=[hermes], http=http)
    write_token(hermes / "google_token.json", token([GMAIL, CAL]))
    try:
        g.events(start, start + timedelta(days=1), force=True)
        check("missing calendar permission is explained", False)
    except G.GoogleError as e:
        check("missing calendar permission is explained without the secret", "connect Google" in str(e) and SECRET not in str(e), str(e))

    # one-time connect reuses the client, stores the token in ATLAS's folder
    write_token(hermes / "google_token.json", token([GMAIL]))
    before = (hermes / "google_token.json").read_bytes()
    http = Http()

    def opener(url):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        redir, state = q["redirect_uri"][0], q["state"][0]
        check("auth url is the Google account page and carries no secret", url.startswith("https://accounts.google.com/") and SECRET not in url and REFRESH not in url)
        with urllib.request.urlopen(redir + "?code=atlas-test-code&state=" + urllib.parse.quote(state), timeout=5) as res:
            page = res.read().decode("utf-8")
        check("the browser page does not echo the code", "atlas-test-code" not in page and "connected" in page.lower(), page)
        return True, "opened"

    g = G.GoogleCalendar(atlas, homes=[hermes], http=http, open_url=opener)
    result, log = quiet(lambda: g.connect(timeout=8))
    saved = atlas / "google_token.json"
    check("connect succeeds and says the permission stays on this computer", result.get("connected") and "same Google app" in result.get("message", "") and "today" in result["message"], result.get("message"))
    check("connect message has no secret", SECRET not in result.get("message", "") and ACCESS not in result.get("message", "") and SECRET not in log)
    check("token is stored in the ATLAS data folder", saved.is_file() and not (hermes / "google_token.json").read_bytes() != before)
    check("Hermes token bytes unchanged", (hermes / "google_token.json").read_bytes() == before)
    mode = stat.S_IMODE(saved.stat().st_mode)
    check("saved token is user-readable only", mode == 0o600, oct(mode))
    body = json.loads(saved.read_text(encoding="utf-8"))
    check("saved token can read the calendar and still lists mail", G.has_calendar(body["scopes"]) and G.has_email(body["scopes"]) and body["client_id"] == CLIENT)
    check("after connect, calendar questions can use it", g.connected() and g.status()["connected"] and SECRET not in json.dumps(g.status()))
    check("no temp token file left behind", not (atlas / "google_token.json.tmp").exists())

    def closed(url):
        return False, "no browser"

    g2 = G.GoogleCalendar(atlas / "other", homes=[hermes], http=Http(), open_url=closed)
    missed = g2.connect(timeout=3)
    check("a browser that didn't open is explained", not missed["connected"] and "browser" in missed["message"] and not (atlas / "other" / "google_token.json").exists(), missed.get("message"))

    def deny(url):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        urllib.request.urlopen(q["redirect_uri"][0] + "?error=access_denied&state=" + urllib.parse.quote(q["state"][0]), timeout=5).read()
        return True, "opened"

    g3 = G.GoogleCalendar(atlas / "denied", homes=[d / "empty-hermes"], http=Http(), open_url=deny)
    write_token(Path(g3.homes[0]) / "google_client_secret.json", {"installed": {"client_id": CLIENT, "client_secret": SECRET}})
    denied = g3.connect(timeout=5)
    check("a denied consent is explained and nothing is saved", not denied["connected"] and "didn't grant" in denied["message"] and not (atlas / "denied" / "google_token.json").exists(), denied.get("message"))
finally:
    cleanup(d)

# ---- spoken answers
class Env:
    def __init__(self, google):
        self.cfg = {"profile": {"name": "Adam", "sports": []}, "prefs": {}}
        self.calendar = C.Calendar(self.cfg, lambda p: None, lambda: "America/Chicago", lambda src: "", lambda: 1000.0)
        self.google = google
        self.scores = None
        self.now = lambda: NOW
    def assistant_name(self):
        return "ATLAS"

d = scratch()
try:
    hermes = d / "hermes"
    atlas = d / "atlas"
    write_token(hermes / "google_token.json", token([GMAIL, CAL]))
    http = Http(EVENTS)
    g = G.GoogleCalendar(atlas, homes=[hermes], http=http)
    env = Env(g)
    reply = asyncio.run(I.Info(env).handle("what's on my calendar"))["reply"]
    check("today is answered from Google Calendar", "Dentist at 9 AM" in reply and "Holiday, all day" in reply and "Tomorrow" not in reply, reply)
    nxt = asyncio.run(I.Info(env).handle("what's my next meeting"))["reply"]
    check("next meeting is the real timed event", nxt == "Your next event is Dentist today at 9 AM in Room 4.", nxt)
    write_token(hermes / "google_token.json", token([GMAIL]))
    g = G.GoogleCalendar(atlas, homes=[hermes], http=Http())
    spoken = asyncio.run(I.Info(Env(g)).handle("what's on my calendar tomorrow"))["reply"]
    check("mail-only sign-in tells Adam to connect Google, and does not invent events",
          "haven't connected a calendar" in spoken and "connect Google" in spoken and "Settings, then Calendar" in spoken and "Dentist" not in spoken, spoken)
    ctx = I.Info(Env(g)).calendar_context("summarize my meetings")
    check("Hermes is told to say connect Google and not to start a sign-in", "connect Google" in ctx and "Do not start a Google sign-in" in ctx and SECRET not in ctx, ctx)
    check("connect google is answered by ATLAS", I.Info(Env(None)).parse("connect google") == {"op": "google_connect"})
    check("connect my google calendar still parses as a calendar connect", (I.Info(Env(None)).parse("can you reconnect my google calendar") or {}).get("op") == "calendar_connect")

    class Fake:
        def __init__(self):
            self.n = 0
        def connected(self):
            return False
        def needs_connect(self):
            return True
        def status(self):
            return {"connected": False, "email": True, "needs_connect": True, "hint": "Email sign-in found, calendar not included yet"}
        def connect(self):
            self.n += 1
            return {"connected": True, "message": "Google Calendar is connected. I used the same Google app as your email, and the permission stays only on this computer. Ask me what's on today."}
        def events(self, start, end, force=False):
            return []
    fake = Fake()
    done = asyncio.run(I.Info(Env(fake)).handle("connect google"))["reply"]
    check("saying connect Google runs the one-time step", fake.n == 1 and "same Google app" in done, done)
finally:
    cleanup(d)


class Feat:
    def describe(self):
        return []


class SetEnv:
    cfg = {"profile": {}, "assistant": {"name": "ATLAS"}, "prefs": {}, "tts": {}}
    features = Feat()

    def assistant_name(self):
        return "ATLAS"


check("settings does not treat connect Google as an AI provider", S.Settings(SetEnv()).parse("connect google") is None)
check("settings leaves connect-another and the account list to the calendar",
      S.Settings(SetEnv()).parse("connect another google account") is None and S.Settings(SetEnv()).parse("what google accounts are connected") is None)

# ---- several Google accounts, still no network (fake HTTP only)
d = scratch()
try:
    hermes = d / "hermes"
    atlas = d / "atlas"
    write_token(atlas / "google_token.json", token([CAL, GMAIL], access="atlas-test-access-legacy"))
    legacy_before = (atlas / "google_token.json").read_bytes()
    http = Http(EVENTS)
    g = G.GoogleCalendar(atlas, homes=[hermes], http=http)
    st, log = quiet(lambda: g.status())
    check("an existing single token is already connected, with no new sign-in",
          st["connected"] and st["accounts"] and st["accounts"][0]["id"] == "google" and not http.calls, st)
    check("migration does not rewrite the existing token or log it",
          (atlas / "google_token.json").read_bytes() == legacy_before and SECRET not in log and "atlas-test-access-legacy" not in log and SECRET not in json.dumps(st))
    start = datetime(2026, 11, 20, tzinfo=TZ)
    evs, log = quiet(lambda: g.events(start, start + timedelta(days=1)))
    check("the migrated token still answers today", [e["title"] for e in evs] == ["Holiday", "Dentist"] and SECRET not in log, [e["title"] for e in evs])
    check("the migrated token file is still the one on disk", (atlas / "google_token.json").read_bytes() == legacy_before)

    # Hermes calendar token is the first account when ATLAS has not saved its own yet
    write_token(hermes / "google_token.json", token([GMAIL, CAL], access=ACCESS))
    hermes_before = (hermes / "google_token.json").read_bytes()
    bare = d / "bare"
    http = Http([EVENTS[0]])

    class TwoHttp(Http):
        def __call__(self, method, url, headers, body, timeout=20):
            parts = urllib.parse.urlsplit(url)
            self.calls.append((method, parts.hostname, parts.path))
            auth = str((headers or {}).get("Authorization") or "")
            bearer = auth.split()[-1] if auth else ""
            if method == "POST":
                self.posts += 1
                return 200, {"access_token": "atlas-test-access-2", "expires_in": 3600, "scope": CAL, "refresh_token": "atlas-test-refresh-2"}
            email, events = ("adam@gmail.com", [EVENTS[0]]) if bearer == ACCESS else ("work@gmail.com", [{
                "status": "confirmed", "summary": "Payroll", "location": "Office",
                "start": {"dateTime": "2026-11-20T10:00:00-06:00"}, "end": {"dateTime": "2026-11-20T11:00:00-06:00"},
            }])
            if parts.path.endswith("/calendarList"):
                return 200, {"items": [{"id": email, "summary": email, "primary": True, "selected": True}]}
            return 200, {"items": events}

    two = TwoHttp()

    def opener(url):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        check("another account asks Google to show the account picker and carries no secret",
              "select_account" in q.get("prompt", [""])[0] and SECRET not in url and REFRESH not in url and ACCESS not in url, q.get("prompt"))
        with urllib.request.urlopen(q["redirect_uri"][0] + "?code=atlas-test-code-2&state=" + urllib.parse.quote(q["state"][0]), timeout=5) as res:
            page = res.read().decode("utf-8")
        check("the second sign-in page does not echo the code", "atlas-test-code-2" not in page)
        return True, "opened"

    g = G.GoogleCalendar(bare, homes=[hermes], http=two, open_url=opener)
    check("Hermes calendar token is the first account", [a["id"] for a in g.public_accounts()] == ["hermes"] and g.public_accounts()[0]["kind"] == "hermes")
    # silence an unused helper from the single-account cases
    del http
    added, log = quiet(lambda: g.connect(another=True, timeout=8))
    saved = list((bare / "google_accounts").glob("g*.json")) if (bare / "google_accounts").is_dir() else []
    check("connect another stores a second token only in the ATLAS data folder",
          added.get("connected") and len(saved) == 1 and not (bare / "google_token.json").exists() and "work@gmail.com" in added.get("message", ""), added.get("message"))
    check("Hermes token bytes unchanged after the second account", (hermes / "google_token.json").read_bytes() == hermes_before)
    check("the second token is user-readable only", saved and stat.S_IMODE(saved[0].stat().st_mode) == 0o600, oct(stat.S_IMODE(saved[0].stat().st_mode)) if saved else "missing")
    manifest = json.loads((bare / "google_accounts.json").read_text(encoding="utf-8"))
    dumped = json.dumps(g.status()) + json.dumps(manifest) + (added.get("message") or "") + log
    check("account list and manifest have labels, not secrets",
          SECRET not in dumped and REFRESH not in dumped and "atlas-test-access" not in dumped and "atlas-test-refresh" not in dumped, dumped)
    merged = g.events(start, start + timedelta(days=1), force=True)
    ids = [a["id"] for a in g.public_accounts()]
    check("Hermes stays first and both labels are the account emails",
          ids[0] == "hermes" and [a["label"] for a in g.public_accounts()] == ["adam@gmail.com", "work@gmail.com"], g.public_accounts())
    titles = [(e["title"], e.get("account")) for e in merged]
    check("today merges both accounts and keeps each label", titles == [("Dentist", "adam@gmail.com"), ("Payroll", "work@gmail.com")], titles)
    env = Env(g)
    spoken = asyncio.run(I.Info(env).handle("what's on my calendar"))["reply"]
    check("a single-account phrasing names each account when two are connected",
          "Dentist (adam@gmail.com) at 9 AM" in spoken and "Payroll (work@gmail.com) at 10 AM" in spoken and SECRET not in spoken, spoken)
    nxt = asyncio.run(I.Info(env).handle("what's my next meeting"))["reply"]
    check("the next meeting names the account it belongs to", "Dentist (adam@gmail.com)" in nxt and "work@" not in nxt, nxt)
    listed = asyncio.run(I.Info(env).handle("what google accounts are connected"))["reply"]
    check("listing accounts says both labels and no secret", "adam@gmail.com" in listed and "work@gmail.com" in listed and SECRET not in listed, listed)
    check("voice parses connect another, list, and remove",
          I.Info(env).parse("connect another google account") == {"op": "google_connect", "another": True}
          and I.Info(env).parse("what google accounts are connected")["op"] == "google_accounts"
          and I.Info(env).parse("remove the work google account") == {"op": "google_remove", "name": "work"})
    removed = asyncio.run(I.Info(env).handle("remove the work google account"))["reply"]
    left = list((bare / "google_accounts").glob("g*.json"))
    check("removing one account deletes only that token",
          "work@gmail.com" in removed and left == [] and (hermes / "google_token.json").read_bytes() == hermes_before and [a["label"] for a in g.public_accounts()] == ["adam@gmail.com"], removed)
    gone = g.remove("hermes")
    check("removing the Hermes account leaves its file in place",
          gone and gone["kind"] == "hermes" and (hermes / "google_token.json").read_bytes() == hermes_before and g.public_accounts() == [], gone)
    # one account: the spoken line stays unlabeled, matching the single-account replies above
    write_token(hermes / "google_token.json", token([GMAIL, CAL]))
    # skipped flag is set; a fresh object still sees the file but honors the skip
    g2 = G.GoogleCalendar(bare, homes=[hermes], http=Http(EVENTS))
    check("a removed Hermes account stays removed for the next launch", g2.public_accounts() == [] and (hermes / "google_token.json").is_file())
finally:
    cleanup(d)

print("FAILED: %s" % fails if fails else "ALL PASSED")
sys.exit(1 if fails else 0)
