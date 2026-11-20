"""ICS parsing, recurrence, sources (file + secret-link with an injected fetcher; no network).   python bridge/dev/test_calendar.py"""
import sys, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import atlas_calendar as C  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402
fails = []
def check(n, ok, info=""):
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else " :: %s" % (info,)))
    if not ok: fails.append(n)

TZ = ZoneInfo("America/Chicago")
def dt(y, m, d, h=0, mi=0): return datetime(y, m, d, h, mi, tzinfo=TZ)
ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:a1
DTSTART;TZID=America/Chicago:20261120T090000
DTEND;TZID=America/Chicago:20261120T093000
SUMMARY:Stand-up\\, team
LOCATION:Room 4
END:VEVENT
BEGIN:VEVENT
UID:a2
DTSTART:20261120T200000Z
DTEND:20261120T210000Z
SUMMARY:UTC call (2 pm Chicago)
END:VEVENT
BEGIN:VEVENT
UID:a3
DTSTART;VALUE=DATE:20261121
SUMMARY:Mike's birthday
END:VEVENT
BEGIN:VEVENT
UID:a4
DTSTART;TZID=America/Chicago:20261102T170000
DURATION:PT1H
SUMMARY:Gym
RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR
EXDATE;TZID=America/Chicago:20261120T170000
END:VEVENT
BEGIN:VEVENT
UID:a5
DTSTART;TZID=America/Chicago:20261101T080000
DTEND;TZID=America/Chicago:20261101T083000
SUMMARY:Daily five
RRULE:FREQ=DAILY;COUNT=5
END:VEVENT
BEGIN:VEVENT
UID:a6
DTSTART;TZID=America/Chicago:20261015T120000
DTEND;TZID=America/Chicago:20261015T130000
SUMMARY:Rent day
RRULE:FREQ=MONTHLY;BYMONTHDAY=15
END:VEVENT
BEGIN:VEVENT
UID:a7
DTSTART;TZID=America/Chicago:20261001T100000
DTEND;TZID=America/Chicago:20261001T110000
SUMMARY:Last Friday review
RRULE:FREQ=MONTHLY;BYDAY=-1FR
END:VEVENT
BEGIN:VEVENT
UID:a8
DTSTART;VALUE=DATE:20250214
SUMMARY:Anniversary
RRULE:FREQ=YEARLY
END:VEVENT
BEGIN:VEVENT
UID:a9
DTSTART;TZID=America/Chicago:20261120T150000
DTEND;TZID=America/Chicago:20261120T160000
SUMMARY:Cancelled thing
STATUS:CANCELLED
END:VEVENT
BEGIN:VEVENT
UID:b1
DTSTART;TZID=America/Chicago:20261125T100000
DTEND;TZID=America/Chicago:20261125T110000
SUMMARY:Long
 folded title
