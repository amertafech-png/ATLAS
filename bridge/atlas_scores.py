"""Live scores for the teams you follow, from ESPN's public scoreboard JSON (site.api.espn.com - no account, no key).
It is an unofficial public endpoint: if it is slow, down, or changes shape, ATLAS says scores are unavailable and shows nothing made up.
Only the host site.api.espn.com is ever contacted, over https, and only to read; nothing about you is sent except the league path."""
import json
import re
import time
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

HOST = "https://site.api.espn.com/apis/site/v2/sports/"
CACHE_SECONDS = 60
SOCCER = {"premier league": ("eng.1", "Premier League"), "la liga": ("esp.1", "La Liga"), "serie a": ("ita.1", "Serie A"), "bundesliga": ("ger.1", "Bundesliga"),
          "ligue 1": ("fra.1", "Ligue 1"), "mls": ("usa.1", "MLS"), "champions league": ("uefa.champions", "Champions League"), "europa league": ("uefa.europa", "Europa League"),
          "eredivisie": ("ned.1", "Eredivisie"), "primeira liga": ("por.1", "Primeira Liga"), "liga mx": ("mex.1", "Liga MX"), "nwsl": ("usa.nwsl", "NWSL")}
SOCCER_DEFAULT = ["premier league", "la liga", "serie a", "bundesliga", "ligue 1", "mls", "champions league"]
PATHS = {"nfl": ("football/nfl", "NFL"), "nba": ("basketball/nba", "NBA"), "mlb": ("baseball/mlb", "MLB"), "nhl": ("hockey/nhl", "NHL"),
         "college football": ("football/college-football", "College football"), "wnba": ("basketball/wnba", "WNBA")}


def fold(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower().replace("&", " and "))).strip()


