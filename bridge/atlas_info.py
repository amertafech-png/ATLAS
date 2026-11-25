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
import atlas_google
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


# ---- calendar questions in everyday words (v1.4.3) ----
CAL_WORDS = r"calendar|calendars|schedule|agenda|appointments?|meetings?|events?|plans|diary"
CAL_TOPIC = re.compile(r"\b(?:%s)\b|\bwhat(?:'s| is| was) (?:on|happening) (?:today|tomorrow|tonight|yesterday|this|next|last|on)\b|\bam i (?:busy|free)\b|\bwas i busy\b" % CAL_WORDS)
ASK_WORDS = re.compile(r"^(?:check|look at|look up|show|show me|read|read me|tell me|list|pull up|open|go through|give me|get|what|what's|whats|which|when|do i|did i|am i|was i|have i|any|anything|is there|are there|how many|how busy|how's|how is)\b")
RANGE_WORDS = (r"today|tonight|tomorrow|yesterday|this week|next week|last week|past week|the past week|previous week|the last (?:7|seven) days|the next (?:7|seven) days|"
               r"this weekend|next weekend|last weekend|this month|next month|last month|(?:last |next |this |on )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)")


def calendar_range(t, today):
    """(first_day, last_day_inclusive, label, past) for the time words in `t`; None if there are none."""
    t = re.sub(r"'s\b", "", t)
    m = re.search(r"\b(" + RANGE_WORDS + r")\b", t)
    if not m:
        return None
    w = re.sub(r"^the ", "", m.group(1))
    wd = lambda d: d.weekday()   # noqa: E731
    if w in ("today", "tonight"):
        return today, today, w, False
    if w == "tomorrow":
        return today + timedelta(days=1), today + timedelta(days=1), w, False
    if w == "yesterday":
        return today - timedelta(days=1), today - timedelta(days=1), w, True
    if w == "this week":
        return today, today + timedelta(days=6), w, False
    if w in ("next 7 days", "next seven days"):
        return today, today + timedelta(days=6), "the next 7 days", False
    if w == "next week":
        mon = today + timedelta(days=7 - wd(today))
        return mon, mon + timedelta(days=6), w, False
    if w in ("last week", "previous week"):
        mon = today - timedelta(days=wd(today) + 7)
        return mon, mon + timedelta(days=6), "last week", True
    if w in ("past week", "last 7 days", "last seven days"):
        return today - timedelta(days=7), today - timedelta(days=1), "the past 7 days", True
    if w in ("this weekend", "next weekend", "last weekend"):
        sat = today + timedelta(days=(5 - wd(today)) % 7)
        if w == "next weekend":
            sat += timedelta(days=7)
        if w == "last weekend":
            sat = today - timedelta(days=(wd(today) - 5) % 7 or 7)
        return sat, sat + timedelta(days=1), w, w == "last weekend"
    if w in ("this month", "next month", "last month"):
        first = today.replace(day=1)
        if w == "next month":
            first = (first + timedelta(days=32)).replace(day=1)
        elif w == "last month":
            first = (first - timedelta(days=1)).replace(day=1)
        last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        if w == "this month":
            first = today
        return first, last, w, w == "last month"
    parts = w.split()
    day = parts[-1]
    i = WEEKDAYS.index(day)
    if parts[0] == "last":
        d = today - timedelta(days=(wd(today) - i) % 7 or 7)
        return d, d, "last " + day.capitalize(), True
    d = today + timedelta(days=(i - wd(today)) % 7)
    if parts[0] == "next" and d == today:
        d += timedelta(days=7)
    return d, d, "on " + day.capitalize(), False