RRULE:FREQ=WEEKLY;INTERVAL=2;UNTIL=20261231T000000Z
END:VEVENT
END:VCALENDAR
"""
ev = C.parse_ics(ICS, TZ)
check("parses 9 events (the cancelled one is dropped)", len(ev) == 10 - 1, len(ev))
def day(y, m, d):
    return [(e["title"], e["occurs"].strftime("%H:%M")) for e in C.expand(ev, dt(y, m, d), dt(y, m, d) + timedelta(days=1))]
d20 = day(2026, 11, 20)
check("Nov 20: escaped comma, TZID time", ("Stand-up, team", "09:00") in d20, d20)
check("UTC time converted to Chicago (2 pm)", ("UTC call (2 pm Chicago)", "14:00") in d20, d20)
check("EXDATE removes that Friday's gym", not any(t == "Gym" for t, _ in d20), d20)
check("cancelled events never show", not any("Cancelled" in t for t, _ in d20))
check("Gym on Monday Nov 23 (BYDAY MO,WE,FR)", ("Gym", "17:00") in day(2026, 11, 23))
check("Gym not on Tuesday", not any(t == "Gym" for t, _ in day(2026, 11, 24)))
check("all-day event on the 21st", ("Mike's birthday", "00:00") in day(2026, 11, 21))
check("DAILY COUNT=5: Nov 5 yes, Nov 6 no", ("Daily five", "08:00") in day(2026, 11, 5) and not any(t == "Daily five" for t, _ in day(2026, 11, 6)))
check("MONTHLY BYMONTHDAY=15", ("Rent day", "12:00") in day(2026, 12, 15) and not any(t == "Rent day" for t, _ in day(2026, 12, 14)))
check("MONTHLY last Friday (-1FR): Nov 27", ("Last Friday review", "10:00") in day(2026, 11, 27) and not any(t == "Last Friday review" for t, _ in day(2026, 11, 20)))
check("YEARLY all-day (Feb 14, 2027)", any(t == "Anniversary" for t, _ in day(2027, 2, 14)))
check("folded long title + biweekly + UNTIL", ("Longfolded title", "10:00") in day(2026, 11, 25) or ("Long folded title", "10:00") in day(2026, 11, 25), day(2026, 11, 25))
check("biweekly skips the off week (Dec 2) and UNTIL stops it (Jan 6 2027)", not any("Long" in t for t, _ in day(2026, 12, 2)) and any("Long" in t for t, _ in day(2026, 12, 9)) and not any("Long" in t for t, _ in day(2027, 1, 6)))
ov = C.parse_ics("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:r\nDTSTART;TZID=America/Chicago:20261120T090000\nDTEND;TZID=America/Chicago:20261120T100000\nSUMMARY:Weekly\nRRULE:FREQ=WEEKLY\nEND:VEVENT\nBEGIN:VEVENT\nUID:r\nRECURRENCE-ID;TZID=America/Chicago:20261127T090000\nDTSTART;TZID=America/Chicago:20261127T130000\nDTEND;TZID=America/Chicago:20261127T140000\nSUMMARY:Weekly (moved)\nEND:VEVENT\nEND:VCALENDAR", TZ)
res = [(e["title"], e["occurs"].strftime("%H:%M")) for e in C.expand(ov, dt(2026, 11, 27), dt(2026, 11, 28))]
check("a moved occurrence (RECURRENCE-ID) replaces the original", res == [("Weekly (moved)", "13:00")], res)

# ---- sources
saved = []
cfg = {}
fetched = []
def fake_fetch(u):
    fetched.append(u)
    if "down" in u: raise OSError("boom https://secret-token-123")
    return ICS
now = [1000.0]
cal = C.Calendar(cfg, lambda p: saved.append(p), lambda: "America/Chicago", fake_fetch, lambda: now[0])
for bad in ("http://example.com/x.ics", "https://localhost/x.ics", "https://192.168.1.5/x.ics", "https://10.0.0.1/a", "notes.txt", "", "C:\\x\\cal.txt"):
    try: cal.add("x", bad); check("refuses %r" % bad, False)
    except ValueError: check("refuses %r" % bad, True)
sid = cal.add("Work", "webcal://calendar.example.com/private/SECRET-abc123/basic.ics")
check("webcal:// becomes https://", cal.sources()[0]["value"].startswith("https://calendar.example.com/"))
check("the secret link is never in the public view", "SECRET-abc123" not in str(cal.public_sources()) and "calendar.example.com" in str(cal.public_sources()))
evs = cal.events(dt(2026, 11, 20), dt(2026, 11, 21))
check("events come back with source name", any(e["source"] == "Work" and e["title"] == "Stand-up, team" for e in evs), evs[:2])
cal.events(dt(2026, 11, 20), dt(2026, 11, 21)); check("10 minute cache: one fetch", len(fetched) == 1, fetched)
now[0] += 700; cal.events(dt(2026, 11, 20), dt(2026, 11, 21)); check("after 10+ minutes it refetches", len(fetched) == 2)
cal.add("Dead", "https://down.example.com/s/TOKEN-zzz/cal.ics")
cal.events(dt(2026, 11, 20), dt(2026, 11, 21))
ps = {p["name"]: p for p in cal.public_sources()}
check("a failing link is reported gently, without the link or token", ps["Dead"]["ok"] is False and "TOKEN-zzz" not in ps["Dead"]["error"] and "secret-token-123" not in ps["Dead"]["error"] and ps["Dead"]["error"], ps["Dead"])
check("...and the others still work", ps["Work"]["ok"])
with tempfile.TemporaryDirectory() as td:
    f = Path(td) / "mine.ics"; f.write_text(ICS)
    cal.add("File", str(f)); cal.events(dt(2026, 11, 20), dt(2026, 11, 21), force=True)
    check("a local .ics file works", {p["name"]: p for p in cal.public_sources()}["File"]["ok"])
    (Path(td) / "junk.ics").write_text("hello"); cal.add("Junk", str(Path(td) / "junk.ics")); cal.events(dt(2026, 11, 20), dt(2026, 11, 21), force=True)
    check("a file that isn't a calendar is reported", "doesn't look like a calendar" in {p["name"]: p for p in cal.public_sources()}["Junk"]["error"])
check("sources are saved to config", saved and saved[-1]["calendar"]["sources"])
check("remove by name", cal.remove("dead") and not any(s["name"] == "Dead" for s in cal.sources()))
check("remove unknown -> False", cal.remove("nope") is False)
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
