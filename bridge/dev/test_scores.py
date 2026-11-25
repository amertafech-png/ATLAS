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
check("one league failing (Premier League timeout) does not break the others", r["error"] == "" and r["stale"] is False and r["checked"] == 2, (r["checked"], r["error"], r["stale"]))
check("a live followed game asks the panel to refresh quickly", r["refresh_seconds"] == S.LIVE_REFRESH, r["refresh_seconds"])
check("leagues were requested from the fixed ESPN host only", all(u.startswith(S.HOST) for u in calls) and all("evil" not in u for u in calls))
n = len(calls); sc.games([{"sport": "NBA", "teams": ["Utah Jazz"]}], date(2026, 11, 20)); check("quiet cache (no new request while the board is still fresh)", len(calls) == n)
nba_only = calls[n:]
check("a removed sport is not requested", nba_only == [] or all("basketball/nba" in u and "soccer" not in u and "football" not in u for u in nba_only), nba_only)
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

# ---- auto-refresh: calendar windows, live vs quiet cadence, keep last board when ESPN is down
def board(events, calendar):
    return {"events": events, "leagues": [{"calendar": calendar}]}
DAY_CAL = ["2026-11-16T12:00Z", "2026-11-20T12:00Z", "2026-11-23T12:00Z", "not-a-date", "https://evil.example/steal"]
OLD = {"events": [ev("old", ("Juventus", "JUV"), ("Torino", "TOR"), "2", "1", "post", "FT", "2026-11-16T19:00Z")]}
NXT = {"events": [ev("nxt", ("Juventus", "JUV"), ("AC Milan", "MIL"), "", "", "pre", "Sun 2:45 PM", "2026-11-23T19:45Z")]}
day_calls = []
def fetch_days(url):
    day_calls.append(url)
    if "dates=20261116" in url: return OLD
    if "dates=20261123" in url: return NXT
    if url.endswith("/scoreboard"): return board([], DAY_CAL)
    raise AssertionError("unexpected url " + url)
rd = S.Scores(fetch_days, now=lambda: 2000.0).games([{"sport": "Soccer", "leagues": ["Serie A"], "teams": ["Juventus"]}], date(2026, 11, 20))
ids = [g["id"] for g in rd["games"]]
check("a final from the previous match day still shows (not only yesterday)", "old" in ids and any(g["id"] == "old" and g["state"] == "post" for g in rd["games"]), ids)
check("the next match day is shown too", "nxt" in ids and any(g["state"] == "pre" for g in rd["games"] if g["id"] == "nxt"), ids)
check("calendar lookups stay on the ESPN host", all(u.startswith(S.HOST) and "evil" not in u for u in day_calls), day_calls)
check("nothing live -> slow refresh", rd["refresh_seconds"] == S.QUIET_REFRESH and rd["stale"] is False, rd["refresh_seconds"])
check("bad calendar text and a bad path add no url", S.followup_urls("../evil", board([], DAY_CAL), date(2026, 11, 20)) == [] and S.followup_urls("https://evil.example/x", board([], DAY_CAL), date(2026, 11, 20)) == [])

NFL_CAL = [{"label": "Regular Season", "value": "2", "entries": [
    {"label": "Week 11", "value": "11", "startDate": "2026-11-18T08:00Z"},
    {"label": "Week 12", "value": "12;drop", "startDate": "2026-11-25T08:00Z"},
    {"label": "Week 12", "value": "12", "startDate": "2026-11-25T08:00Z"}]},
    {"label": "Postseason", "value": "3", "entries": [{"label": "Wild Card", "value": "1", "startDate": "2027-01-13T08:00Z"}]}]
post_only = [ev("sun", ("Chicago Bears", "CHI"), ("New Orleans Saints", "NO"), "21", "13", "post", "Final", "2026-11-22T18:00Z")]
upcoming = {"events": [ev("wed", ("Green Bay Packers", "GB"), ("Los Angeles Rams", "LAR"), "", "", "pre", "Wed 8:00 PM", "2026-11-26T01:00Z"),
                       ev("sun2", ("Chicago Bears", "CHI"), ("Detroit Lions", "DET"), "", "", "pre", "Sun 1:00 PM", "2026-11-26T18:00Z")]}
check("next week is requested only once the current slate is all finals", S.followup_urls("football/nfl", board(post_only, NFL_CAL), date(2026, 11, 24)) == [S.board_url("football/nfl", "seasontype=2&week=12")])
check("a live or upcoming game on the current board does not fetch another week", S.followup_urls("football/nfl", board([ev("live", ("Chicago Bears", "CHI"), ("Detroit Lions", "DET"), "7", "3", "in", "Q2")], NFL_CAL), date(2026, 11, 22)) == [])
wk_calls = []
def fetch_week(url):
    wk_calls.append(url)
    if "week=12" in url: return upcoming
    if url.endswith("/scoreboard"): return board(post_only, NFL_CAL)
    return {"events": []}