def fetch_json(url, timeout=8):
    if not url.startswith(HOST):
        raise ValueError("unexpected host")
    req = urllib.request.Request(url, headers={"User-Agent": "ATLAS-scores/1", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 (fixed https host)
        return json.loads(r.read(4_000_000).decode("utf-8", "replace"))


def league_paths(sport_entry):
    """[(url path, league label)] to ask for one followed sport."""
    sp = fold(sport_entry.get("sport"))
    if sp == "soccer":
        names = [fold(x) for x in (sport_entry.get("leagues") or [])]
        keys = [n for n in names if n in SOCCER] or SOCCER_DEFAULT
        return [("soccer/" + SOCCER[k][0], SOCCER[k][1]) for k in keys]
    if sp in PATHS:
        return [(PATHS[sp][0], PATHS[sp][1])]
    if sp in ("football",):
        return [(PATHS["nfl"][0], "NFL")]
    return []


def team_names(t):
    out = set()
    for k in ("displayName", "shortDisplayName", "name", "location", "abbreviation", "nickname"):
        v = fold(t.get(k))
        if v:
            out.add(v)
    return out


def matches(user_team, team):
    u = fold(user_team)
    if not u:
        return False
    names = team_names(team)
    if u in names:
        return True
    for n in names:
        if len(n) >= 4 and (re.search(r"\b%s\b" % re.escape(u), n) or re.search(r"\b%s\b" % re.escape(n), u)) and len(u) >= 4:
            return True
    return False


def parse_event(ev, league):
    comp = (ev.get("competitions") or [{}])[0]
    cps = comp.get("competitors") or []
    if len(cps) != 2:
        return None
    st = (comp.get("status") or ev.get("status") or {}).get("type") or {}
    def side(c):
        t = c.get("team") or {}
        sc = c.get("score")
        if isinstance(sc, dict):
            sc = sc.get("displayValue")
        return {"name": t.get("displayName") or t.get("name") or "?", "abbr": t.get("abbreviation") or "", "score": (str(sc) if sc not in (None, "") else ""), "winner": bool(c.get("winner")), "team": t}
    home = next((c for c in cps if c.get("homeAway") == "home"), cps[0])
    away = next((c for c in cps if c.get("homeAway") == "away"), cps[1])
    return {"id": ev.get("id"), "league": league, "state": st.get("state") or "pre", "detail": st.get("shortDetail") or st.get("detail") or "",
            "completed": bool(st.get("completed")), "start": ev.get("date") or comp.get("date") or "", "home": side(home), "away": side(away),
            "clock": (comp.get("status") or {}).get("displayClock", "")}


class Scores:
    def __init__(self, fetch=fetch_json, now=time.time):
        self.fetch, self.now = fetch, now
        self.cache = {}          # url -> (t, json|None, err)

    def _get(self, url):
        c = self.cache.get(url)
        if c and self.now() - c[0] < CACHE_SECONDS:
            return c[1], c[2]
        try:
            j, err = self.fetch(url), ""
        except Exception as e:  # noqa
            j, err = None, type(e).__name__
        self.cache[url] = (self.now(), j, err)
        return j, err

    def games(self, sports, today=None):
        """sports = profile.sports.  Returns {games:[...], error:'', unsupported:[sports], checked:int}.
        Each game: league, state(pre|in|post), detail, start, home/away {name,abbr,score,winner}, mine (the followed team's name), sport."""
        today = today or datetime.now(timezone.utc).date()
        days = [(today - timedelta(days=1)).strftime("%Y%m%d"), today.strftime("%Y%m%d")]      # ESPN rejects date ranges: one request per day
        jobs, unsupported = [], []
        for e in sports or []:
            paths = league_paths(e)
            if not paths:
                unsupported.append(e.get("sport"))
                continue
            for path, label in paths:
                for d in days:
                    jobs.append((e, path, label, d))
        results = []
        def run(job):
            e, path, label, d = job
            j, err = self._get(HOST + path + "/scoreboard?dates=" + d)
            return job, j, err
        with ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(run, jobs))
        games, errors, seen = [], 0, set()
        for (e, path, label, _d), j, err in results:
            if j is None:
                errors += 1
                continue
            teams = e.get("teams") or []
            evs = j.get("events") if isinstance(j, dict) else None
            for ev in (evs if isinstance(evs, list) else []):
                try:
                    g = parse_event(ev, label)
                except Exception:  # noqa  (an event in an unexpected shape is skipped, not fatal)
                    g = None
                if not g:
                    continue
                mine = None
                for ut in teams:
                    if matches(ut, g["home"]["team"]) or matches(ut, g["away"]["team"]):
                        mine = ut
                        break
                if teams and not mine:
                    continue
                if not teams and not e.get("leagues"):
                    continue
                key = (g["id"], mine)
                if key in seen:
                    continue
                seen.add(key)
                g["mine"] = mine or ""
                g["sport"] = e.get("sport")
                for side in ("home", "away"):
                    g[side].pop("team", None)
                games.append(g)
        order = {"in": 0, "pre": 1, "post": 2}
        games.sort(key=lambda g: (order.get(g["state"], 3), g["start"] if g["state"] != "post" else "~" + "".join(chr(255 - ord(c)) for c in g["start"])))
        err = ""
        if jobs and errors == len(jobs):
            err = "Live scores are unavailable right now (offline, or the score service didn't answer)."
        return {"games": games, "error": err, "unsupported": unsupported, "checked": len(jobs) - errors}


def speak_game(g, my_team=None):
    """One plain sentence about a game (used by voice and the daily brief)."""
    h, a = g["home"], g["away"]
    mine = my_team or g.get("mine") or h["name"]
    is_home = fold(mine) in fold(h["name"]) or fold(h["name"]) in fold(mine)
    me, opp = (h, a) if is_home else (a, h)
    if g["state"] == "post":
        try:
            ms, os_ = float(me["score"] or 0), float(opp["score"] or 0)
        except ValueError:
            ms = os_ = 0
        verb = "beat" if ms > os_ else "lost to" if ms < os_ else "drew with"
        return "%s %s %s, %s to %s." % (me["name"], verb, opp["name"], me["score"], opp["score"]) if verb != "drew with" else "%s drew with %s, %s all." % (me["name"], opp["name"], me["score"])
    if g["state"] == "in":
        return "%s and %s are playing now: %s %s, %s %s (%s)." % (me["name"], opp["name"], me["name"], me["score"], opp["name"], opp["score"], g["detail"] or "live")
    try:
        t = datetime.fromisoformat(g["start"].replace("Z", "+00:00")).astimezone()
        when = t.strftime("%a %-I:%M %p") if hasattr(t, "strftime") else ""
    except Exception:  # noqa
        when = g["detail"]
    return "%s play %s %s." % (me["name"], opp["name"], ("on " + when) if when else "soon")
