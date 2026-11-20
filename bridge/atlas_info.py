"""Questions ATLAS answers itself, without sending them to Hermes: your calendar, your teams' scores, and the daily brief.
Info(env).handle(text) -> None (not for me) | {reply, changed, ui, pending, [data]}.
The brief is built here from local data (weather from Open-Meteo, your calendar, ESPN scores for teams you follow, your reminders) - no AI involved,
so it is fast, free and never invents anything."""
import asyncio
import json
import re
import urllib.request
from datetime import datetime, timedelta

import atlas_calendar as CAL
import atlas_scores as SC

WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 48: "foggy", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "rain showers", 81: "rain showers",
       82: "heavy showers", 85: "snow showers", 86: "snow showers", 95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail"}
NOT_TEAMS = {"it", "that", "this", "things", "everything", "you", "i", "we", "he", "she", "they", "day", "week", "meeting", "interview", "exam", "test", "trip", "date", "flight", "presentation", "doctor", "party", "game", "match", "team", "teams", "weather", "market", "stocks"}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def fetch_weather(profile):
    """Today's weather for the profile's city from Open-Meteo (free, no key).  Returns dict or raises."""
    lat, lon = profile.get("lat"), profile.get("lon")
    if lat is None or lon is None:
        return None
    imp = profile.get("units") == "imperial"
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&current=temperature_2m,weather_code&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
           "&temperature_unit=%s&timezone=auto&forecast_days=1" % (lat, lon, "fahrenheit" if imp else "celsius"))
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ATLAS-brief/1"}), timeout=8) as r:   # noqa: S310 (fixed https host)
        j = json.loads(r.read(200000).decode())
    cur, day = j.get("current") or {}, j.get("daily") or {}
    return {"temp": round(cur.get("temperature_2m")), "cond": WMO.get(cur.get("weather_code"), ""), "hi": round(day["temperature_2m_max"][0]), "lo": round(day["temperature_2m_min"][0]),
            "rain": (day.get("precipitation_probability_max") or [None])[0], "unit": "degrees"}


def clock(dt):
    s = dt.strftime("%I:%M %p").lstrip("0")
    return s.replace(":00 ", " ")


def day_label(d, today):
    delta = (d - today).days
    return "today" if delta == 0 else "tomorrow" if delta == 1 else d.strftime("%A")


