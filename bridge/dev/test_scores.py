"""Scores: team matching, parsing, graceful failure (fixtures, no network).  python bridge/dev/test_scores.py [--live]"""
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import atlas_scores as S  # noqa: E402
fails = []
def check(n, ok, info=""):
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else " :: %s" % (info,)))
    if not ok: fails.append(n)
def ev(i, home, away, hs, as_, state, detail, date="2026-11-20T01:00Z", completed=None):
    mk = lambda n, ab, sc, ha: {"homeAway": ha, "score": sc, "winner": False, "team": {"displayName": n, "shortDisplayName": n.split()[-1], "abbreviation": ab, "location": n.rsplit(" ", 1)[0]}}
    return {"id": i, "date": date, "competitions": [{"date": date, "status": {"displayClock": "0'", "type": {"state": state, "completed": state == "post", "shortDetail": detail, "detail": detail}},
            "competitors": [mk(home[0], home[1], hs, "home"), mk(away[0], away[1], as_, "away")]}]}
SERIE_A = {"events": [ev("1", ("Juventus", "JUV"), ("Torino", "TOR"), "2", "1", "post", "FT"), ev("2", ("AS Roma", "ROM"), ("Atlético Madrid", "ATM"), "1", "0", "in", "67'"), ev("3", ("Como", "COM"), ("Cagliari", "CAG"), "0", "0", "pre", "Sat 9:00 AM")]}
NBA = {"events": [ev("9", ("Los Angeles Lakers", "LAL"), ("Boston Celtics", "BOS"), "101", "99", "post", "Final"), ev("10", ("Utah Jazz", "UTA"), ("LA Clippers", "LAC"), "", "", "pre", "7:00 PM")]}
calls = []
def fetch(url):
    calls.append(url)
    if "/ita.1/" in url: return SERIE_A
    if "/nba/" in url: return NBA
    if "/eng.1/" in url: raise TimeoutError("slow")
    return {"events": []}
sc = S.Scores(fetch, now=lambda: 1000.0)
r = sc.games([{"sport": "Soccer", "leagues": ["Serie A", "Premier League"], "teams": ["Juventus", "Roma", "Atletico Madrid", "Arsenal"]}, {"sport": "NBA", "leagues": [], "teams": ["Los Angeles Lakers", "Boston Celtics"]}], date(2026, 11, 20))
mine = sorted((g["mine"], g["state"]) for g in r["games"])
check("followed teams matched across accents / city prefixes (Roma = AS Roma, Atletico = Atlético)", ("Roma", "in") in mine and ("Juventus", "post") in mine and ("Los Angeles Lakers", "post") in mine, mine)
check("Atletico Madrid (no accent) matches 'Atlético Madrid'", S.matches("Atletico Madrid", {"displayName": "Atlético Madrid"}) and not S.matches("Madrid", {"displayName": "Real Betis"}))
check("games for teams you do not follow are not shown (Como v Cagliari)", not any(g["id"] == "3" for g in r["games"]))
check("a game between two followed teams (Lakers v Celtics) is listed once", sum(1 for g in r["games"] if g["id"] == "9") == 1)
check("live games sort first, finished last", r["games"][0]["state"] == "in" and r["games"][-1]["state"] == "post", [g["state"] for g in r["games"]])
check("one league failing (Premier League timeout) does not break the others", r["error"] == "" and r["checked"] == 4, r["checked"])
check("leagues were requested from the fixed ESPN host only", all(u.startswith(S.HOST) for u in calls))
n = len(calls); sc.games([{"sport": "NBA", "teams": ["Utah Jazz"]}], date(2026, 11, 20)); check("60 s cache (no new request for a repeated URL)", len(calls) == n)
r = S.Scores(lambda u: (_ for _ in ()).throw(OSError("offline")), now=lambda: 1.0).games([{"sport": "NFL", "teams": ["Chicago Bears"]}], date(2026, 11, 20))
check("everything offline -> a friendly error and no games", r["games"] == [] and "unavailable" in r["error"], r)
r = S.Scores(fetch).games([{"sport": "Cricket", "teams": ["India"]}, {"sport": "F1", "teams": []}], date(2026, 11, 20))
check("sports without a feed are listed as unsupported, not guessed", r["unsupported"] == ["Cricket", "F1"] and r["games"] == [])
r = S.Scores(lambda u: {"events": "garbage"}).games([{"sport": "NBA", "teams": ["Utah Jazz"]}], date(2026, 11, 20))
check("an unexpected response shape is tolerated", r["games"] == [])
try:
    S.fetch_json("https://evil.example.com/x"); check("fetch refuses other hosts", False)
except ValueError: check("fetch refuses other hosts", True)
g = SERIE_A["events"][0]; gg = S.parse_event(g, "Serie A")
check("speak: finished game", S.speak_game(gg, "Juventus") == "Juventus beat Torino, 2 to 1.", S.speak_game(gg, "Juventus"))
check("speak: lost game (from the other side)", "lost to Juventus" in S.speak_game(gg, "Torino"))
check("speak: live game", "playing now" in S.speak_game(S.parse_event(SERIE_A["events"][1], "Serie A"), "Roma"))
if "--live" in sys.argv:
    live = S.Scores().games([{"sport": "NBA", "teams": ["Lakers", "Celtics", "Jazz"]}, {"sport": "Soccer", "leagues": ["Serie A"], "teams": ["Juventus", "Como"]}])
    check("LIVE ESPN call returns without error (%d games)" % len(live["games"]), live["error"] == "" and live["checked"] >= 1, live)
    print([ (g["league"], g["home"]["name"], g["away"]["name"], g["state"]) for g in live["games"][:4]])
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
