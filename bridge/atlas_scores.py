"""Scores for the teams you follow, from ESPN's public scoreboard JSON (site.api.espn.com - no account, no key).

The current scoreboard (no date) is what ESPN keeps fresh during a game. A second, small look at the league calendar
picks up the previous match day and the next one, so a final or a later fixture still shows after it has left
"yesterday and today". While a followed team is playing, the answer is reused for only a few seconds; otherwise it
is reused for several minutes. If ESPN does not answer, the last good scoreboard is kept and marked stale.
Only the host site.api.espn.com is ever contacted, over https, and only to read; nothing about you is sent except the league path.
"""
import json
import re
import time
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

HOST = "https://site.api.espn.com/apis/site/v2/sports/"
LIVE_REFRESH = 15          # a followed team is in a game: ESPN's own cache is about 10 seconds
QUIET_REFRESH = 300        # nothing live: finals and upcoming games do not move
RETRY_REFRESH = 30         # the last fetch failed: try again soon, but keep showing the previous board
SOCCER = {"premier league": ("eng.1", "Premier League"), "la liga": ("esp.1", "La Liga"), "serie a": ("ita.1", "Serie A"), "bundesliga": ("ger.1", "Bundesliga"),
          "ligue 1": ("fra.1", "Ligue 1"), "mls": ("usa.1", "MLS"), "champions league": ("uefa.champions", "Champions League"), "europa league": ("uefa.europa", "Europa League"),
          "eredivisie": ("ned.1", "Eredivisie"), "primeira liga": ("por.1", "Primeira Liga"), "liga mx": ("mex.1", "Liga MX"), "nwsl": ("usa.nwsl", "NWSL")}
SOCCER_DEFAULT = ["premier league", "la liga", "serie a", "bundesliga", "ligue 1", "mls", "champions league"]
PATHS = {"nfl": ("football/nfl", "NFL"), "nba": ("basketball/nba", "NBA"), "mlb": ("baseball/mlb", "MLB"), "nhl": ("hockey/nhl", "NHL"),
         "college football": ("football/college-football", "College football"), "wnba": ("basketball/wnba", "WNBA")}
_PATH_OK = re.compile(r"^[a-z0-9]+(?:[./-][a-z0-9]+)+$")
_YMD = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_NUM = re.compile(r"^\d{1,4}$")


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


def board_url(path, query=""):
    return HOST + path + "/scoreboard" + (("?" + query) if query else "")


def _ymd(value):
    m = _YMD.match(str(value or ""))
    return "%s%s%s" % m.groups() if m else ""


def _num(value):
    s = str(value or "").strip()
    return s if _NUM.match(s) else ""


def _event_state(ev):
    try:
        comp = (ev.get("competitions") or [{}])[0]
        st = (comp.get("status") or ev.get("status") or {}).get("type") or {}
    except Exception:  # noqa
        return ""
    return st.get("state") or ""


def _payload_live(payload):
    if not isinstance(payload, dict):
        return False
    evs = payload.get("events")
    if not isinstance(evs, list):
        return False
    return any(_event_state(ev) == "in" for ev in evs)


def followup_urls(path, payload, today):
    """At most two more scoreboard URLs, taken from the calendar ESPN sends with the current board.

    Day calendars (NBA, NHL, soccer, ...): the previous match day and the next one. That is how a Sunday
    final, or a game later in the week, still shows up — the old "yesterday and today" window dropped them.
    Week calendars (NFL, college football): the next week, and only when this board has no live or upcoming
    game (the current week is already on the undated board). Every URL stays on HOST; calendar text that is
    not a date or a week number is ignored.
    """
    if not _PATH_OK.match(path or "") or not isinstance(payload, dict):
        return []
    leagues = payload.get("leagues") or []
    if not leagues or not isinstance(leagues[0], dict):
        return []
    cal = leagues[0].get("calendar")
    if not isinstance(cal, list) or not cal:
        return []
    base = board_url(path)
    today_s = today.strftime("%Y%m%d")
    if isinstance(cal[0], str):
        days = sorted({d for d in (_ymd(x) for x in cal) if d})
        prev = max((d for d in days if d < today_s), default="")
        nxt = min((d for d in days if d > today_s), default="")
        return [base + "?dates=" + d for d in (prev, nxt) if d][:2]
    if not isinstance(cal[0], dict):
        return []
    evs = payload.get("events") if isinstance(payload.get("events"), list) else []
    if any(_event_state(ev) in ("in", "pre") for ev in evs):
        return []
    best = None
    for group in cal:
        if not isinstance(group, dict):
            continue
        season = _num(group.get("value"))
        if not season:
            continue
        for entry in group.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            week = _num(entry.get("value"))
            start = _ymd(entry.get("startDate"))
            if week and start and start >= today_s and (best is None or start < best[0]):
                best = (start, season, week)
    if not best:
        return []
    return [base + "?seasontype=%s&week=%s" % (best[1], best[2])]