rw = S.Scores(fetch_week, now=lambda: 3000.0).games([{"sport": "NFL", "teams": ["Bears"]}], date(2026, 11, 24))
got = sorted((g["id"], g["state"]) for g in rw["games"])
check("Tuesday still shows Sunday's final and Sunday's next game for a followed team", got == [("sun", "post"), ("sun2", "pre")], got)
check("a team you do not follow is left out of that week", not any(g["id"] == "wed" for g in rw["games"]))
check("removing every sport asks for nothing", S.Scores(lambda u: (_ for _ in ()).throw(AssertionError("network")), now=lambda: 1.0).games([], date(2026, 11, 20))["games"] == [])
asked = []
S.Scores(lambda u: asked.append(u) or {"events": []}, now=lambda: 1.0).games([{"sport": "NBA", "teams": ["Jazz"]}], date(2026, 11, 20))
check("only a sport you still follow is requested", asked and all("basketball/nba" in u and "soccer" not in u and "football" not in u for u in asked), asked)

clock = [4000.0]
live_n = {"n": 0}
def fetch_live(url):
    live_n["n"] += 1
    return {"events": [ev("L1", ("Los Angeles Lakers", "LAL"), ("Boston Celtics", "BOS"), "10", "8", "in", "Q2")]}
live_sc = S.Scores(fetch_live, now=lambda: clock[0])
rl = live_sc.games([{"sport": "NBA", "teams": ["Lakers"]}], date(2026, 11, 20))
check("live board refresh interval is 15 s", rl["refresh_seconds"] == 15 and rl["games"][0]["home"]["score"] == "10", rl["refresh_seconds"])
n_live = live_n["n"]; clock[0] += 14; live_sc.games([{"sport": "NBA", "teams": ["Lakers"]}], date(2026, 11, 20))
check("a live board is not fetched again before 15 s", live_n["n"] == n_live, live_n["n"])
clock[0] += 2; live_sc.games([{"sport": "NBA", "teams": ["Lakers"]}], date(2026, 11, 20))
check("a live board is fetched again after 15 s", live_n["n"] == n_live + 1, live_n["n"])

clock[0] = 5000.0
quiet_n = {"n": 0}
def fetch_quiet(url):
    quiet_n["n"] += 1
    return {"events": [ev("Q1", ("Utah Jazz", "UTA"), ("LA Clippers", "LAC"), "110", "105", "post", "Final")]}
quiet_sc = S.Scores(fetch_quiet, now=lambda: clock[0])
quiet_sc.games([{"sport": "NBA", "teams": ["Jazz"]}], date(2026, 11, 20))
n_quiet = quiet_n["n"]; clock[0] += 16; quiet_sc.games([{"sport": "NBA", "teams": ["Jazz"]}], date(2026, 11, 20))
check("a final-only board is not fetched again after 15 s", quiet_n["n"] == n_quiet, quiet_n["n"])
clock[0] += S.QUIET_REFRESH; quiet_sc.games([{"sport": "NBA", "teams": ["Jazz"]}], date(2026, 11, 20))
check("a final-only board is fetched again after 5 minutes", quiet_n["n"] == n_quiet + 1, quiet_n["n"])

clock[0] = 9000.0
down = {"fail": False}
def fetch_keep(url):
    if down["fail"]: raise OSError("offline")
    return {"events": [ev("K1", ("Los Angeles Lakers", "LAL"), ("Boston Celtics", "BOS"), "101", "99", "post", "Final")]}
keep = S.Scores(fetch_keep, now=lambda: clock[0])
keep.games([{"sport": "NBA", "teams": ["Lakers"]}], date(2026, 11, 20))
down["fail"] = True; clock[0] += S.QUIET_REFRESH + 1
rk = keep.games([{"sport": "NBA", "teams": ["Lakers"]}], date(2026, 11, 20))
check("ESPN down after a good read keeps the last score and marks it stale", rk["stale"] is True and rk["error"] == "" and rk["games"] and rk["games"][0]["home"]["score"] == "101" and rk["refresh_seconds"] == S.RETRY_REFRESH, rk)
clock[0] += S.RETRY_REFRESH + 1
rk2 = keep.games([{"sport": "NBA", "teams": ["Lakers"]}], date(2026, 11, 20))
check("a later failure still keeps that same score", rk2["stale"] is True and rk2["games"][0]["home"]["score"] == "101", rk2)
if "--live" in sys.argv:
    live = S.Scores().games([{"sport": "NBA", "teams": ["Lakers", "Celtics", "Jazz"]}, {"sport": "Soccer", "leagues": ["Serie A"], "teams": ["Juventus", "Como"]}])
    check("LIVE ESPN call returns without error (%d games)" % len(live["games"]), live["error"] == "" and live["checked"] >= 1, live)
    print([ (g["league"], g["home"]["name"], g["away"]["name"], g["state"]) for g in live["games"][:4]])
print("FAILED: %s" % fails if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