def is_calendar_topic(t):
    t = (t or "").lower().replace("\u2019", "'")
    return bool(CAL_TOPIC.search(t)) and not re.search(r"\bwhat (?:is|'s) an? (?:calendar|schedule|agenda)\b|\bcalendar (?:settings|app)\b|\bsettings\b", t)


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

    def _google(self):
        return getattr(self.env, "google", None)

    def calendar_ready(self):
        if self.env.calendar.sources():
            return True
        g = self._google()
        return bool(g is not None and g.connected())

    def events_between(self, start, end, force=False):
        """ICS sources plus Google Calendar. The error string is spoken only when Google was the source and it failed."""
        evs = []
        try:
            evs.extend(self.env.calendar.events(start, end, force))
        except Exception:
            evs = []
        err = ""
        g = self._google()
        if g is not None and g.connected():
            try:
                evs.extend(g.events(start, end, force=force))
            except atlas_google.GoogleError as e:
                err = str(e)
            except Exception:
                err = "I couldn't reach Google Calendar just now."
        seen, out = set(), []
        for e in sorted(evs, key=lambda e: (e["start"], e["title"])):
            key = (e.get("title"), e["start"].isoformat() if hasattr(e["start"], "isoformat") else e["start"], bool(e.get("all_day")))
            if key in seen:
                continue
            seen.add(key)
            out.append(e)
        self._google_err = err
        return out, err

    def calendar_lines(self, start, end, today):
        evs, _err = self.events_between(start, end)
        out = []
        for e in evs:
            when = "all day" if e["all_day"] else "at " + clock(e["start"])
            out.append((e, e["title"] + (" " + when if not e["all_day"] else ", all day") + ((" in " + e["location"]) if e["location"] else "")))
        return out

    def scores(self):
        if not self.live_scores_on():
            return {"games": [], "error": "Live scores are switched off in Settings.", "unsupported": [], "checked": 0, "stale": False, "refresh_seconds": SC.QUIET_REFRESH}
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
        if not self.calendar_ready():
            sec["calendar"] = "No calendar is connected yet. Add an .ics file or link in Settings."
        else:
            start = datetime.combine(today, datetime.min.time()).replace(tzinfo=now.tzinfo)
            rows = [(e, t) for e, t in self.calendar_lines(start, start + timedelta(days=1), today) if e["all_day"] or e["end"] > now]
            if not rows:
                sec["calendar"] = self._google_err or "Nothing else on your calendar today."
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
                sec["sports"] = "No recent or upcoming games for the teams you follow."
            else:
                lines = [SC.speak_game(g) for g in sc["games"][:5]]
                sec["sports"] = "Sports: " + " ".join(lines) + (" (may be out of date)" if sc.get("stale") else "")
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
        # "connect google": one-time browser sign-in that reuses Hermes' Google app. "reconnect my google calendar" stays on the broader rule below.
        if re.search(r"\bconnect google\b", t):
            return {"op": "google_connect"}
        # "reconnect my Google calendar": answer here, never via Hermes' connectors
        if re.search(r"\b(?:re-?connect|connect|link|sync|hook up|set up|authori[sz]e|sign in to|log in to)\b.*\b(?:google |outlook |icloud |my |the )?calendar\b|\bcalendar\b.*\b(?:not connected|isn't connected|disconnected|reconnect)\b", t):
            return {"op": "calendar_connect"}
        # everyday calendar questions: "check last week's calendar", "what meetings did I have yesterday", "am I free on Friday"
        if is_calendar_topic(t) and len(t.split()) <= 16 and ASK_WORDS.search(t) and not re.search(r"\b(?:add|create|schedule a|book|move|cancel|delete|remind|invite|email|send|why|how do i|how can i)\b", t):
            rng = calendar_range(t, self.now().date())
            return {"op": "calendar", "range": rng} if rng else {"op": "calendar", "when": "this week"}
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
        if op == "google_connect":
            return await self._connect_google()
        if op == "calendar_connect":
            if self.calendar_ready():
                return self._already_connected()
            g = self._google()
            if g is not None and g.needs_connect():
                return await self._connect_google()
            return self._r("I read your calendar from its secret calendar link, no sign-in needed. Open Settings, then Calendar, and paste the link from Google Calendar "
                           "(Settings and sharing, Secret address in iCal format) or an .ics file.")
        if op in ("calendar", "next"):
            return await loop.run_in_executor(None, self._calendar_reply, it)
        if op == "calendars":
            names = [p["name"] for p in self.env.calendar.public_sources()]
            g = self._google()
            if g is not None and g.connected():
                names.append("Google")
            return self._r("You have %d calendar%s: %s." % (len(names), "" if len(names) == 1 else "s", ", ".join(names)) if names else "No calendar is connected yet. Open Settings and add an .ics file or a calendar link.")
        if op == "calendar_remove":
            ok = self.env.calendar.remove(it["name"])
            return {"reply": ("Okay, I disconnected the %s calendar." % it["name"]) if ok else "I don't have a calendar called %s." % it["name"], "changed": ["calendar.sources"] if ok else [], "ui": None, "pending": False}
        if op == "scores":
            return await loop.run_in_executor(None, self._scores_reply, it)
        return None

    def _r(self, reply):
        return {"reply": reply, "changed": [], "ui": None, "pending": False}

    def _not_connected_reply(self):
        g = self._google()
        st = g.status() if g is not None else {}
        if st.get("email") and st.get("needs_connect"):
            return self._r("You haven't connected a calendar yet. I can already read your email with the Google sign-in on this computer, but that sign-in doesn't include your calendar. "
                           "Say connect Google and I'll open a one-time page in your browser. Or open Settings, then Calendar, and add an .ics file or a secret link.")
        if st.get("needs_connect"):
            return self._r("You haven't connected a calendar yet. Say connect Google and I'll open a one-time page in your browser. Or open Settings, then Calendar, and add an .ics file or a secret link.")
        return self._r("You haven't connected a calendar yet. Open Settings, then Calendar, and add an .ics file or your calendar's secret link - no sign-in needed.")

    def _already_connected(self):
        names = [p["name"] for p in self.env.calendar.public_sources()]
        g = self._google()
        google_on = g is not None and g.connected()
        if google_on:
            names.append("Google")
        if google_on and not self.env.calendar.sources():
            return self._r("Your calendar is already connected to me: Google. I read it with the same Google sign-in as your email, saved only on this computer. "
                           "Ask me what's on today, tomorrow or next. To add another calendar, open Settings, then Calendar.")
        return self._r("Your calendar is already connected to me: %s. I read it straight from the calendar link in Settings, so there's nothing to reconnect and no Google sign-in is needed. "
                       "Ask me what's on today, tomorrow or last week. To change the link, open Settings, then Calendar." % ", ".join(names))

    async def _connect_google(self):
        g = self._google()
        if g is not None and g.connected():
            return self._r("Google Calendar is already connected. I read it with the same Google sign-in as your email, saved only on this computer. Ask what's on today or tomorrow.")
        if g is None or not g.needs_connect():
            return self._r("I read your calendar from its secret calendar link, no sign-in needed. Open Settings, then Calendar, and paste the link from Google Calendar "
                           "(Settings and sharing, Secret address in iCal format) or an .ics file.")
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, g.connect)
        return self._r((result or {}).get("message") or "I couldn't finish connecting Google.")

    def _calendar_reply(self, it):
        if not self.calendar_ready():
            return self._not_connected_reply()
        now = self.now()
        today = now.date()
        mid = lambda d: datetime.combine(d, datetime.min.time()).replace(tzinfo=now.tzinfo)
        if it["op"] == "next":
            evs, err = self.events_between(now, now + timedelta(days=30))
            evs = [e for e in evs if not e["all_day"] and e["start"] >= now - timedelta(minutes=1)]
            if not evs:
                return self._r(err or "Nothing coming up in the next 30 days.")
            e = evs[0]
            return self._r("Your next event is %s %s at %s%s." % (e["title"], day_label(e["start"].date(), today), clock(e["start"]), (" in " + e["location"]) if e["location"] else ""))
        if it.get("range"):
            first, last, label, past = it["range"]
            days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
            evs, err = self.events_between(mid(first), mid(last) + timedelta(days=1))
            if label == "tonight":
                evs = [e for e in evs if not e["all_day"] and e["start"].hour >= 17]
            if not evs:
                return self._r(err or (("There was nothing on your calendar %s." if past else "Nothing %s on your calendar.") % label))
            multi = len(days) > 1
            lines = ["%s%s%s" % ((e["start"].strftime("%A %b ") + str(e["start"].day) + ": ") if multi else "", e["title"], (", all day" if e["all_day"] else " at " + clock(e["start"]))) for e in evs[:10]]
            return self._r("%s you %s %d event%s. %s%s." % (label[0].upper() + label[1:] + ",", "had" if past else "have", len(evs), "" if len(evs) == 1 else "s", "; ".join(lines),
                                                           ("; and %d more" % (len(evs) - 10)) if len(evs) > 10 else ""))
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
        evs, err = self.events_between(mid(days[0]), mid(days[-1]) + timedelta(days=1))
        if when in ("today",):
            evs = [e for e in evs if e["all_day"] or e["end"] > now]
        if when == "tonight":
            evs = [e for e in evs if not e["all_day"] and e["start"].hour >= 17]
        if not evs:
            return self._r(err or ("Nothing %s on your calendar." % label))
        multi = len(days) > 1
        lines = []
        for e in evs[:8]:
            lines.append("%s%s%s" % ((day_label(e["start"].date(), today) + ": ") if multi else "", e["title"], (", all day" if e["all_day"] else " at " + clock(e["start"]))))
        return self._r("%s: %d event%s. %s%s." % (label.capitalize(), len(evs), "" if len(evs) == 1 else "s", "; ".join(lines), ("; and %d more" % (len(evs) - 8)) if len(evs) > 8 else ""))

    def calendar_context(self, text):
        """Calendar facts to hand to Hermes with a calendar-related request ATLAS doesn't answer itself."""
        rule = ("Use ONLY these calendar events from ATLAS. Do not use Google, Microsoft or any other connectors or skills for the calendar, do not ask the user to sign in "
                "or reconnect, and never search the user's files for credentials or tokens. If something isn't in this list, say it isn't on the calendar ATLAS reads.")
        if not self.calendar_ready():
            g = self._google()
            st = g.status() if g is not None else {}
            if st.get("needs_connect"):
                return ("[ATLAS calendar] No calendar is connected to ATLAS yet. Email works through the Google sign-in already on this computer, but that sign-in does not include the calendar. "
                        "Tell the user to say connect Google. Do not start a Google sign-in, do not use connectors, and never search the user's files for credentials or tokens.")
            return ("[ATLAS calendar] No calendar is connected to ATLAS. Tell the user to open ATLAS Settings, then Calendar, and paste their calendar's secret iCal link "
                    "(Google Calendar: Settings and sharing, Secret address in iCal format). Do not try to connect or search for a calendar yourself.")
        now = self.now()
        today = now.date()
        rng = calendar_range(str(text or "").lower().replace("\u2019", "'"), today)
        first, last = (rng[0], rng[1]) if rng else (today - timedelta(days=7), today + timedelta(days=14))
        first, last = min(first, today - timedelta(days=1)), max(last, today + timedelta(days=1))
        mid = lambda d: datetime.combine(d, datetime.min.time()).replace(tzinfo=now.tzinfo)   # noqa: E731
        evs, gerr = self.events_between(mid(first), mid(last) + timedelta(days=1))
        lines = ["- %s %s: %s%s" % (e["start"].strftime("%a %Y-%m-%d"), "all day" if e["all_day"] else clock(e["start"]) + "-" + clock(e["end"]), e["title"],
                                   (" (" + e["location"] + ")") if e["location"] else "") for e in evs[:60]]
        errs = [p["name"] + ": " + p["error"] for p in self.env.calendar.public_sources() if p.get("error")]
        if gerr:
            errs.append(gerr)
        return ("[ATLAS calendar] Now: %s. Events from %s to %s (%d):\n%s%s\n%s" % (now.strftime("%A %Y-%m-%d %H:%M"), first.isoformat(), last.isoformat(), len(evs),
                "\n".join(lines) if lines else "- (no events in this period)", ("\nCalendar problems: " + "; ".join(errs)) if errs else "", rule))

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
                return self._r(("I don't see a recent or upcoming game for %s." % team) if followed else "You don't follow %s yet. Say follow %s in soccer (or the right sport) and I'll watch for them." % (team, team))
            return self._r(" ".join(SC.speak_game(g, g["mine"]) for g in mine[:3]) + (" These scores may be out of date." if sc.get("stale") else ""))
        if not games:
            return self._r("No recent or upcoming games for the teams you follow." + ((" There's no live feed for %s." % ", ".join(sc["unsupported"])) if sc["unsupported"] else ""))
        return self._r(" ".join(SC.speak_game(g) for g in games[:5]) + (" These scores may be out of date." if sc.get("stale") else ""))