def dated_fallback(path, today):
    """Used only when the current board could not be read: yesterday, today and tomorrow."""
    if not _PATH_OK.match(path or ""):
        return []
    days = [(today - timedelta(days=1)).strftime("%Y%m%d"), today.strftime("%Y%m%d"), (today + timedelta(days=1)).strftime("%Y%m%d")]
    return [board_url(path, "dates=" + d) for d in days]


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


def refresh_seconds(games, stale, error):
    if any(g.get("state") == "in" for g in games or []):
        return LIVE_REFRESH
    if stale or error:
        return RETRY_REFRESH
    return QUIET_REFRESH


class Scores:
    def __init__(self, fetch=fetch_json, now=time.time):
        self.fetch, self.now = fetch, now
        self.cache = {}          # url -> {t, json, err, stale, good, ttl}

    def _get(self, url):
        c = self.cache.get(url)
        if c and self.now() - c["t"] < c["ttl"]:
            return c["json"], c["err"], c["stale"]
        try:
            j = self.fetch(url)
            err, stale, good = "", False, j
        except Exception as e:  # noqa
            good = (c or {}).get("good")
            j, err, stale = good, type(e).__name__, good is not None
        ttl = RETRY_REFRESH if err else (LIVE_REFRESH if _payload_live(j) else QUIET_REFRESH)
        self.cache[url] = {"t": self.now(), "json": j, "err": err, "stale": stale, "good": good, "ttl": ttl}
        return j, err, stale

    def _fetch(self, jobs):
        if not jobs:
            return []
        def run(job):
            j, err, stale = self._get(job[3])
            return job, j, err, stale
        with ThreadPoolExecutor(max_workers=6) as ex:
            return list(ex.map(run, jobs))

    def games(self, sports, today=None):
        """sports = profile.sports.  Returns {games, error, unsupported, checked, stale, refresh_seconds}.
        Each game: league, state(pre|in|post), detail, start, home/away {name,abbr,score,winner}, mine (the followed team's name), sport.
        stale is true when a board could not be refreshed and the previous one is still being shown."""
        today = today or datetime.now(timezone.utc).date()
        jobs, unsupported = [], []
        for e in sports or []:
            paths = league_paths(e)
            if not paths:
                unsupported.append(e.get("sport"))
                continue
            for path, label in paths:
                jobs.append((e, path, label, board_url(path)))
        rows = self._fetch(jobs)
        seen_urls = {job[3] for job, _j, _e, _s in rows}
        extra = []
        for (e, path, label, _url), j, _err, _stale in rows:
            more = dated_fallback(path, today) if j is None else followup_urls(path, j, today)
            for url in more:
                if url not in seen_urls:
                    seen_urls.add(url)
                    extra.append((e, path, label, url))
        rows.extend(self._fetch(extra))
        games, errors, seen = [], 0, set()
        stale = False
        for (e, _path, label, _url), j, _err, was_stale in rows:
            if was_stale:
                stale = True
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
        if rows and errors == len(rows):
            err = "Live scores are unavailable right now (offline, or the score service didn't answer)."
            stale = False
        return {"games": games, "error": err, "unsupported": unsupported, "checked": len(rows) - errors,
                "stale": stale, "refresh_seconds": refresh_seconds(games, stale, err)}


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