class Info:
    def __init__(self, env):
        self.env = env
        self.pending = None

    # ---- data
    def name(self):
        return (self.env.cfg.get("profile") or {}).get("name") or ""

    def prefs(self):
        return self.env.cfg.get("prefs") or {}

    def live_scores_on(self):
        return bool(self.prefs().get("live_scores", True))

    def now(self):
        f = getattr(self.env, "now", None)
        return f() if f else self.env.calendar.now_dt()

    def calendar_lines(self, start, end, today):
        evs = self.env.calendar.events(start, end)
        out = []
        for e in evs:
            when = "all day" if e["all_day"] else "at " + clock(e["start"])
            out.append((e, e["title"] + (" " + when if not e["all_day"] else ", all day") + ((" in " + e["location"]) if e["location"] else "")))
        return out

    def scores(self):
        if not self.live_scores_on():
            return {"games": [], "error": "Live scores are switched off in Settings.", "unsupported": [], "checked": 0}
        sports = (self.env.cfg.get("profile") or {}).get("sports") or []
        return self.env.scores.games(sports, self.now().date())

    # ---- brief
    def brief(self, weather=None, force=False):
        env = self.env
        now = self.now()
        today = now.date()
        prof = env.cfg.get("profile") or {}
        hr = now.hour
        greet = "Good morning" if hr < 12 else "Good afternoon" if hr < 18 else "Good evening"
        name = self.name()
        sec = {}
        parts = ["%s%s." % (greet, (", " + name) if name else "")]
        # weather
        w = None
        try:
            w = (env.weather or fetch_weather)(prof)
        except Exception:  # noqa
            w = "err"
        city = prof.get("city") or ""
        if w == "err":
            sec["weather"] = "I couldn't get the weather right now."
        elif w is None:
            sec["weather"] = "Set your city in Settings and I'll add the weather."
        else:
            sec["weather"] = "It's %d %s%s%s, with a high of %d and a low of %d%s." % (w["temp"], w["unit"], (" and " + w["cond"]) if w["cond"] else "", (" in " + city) if city else "", w["hi"], w["lo"],
                                                                                    (", and a %d percent chance of rain" % w["rain"]) if (w.get("rain") or 0) >= 20 else "")
        parts.append(sec["weather"])
        # calendar
        if not env.calendar.sources():
            sec["calendar"] = "No calendar is connected yet. Add an .ics file or link in Settings."
        else:
            start = datetime.combine(today, datetime.min.time()).replace(tzinfo=now.tzinfo)
            rows = [(e, t) for e, t in self.calendar_lines(start, start + timedelta(days=1), today) if e["all_day"] or e["end"] > now]
            if not rows:
                sec["calendar"] = "Nothing else on your calendar today."
            else:
                sec["calendar"] = "You have %d thing%s on your calendar today: %s." % (len(rows), "" if len(rows) == 1 else "s", "; ".join(t for _e, t in rows[:6]) + ("; and more" if len(rows) > 6 else ""))
        parts.append(sec["calendar"])
        # sports
        sports = prof.get("sports") or []
        if not sports:
            sec["sports"] = ""
        else:
            sc = self.scores()
            if sc["error"]:
                sec["sports"] = sc["error"]
            elif not sc["games"]:
                sec["sports"] = "No games today or yesterday for the teams you follow."
            else:
                lines = [SC.speak_game(g) for g in sc["games"][:5]]
                sec["sports"] = "Sports: " + " ".join(lines)
        if sec.get("sports"):
            parts.append(sec["sports"])
        # reminders
        rem = env.reminders_upcoming() if getattr(env, "reminders_upcoming", None) else None
        if rem is not None:
            sec["reminders"] = ("You have %d reminder%s coming up: %s." % (len(rem), "" if len(rem) == 1 else "s", "; ".join(rem[:5]))) if rem else "No reminders coming up."
            parts.append(sec["reminders"])
        return {"text": " ".join(parts), "sections": sec, "generated": now.isoformat(timespec="seconds")}

    # ---- parsing
    def parse(self, text):
        t = re.sub(r"[\"!?;]+", " ", str(text or "").replace("\u2019", "'"))
        t = re.sub(r"\s+", " ", t).strip(" .,:").lower()
        t = re.sub(r"^(?:hey |ok |okay )?(?:%s)[ ,:]+" % re.escape((self.env.assistant_name() or "atlas").lower()), "", t)
        t = re.sub(r"^(?:please|can you|could you|would you|will you|just)\s+", "", t)
        t = re.sub(r"\s+(?:please|for me|now)$", "", t)
        if re.fullmatch(r"(?:give me|read me|tell me|play|read out|do|run|get|show me|what(?:'s| is)|start)?\s*(?:my |the |a )?(?:daily |morning |day )?(?:brief|briefing|brief me)(?: now| today)?|brief me|what(?:'s| is| does) my day (?:look like|looking like|like)|how(?:'s| is) my day (?:looking|shaping up)|what(?:'s| is) (?:going on )?today|catch me up|start my day", t):
            return {"op": "brief"}
        m = re.fullmatch(r"(?:what(?:'s| is| do i have| have i got| am i doing)|do i have (?:anything|something|any events|any meetings)|am i (?:busy|free)|show me|read me|read out|tell me|list|check|what are)?\s*(?:on )?(?:my |the )?(?:calendar|schedule|agenda|events|appointments|meetings)(?: (?:for )?(today|tomorrow|tonight|this week|this weekend|next week|on [a-z]+|[a-z]+day))?", t) \
            or re.fullmatch(r"(?:what|anything)(?: do i have| have i got| is on| is going on|'s on| is happening)? ?(today|tomorrow|tonight|this week|on [a-z]+day|[a-z]+day)", t) \
            or re.fullmatch(r"(?:what do i have|do i have anything|what's on|what is on|what am i doing) (today|tomorrow|tonight|this week|on [a-z]+day|[a-z]+day)", t)
        if m:
            return {"op": "calendar", "when": (m.group(1) or "today") if m.groups() else "today"}
        if re.fullmatch(r"(?:what(?:'s| is)|when(?:'s| is)|tell me|read me|show me)(?: about)? my next (?:meeting|event|appointment|calendar event|thing)|when(?:'s| is) my next (?:meeting|event|appointment)|what(?:'s| is) next on my (?:calendar|schedule)|what(?:'s| is) next", t):
            return {"op": "next"}
        m = re.fullmatch(r"(?:list|what are|which are|show|read)(?: me)?(?: all)?(?: my| the)? calendars|what calendars (?:do i have|are (?:connected|added|set up))", t)
        if m:
            return {"op": "calendars"}
        m = re.fullmatch(r"(?:remove|delete|disconnect|unlink|stop using) (?:my |the )?(.+?) calendar|(?:remove|delete|disconnect|unlink) (?:the )?calendar (?:called |named )?(.+)", t)
        if m:
            return {"op": "calendar_remove", "name": (m.group(1) or m.group(2)).strip()}
        if re.fullmatch(r"(?:what(?:'s| is| are)|read me|tell me|give me|show me)(?: me)?(?: the| my)? (?:live |latest |sports |sport )?(?:scores?|sports|results?)(?: today| now| so far)?|any (?:games|matches) (?:today|on today|tonight|on tonight|live)|(?:who(?:'s| is)|which teams are) playing(?: today| tonight| now)?|(?:are there|is there) (?:any )?(?:games?|matches?) (?:today|tonight|on)|what games (?:are )?(?:on|today|tonight)|sports update|score update", t):
            return {"op": "scores", "team": ""}
        m = re.fullmatch(r"(?:how did|how'd|how have|how are) (?:the |my )?(.+?) (?:do|did|play|played|go|get on|doing|fare|fared)(?: today| yesterday| last night| in their last game| lately)?", t) \
            or re.fullmatch(r"(?:what(?:'s| is| was)) the (?:latest |current |final |live )?score (?:of|for|in) (?:the |my )?(.+?)(?: game| match)?(?: today| now| last night)?", t) \
            or re.fullmatch(r"(?:did|do|does|is|are|when do|when does|when is|when are) (?:the |my )?(.+?) (?:win|lose|play|playing|win last night|play today|play tonight|play next|playing today|playing tonight|playing now|won|lost)(?: today| tonight| last night| next| now)?", t) \
            or re.fullmatch(r"(?:what(?:'s| is)) the (.+?) score", t) \
            or re.fullmatch(r"(?:score|results?) (?:for|of) (?:the |my )?(.+)", t)
        if m and m.group(1).strip() not in NOT_TEAMS and not re.search(r"\b(?:day|week|night|morning|weekend|you|your)$", m.group(1).strip()):
            return {"op": "scores", "team": m.group(1).strip()}
        return None

    # ---- executing
    async def handle(self, text):
        it = self.parse(text)
        if not it:
            return None
        loop = asyncio.get_running_loop()
        op = it["op"]
        if op == "brief":
            b = await loop.run_in_executor(None, self.brief)
            return {"reply": b["text"], "changed": [], "ui": None, "pending": False, "brief": b}
        if op in ("calendar", "next"):
            return await loop.run_in_executor(None, self._calendar_reply, it)
        if op == "calendars":
            ps = self.env.calendar.public_sources()
            return self._r("You have %d calendar%s: %s." % (len(ps), "" if len(ps) == 1 else "s", ", ".join(p["name"] for p in ps)) if ps else "No calendar is connected yet. Open Settings and add an .ics file or a calendar link.")
        if op == "calendar_remove":
            ok = self.env.calendar.remove(it["name"])
            return {"reply": ("Okay, I disconnected the %s calendar." % it["name"]) if ok else "I don't have a calendar called %s." % it["name"], "changed": ["calendar.sources"] if ok else [], "ui": None, "pending": False}
        if op == "scores":
            return await loop.run_in_executor(None, self._scores_reply, it)
        return None

    def _r(self, reply):
        return {"reply": reply, "changed": [], "ui": None, "pending": False}

    def _calendar_reply(self, it):
        cal = self.env.calendar
        if not cal.sources():
            return self._r("You haven't connected a calendar yet. Open Settings and add an .ics file or your calendar's secret link - no sign-in needed.")
        now = self.now()
        today = now.date()
        mid = lambda d: datetime.combine(d, datetime.min.time()).replace(tzinfo=now.tzinfo)
        if it["op"] == "next":
            evs = [e for e in cal.events(now, now + timedelta(days=30)) if not e["all_day"] and e["start"] >= now - timedelta(minutes=1)]
            if not evs:
                return self._r("Nothing coming up in the next 30 days.")
            e = evs[0]
            return self._r("Your next event is %s %s at %s%s." % (e["title"], day_label(e["start"].date(), today), clock(e["start"]), (" in " + e["location"]) if e["location"] else ""))
        when = it["when"]
        if when in ("today", "tonight"):
            days = [today]; label = "today" if when == "today" else "tonight"
        elif when == "tomorrow":
            days = [today + timedelta(days=1)]; label = "tomorrow"
        elif when in ("this week", "next week", "this weekend"):
            if when == "this week":
                days = [today + timedelta(days=i) for i in range(7)]
            elif when == "next week":
                days = [today + timedelta(days=7 + i) for i in range(7)]
            else:
                sat = today + timedelta(days=(5 - today.weekday()) % 7)
                days = [sat, sat + timedelta(days=1)]
            label = when
        else:
            wd = re.sub(r"^on ", "", when)
            if wd not in WEEKDAYS:
                return self._r("Which day? Say today, tomorrow, or a weekday.")
            d = today + timedelta(days=(WEEKDAYS.index(wd) - today.weekday()) % 7)
            days, label = [d], "on " + wd.capitalize()
        evs = cal.events(mid(days[0]), mid(days[-1]) + timedelta(days=1))
        if when in ("today",):
            evs = [e for e in evs if e["all_day"] or e["end"] > now]
        if when == "tonight":
            evs = [e for e in evs if not e["all_day"] and e["start"].hour >= 17]
        if not evs:
            return self._r("Nothing %s on your calendar." % label)
        multi = len(days) > 1
        lines = []
        for e in evs[:8]:
            lines.append("%s%s%s" % ((day_label(e["start"].date(), today) + ": ") if multi else "", e["title"], (", all day" if e["all_day"] else " at " + clock(e["start"]))))
        return self._r("%s: %d event%s. %s%s." % (label.capitalize(), len(evs), "" if len(evs) == 1 else "s", "; ".join(lines), ("; and %d more" % (len(evs) - 8)) if len(evs) > 8 else ""))

    def _scores_reply(self, it):
        sports = (self.env.cfg.get("profile") or {}).get("sports") or []
        if not sports:
            return self._r("You're not following any sports yet. Say follow soccer, or add teams in Settings.")
        if not self.live_scores_on():
            return self._r("Live scores are switched off. Say turn on live scores to use them.")
        sc = self.scores()
        if sc["error"]:
            return self._r(sc["error"])
        team = it.get("team") or ""
        games = sc["games"]
        if team:
            tf = SC.fold(team)
            mine = [g for g in games if tf and (tf in SC.fold(g["mine"]) or SC.fold(g["mine"]) in tf or SC.matches(team, {"displayName": g["home"]["name"]}) or SC.matches(team, {"displayName": g["away"]["name"]}))]
            if not mine:
                followed = any(tf in SC.fold(t) or SC.fold(t) in tf for e in sports for t in (e.get("teams") or []) if t)
                return self._r(("I don't see a game for %s today or yesterday." % team) if followed else "You don't follow %s yet. Say follow %s in soccer (or the right sport) and I'll watch for them." % (team, team))
            return self._r(" ".join(SC.speak_game(g, g["mine"]) for g in mine[:3]))
        if not games:
            return self._r("No games today or yesterday for the teams you follow." + ((" There's no live feed for %s." % ", ".join(sc["unsupported"])) if sc["unsupported"] else ""))
        return self._r(" ".join(SC.speak_game(g) for g in games[:5]))
