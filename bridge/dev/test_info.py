"""Calendar / scores / brief questions: intent parsing + answers with a fake calendar, fake ESPN and fake weather (no network).   python bridge/dev/test_info.py"""
import asyncio, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import atlas_calendar as C, atlas_scores as SC, atlas_info as I  # noqa: E402
fails = []
def check(n, ok, info=""):
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else " :: %s" % (info,)))
    if not ok: fails.append(n)

TZ = ZoneInfo("America/Chicago")
NOW = datetime(2026, 11, 20, 8, 0, tzinfo=TZ)           # Friday
ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:1
DTSTART;TZID=America/Chicago:20261120T090000
DTEND;TZID=America/Chicago:20261120T093000
SUMMARY:Stand-up
LOCATION:Room 4
END:VEVENT
BEGIN:VEVENT
UID:2
DTSTART;TZID=America/Chicago:20261120T140000
DTEND;TZID=America/Chicago:20261120T150000
SUMMARY:Dentist
END:VEVENT
BEGIN:VEVENT
UID:3
DTSTART;TZID=America/Chicago:20261121T100000
DTEND;TZID=America/Chicago:20261121T110000
SUMMARY:Soccer practice
END:VEVENT
BEGIN:VEVENT
UID:4
DTSTART;TZID=America/Chicago:20261123T180000
DTEND;TZID=America/Chicago:20261123T190000
SUMMARY:Dinner with Mike
END:VEVENT
END:VCALENDAR
"""
def esp(eid, home, away, hs, as_, state, detail="", date="2026-11-20T01:00Z", completed=False):
    return {"id": eid, "date": date, "competitions": [{"status": {"type": {"state": state, "shortDetail": detail, "completed": completed}}, "competitors": [
        {"homeAway": "home", "score": hs, "team": {"displayName": home, "abbreviation": home[:3].upper()}, "winner": completed and int(hs or 0) > int(as_ or 0)},
        {"homeAway": "away", "score": as_, "team": {"displayName": away, "abbreviation": away[:3].upper()}, "winner": completed and int(as_ or 0) > int(hs or 0)}]}]}
def fake_espn(url, timeout=8):
    if "nba" in url:
        return {"events": [esp("n1", "Los Angeles Lakers", "Sacramento Kings", "", "", "pre", "7:30 PM", "2026-11-21T01:30Z")]}
    if "ita.1" in url:
        return {"events": [esp("s1", "Juventus", "Torino", "2", "1", "post", "FT", "2026-11-19T19:45Z", True)]}
    return {"events": []}

class Env:
    def __init__(self, with_cal=True, sports=True, live=True, weather=None):
        self.cfg = {"profile": {"name": "Adam", "city": "Dallas", "lat": 32.7, "lon": -96.8, "units": "imperial",
                                "sports": [{"sport": "Soccer", "leagues": ["Serie A"], "teams": ["Juventus"]}, {"sport": "NBA", "leagues": [], "teams": ["Lakers"]}] if sports else []},
                    "prefs": {"live_scores": live}}
        saved = []
        self.calendar = C.Calendar(self.cfg, lambda p: saved.append(p), lambda: "America/Chicago", lambda src: ICS, lambda: 1000.0)
        if with_cal:
            self.calendar.add("Home", "https://example.com/private/basic.ics") if False else self.cfg.setdefault("calendar", {}).__setitem__("sources", [{"id": "home", "name": "Home", "kind": "url", "value": "https://example.com/x/basic.ics"}])
        self.scores = SC.Scores(fake_espn, lambda: 1000.0)
        self.weather = (lambda prof: weather) if weather != "boom" else (lambda prof: (_ for _ in ()).throw(OSError("offline")))
        self.now = lambda: NOW
    def assistant_name(self): return "ATLAS"

W = {"temp": 61, "cond": "partly cloudy", "hi": 72, "lo": 50, "rain": 30, "unit": "degrees"}
def ask(env, text):
    return asyncio.run(I.Info(env).handle(text))
def parse(text): return I.Info(Env(weather=W)).parse(text)

# ---- parsing
for t in ["give me my brief", "give me my daily brief", "Hey ATLAS, give me my morning brief", "brief me", "what's my day look like", "read me my briefing", "daily brief please", "catch me up", "start my day"]:
    check("brief: %r" % t, (parse(t) or {}).get("op") == "brief", parse(t))
for t, when in [("what's on my calendar", "today"), ("what's on my calendar tomorrow", "tomorrow"), ("what do I have today", "today"), ("what do I have tomorrow", "tomorrow"),
                ("do I have anything on my schedule this week", "this week"), ("what's on my calendar on monday", "on monday"), ("show me my calendar", "today"), ("what's my schedule tonight", "tonight"),
                ("what's on my calendar this weekend", "this weekend")]:
    p = parse(t)
    check("calendar: %r -> %s" % (t, when), p and p["op"] == "calendar" and p["when"] == when, p)
for t in ["when is my next meeting", "what's my next event", "what's next on my calendar", "what's my next appointment"]:
    check("next: %r" % t, (parse(t) or {}).get("op") == "next", parse(t))
check("list calendars", (parse("what calendars do I have") or {}).get("op") == "calendars")
check("remove calendar", parse("remove the work calendar") == {"op": "calendar_remove", "name": "work"}, parse("remove the work calendar"))
for t, team in [("how did the Lakers do", "lakers"), ("how did Juventus play last night", "juventus"), ("what's the score of the Lakers game", "lakers"), ("did Juventus win", "juventus"), ("when do the Lakers play", "lakers"),
                ("how'd Arsenal do today", "arsenal"), ("what's the Lakers score", "lakers")]:
    p = parse(t)
    check("scores team: %r" % t, p and p["op"] == "scores" and p["team"] == team, p)
for t in ["what are the scores", "any games today", "who's playing tonight", "sports update", "what's the score"]:
    p = parse(t)
    check("scores all: %r" % t, p and p["op"] == "scores" and p["team"] == "", p)
for t in ["what's the weather", "play some music", "what is a calendar", "add a reminder", "how did you do that", "how did my day go", "tell me a joke about the brief of the day ahead", "open the calendar settings", "what time is it", "scanner", "whats the plan"]:
    check("not mine: %r" % t, parse(t) is None, parse(t))

# ---- answers
r = ask(Env(weather=W), "give me my brief")
tx = r["reply"]
check("brief greets by name + time of day", tx.startswith("Good morning, Adam."), tx)
check("brief has weather", "61 degrees and partly cloudy in Dallas" in tx and "high of 72" in tx and "30 percent chance of rain" in tx, tx)
check("brief has calendar", "2 things on your calendar today" in tx and "Stand-up at 9 AM in Room 4" in tx and "Dentist at 2 PM" in tx, tx)
check("brief has sports (yesterday result + tonight's game)", "Juventus beat Torino, 2 to 1." in tx and "Lakers play Sacramento Kings" in tx, tx)
check("brief returns sections", set(r["brief"]["sections"]) >= {"weather", "calendar", "sports"})
e = Env(weather=W); e.reminders_upcoming = lambda: ["call Mike at 3 PM"]
check("brief includes reminders when the feature exists", "1 reminder coming up: call Mike at 3 PM." in ask(e, "brief me")["reply"])
e.reminders_upcoming = lambda: []
check("brief says no reminders", "No reminders coming up." in ask(e, "brief me")["reply"])
tx = ask(Env(with_cal=False, sports=False, weather="boom"), "brief me")["reply"]
check("brief degrades: no weather, no calendar, no sports", "couldn't get the weather" in tx and "No calendar is connected" in tx and "Sports" not in tx, tx)
tx = ask(Env(live=False), "give me my brief")["reply"]
check("brief respects live-scores off", "switched off" in tx, tx)
check("brief without a city asks for it", "Set your city" in ask(Env(weather=None), "brief me")["reply"])

r = ask(Env(weather=W), "what's on my calendar")
check("calendar today lists remaining events", "Stand-up at 9 AM" in r["reply"] and "Dentist at 2 PM" in r["reply"] and "Soccer" not in r["reply"], r["reply"])
check("calendar tomorrow", "Soccer practice at 10 AM" in ask(Env(), "what do I have tomorrow")["reply"])
r = ask(Env(), "what's on my calendar this week")["reply"]
check("calendar this week labels days", "tomorrow: Soccer practice" in r and "Monday: Dinner with Mike at 6 PM" in r, r)
r = ask(Env(), "what's on my calendar on sunday")["reply"]
check("calendar empty day", r == "Nothing on Sunday on your calendar.", r)
check("next event", "Your next event is Stand-up today at 9 AM in Room 4." in ask(Env(), "what's my next event")["reply"], ask(Env(), "what's my next event")["reply"])
check("no calendar yet is explained", "haven't connected a calendar" in ask(Env(with_cal=False), "what's on my calendar")["reply"])
check("list calendars", "1 calendar: Home" in ask(Env(), "what calendars do I have")["reply"], ask(Env(), "what calendars do I have")["reply"])
e = Env(); r = ask(e, "remove the Home calendar")
check("remove calendar by name", "disconnected the home calendar" in r["reply"] and r["changed"] == ["calendar.sources"] and e.calendar.sources() == [], r)
check("remove unknown calendar", "don't have a calendar called nope" in ask(Env(), "remove the nope calendar")["reply"])

r = ask(Env(), "how did juventus do")["reply"]
check("score for one team", r == "Juventus beat Torino, 2 to 1.", r)
r = ask(Env(), "when do the lakers play")["reply"]
check("upcoming game for one team", r.startswith("Los Angeles Lakers play Sacramento Kings"), r)
r = ask(Env(), "how did arsenal do")["reply"]
check("unfollowed team explains how to follow", "don't follow arsenal" in r.lower(), r)
r = ask(Env(), "what are the scores")["reply"]
check("all followed scores", "Juventus beat Torino" in r and "Lakers play" in r, r)
check("no followed sports", "not following any sports" in ask(Env(sports=False), "what are the scores")["reply"])
check("scores off", "switched off" in ask(Env(live=False), "what are the scores")["reply"])
def down(url, timeout=8): raise OSError("offline")
e = Env(); e.scores = SC.Scores(down, lambda: 1000.0)
check("ESPN down: plain message, no crash", "unavailable right now" in ask(e, "what are the scores")["reply"], ask(e, "what are the scores")["reply"])
check("a normal question is not intercepted", ask(Env(), "tell me a story") is None)
print("FAILED: %s" % fails if fails else "ALL PASSED")
sys.exit(1 if fails else 0)
