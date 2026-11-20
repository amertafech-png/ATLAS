"""ATLAS calendar: reads iCalendar (.ics) from a local file or a private link ("secret address" of Google Calendar / Outlook / iCloud / any CalDAV
export).  No OAuth, no account login, no write access: it only reads.  Nothing here talks to any provider except the one address you gave it.

parse_ics(text, tz)        -> events (recurrence rules are expanded later)
expand(events, start, end) -> every occurrence between two aware datetimes, sorted
Calendar(cfg, save, ...)   -> the sources list (kept in config.json under calendar.sources), fetching + a 10 minute cache; the secret link is never shown or logged
"""
import ipaddress
import re
import socket
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except Exception:  # noqa
    ZoneInfo = None

MAX_BYTES = 5 * 1024 * 1024
CACHE_SECONDS = 600
DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def local_tz(name=""):
    if name and ZoneInfo:
        try:
            return ZoneInfo(name)
        except Exception:  # noqa
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc


def unfold(text):
    out = []
    for line in re.split(r"\r\n|\n|\r", text):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def unescape(v):
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def parse_prop(line):
    if ":" not in line:
        return None
    head, val = line.split(":", 1)
    parts = head.split(";")
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.upper()] = v.strip('"')
    return parts[0].upper(), params, val


def parse_dt(val, params, tz):
    """-> (aware datetime, all_day)"""
    val = val.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", val):
        d = datetime.strptime(val[:8], "%Y%m%d")
        return d.replace(tzinfo=tz), True
    z = val.endswith("Z")
    d = datetime.strptime(val.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if z:
        return d.replace(tzinfo=timezone.utc).astimezone(tz), False
    tzid = params.get("TZID")
    if tzid:
        t = None
        if ZoneInfo:
            try:
                t = ZoneInfo(tzid)
            except Exception:  # noqa
                t = None
        return d.replace(tzinfo=t or tz).astimezone(tz), False
    return d.replace(tzinfo=tz), False


def parse_rrule(v):
    r = {}
    for part in v.split(";"):
        if "=" in part:
            k, x = part.split("=", 1)
            r[k.upper()] = x
    return r


def parse_ics(text, tz):
    events, cur = [], None
    for line in unfold(text):
        u = line.strip().upper()
        if u == "BEGIN:VEVENT":
            cur = {"exdates": [], "title": "", "location": "", "uid": "", "rrule": None, "status": "", "rid": None, "start": None, "end": None, "all_day": False, "duration": None}
            continue
        if u == "END:VEVENT":
            if cur and cur["start"]:
                if cur["end"] is None:
                    cur["end"] = cur["start"] + (cur["duration"] or (timedelta(days=1) if cur["all_day"] else timedelta(0)))
                if cur["status"] != "CANCELLED":
                    events.append(cur)
            cur = None
            continue
        if cur is None:
            continue
        pp = parse_prop(line)
        if not pp:
            continue
        name, params, val = pp
        try:
            if name == "DTSTART":
                cur["start"], cur["all_day"] = parse_dt(val, params, tz)
            elif name == "DTEND":
                cur["end"], _ = parse_dt(val, params, tz)
            elif name == "DURATION":
                m = re.fullmatch(r"P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", val.strip())
                if m:
                    w, d, h, mi, s = [int(x or 0) for x in m.groups()]
                    cur["duration"] = timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)
            elif name == "SUMMARY":
                cur["title"] = unescape(val).strip()
            elif name == "LOCATION":
                cur["location"] = unescape(val).strip()
            elif name == "UID":
                cur["uid"] = val.strip()
            elif name == "STATUS":
                cur["status"] = val.strip().upper()
            elif name == "RRULE":
                cur["rrule"] = parse_rrule(val.strip())
            elif name == "EXDATE":
                for x in val.split(","):
                    d, _ = parse_dt(x, params, tz)
                    cur["exdates"].append(d)
            elif name == "RECURRENCE-ID":
                cur["rid"], _ = parse_dt(val, params, tz)
        except (ValueError, KeyError):
            continue
    return events


def _nth_weekday(year, month, wd, n):
    if n > 0:
        d = date(year, month, 1)
        d += timedelta(days=(wd - d.weekday()) % 7 + 7 * (n - 1))
        return d if d.month == month else None
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    d -= timedelta(days=(d.weekday() - wd) % 7 + 7 * (-n - 1))
    return d if d.month == month else None


def occurrences(ev, win_start, win_end):
    """Start datetimes of one event (with its RRULE) that overlap [win_start, win_end)."""
    st, en = ev["start"], ev["end"]
    dur = en - st
    r = ev["rrule"]
    out = []
    if not r:
        if en > win_start and st < win_end or (dur == timedelta(0) and win_start <= st < win_end):
            out.append(st)
        return out
    freq = r.get("FREQ", "")
    interval = max(1, int(r.get("INTERVAL", "1") or 1))
    count = int(r["COUNT"]) if r.get("COUNT", "").isdigit() else None
    until = None
    if r.get("UNTIL"):
        try:
            until, _ = parse_dt(r["UNTIL"], {}, st.tzinfo)
            if re.fullmatch(r"\d{8}", r["UNTIL"]):
                until += timedelta(days=1)
        except ValueError:
            until = None
    ex = {x.astimezone(st.tzinfo) for x in ev["exdates"]} if not ev["all_day"] else {x.replace(tzinfo=st.tzinfo) for x in ev["exdates"]}
    byday = [x for x in r.get("BYDAY", "").split(",") if x]
    bymd = [int(x) for x in r.get("BYMONTHDAY", "").split(",") if re.fullmatch(r"-?\d+", x)]
    n = 0
    cur_date = st.date()
    seen = 0
    guard = 0
    cands = []
    # generate candidate dates lazily, period by period
    period = 0
    while guard < 6000:
        guard += 1
        dates = []
        if freq == "DAILY":
            dates = [cur_date + timedelta(days=interval * period)]
        elif freq == "WEEKLY":
            base = cur_date - timedelta(days=cur_date.weekday()) + timedelta(weeks=interval * period)
            days = [DAYS.index(x[-2:]) for x in byday if x[-2:] in DAYS] or [cur_date.weekday()]
            dates = sorted(base + timedelta(days=d) for d in days)
        elif freq == "MONTHLY":
            y, m = st.year + (st.month - 1 + interval * period) // 12, (st.month - 1 + interval * period) % 12 + 1
            if bymd:
                import calendar as _c
                last = _c.monthrange(y, m)[1]
                dates = sorted(date(y, m, d if d > 0 else last + 1 + d) for d in bymd if 1 <= (d if d > 0 else last + 1 + d) <= last)
            elif byday:
                for x in byday:
                    mm = re.fullmatch(r"(-?\d+)?([A-Z]{2})", x)
                    if mm and mm.group(2) in DAYS:
                        d = _nth_weekday(y, m, DAYS.index(mm.group(2)), int(mm.group(1) or 1))
                        if d:
                            dates.append(d)
                dates.sort()
            else:
                try:
                    dates = [date(y, m, st.day)]
                except ValueError:
                    dates = []
        elif freq == "YEARLY":
            try:
                dates = [date(st.year + interval * period, st.month, st.day)]
            except ValueError:
                dates = []
        else:
            return [st] if win_start <= st < win_end else []
        period += 1
        first_of_period = None
        for d in dates:
            if d < st.date():
                continue
            cand = datetime.combine(d, st.timetz()) if not ev["all_day"] else datetime.combine(d, datetime.min.time()).replace(tzinfo=st.tzinfo)
            cand = cand.replace(tzinfo=st.tzinfo)
            first_of_period = first_of_period or cand
            seen += 1
            if count is not None and seen > count:
                return out
            if until and cand > until:
                return out
            if cand + dur > win_start and cand < win_end and cand not in ex:
                out.append(cand)
            if cand >= win_end:
                return out
        if not dates and period > 2000:
            return out
        if first_of_period and first_of_period >= win_end:
            return out
    return out


def expand(events, win_start, win_end):
    overrides = {(e["uid"], e["rid"]): e for e in events if e.get("rid")}
    res = []
    for ev in events:
        if ev.get("rid"):
            if ev["end"] > win_start and ev["start"] < win_end:
                res.append(ev | {"occurs": ev["start"]})
            continue
        for st in occurrences(ev, win_start, win_end):
            ov = overrides.get((ev["uid"], st))
            if ov:
                continue
            res.append(ev | {"occurs": st, "occ_end": st + (ev["end"] - ev["start"])})
    for r in res:
        r.setdefault("occ_end", r["end"])
    res.sort(key=lambda e: (e["occurs"], e["title"]))
    return res


def clean_url(u):
    u = (u or "").strip()
    if u.lower().startswith("webcal://"):
        u = "https://" + u[9:]
    return u


def check_public_url(u):
    p = urllib.parse.urlparse(u)
    if p.scheme != "https" or not p.hostname:
        raise ValueError("Use a link that starts with https:// (or webcal://).")
    host = p.hostname
    if host in ("localhost",) or host.endswith(".local") or host.endswith(".internal"):
        raise ValueError("That address is on this computer or your own network - use the calendar's public secret link.")
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            raise ValueError("That address is on your own network - use the calendar's public secret link.")
    except ValueError as e:
        if "own network" in str(e):
            raise
    return u


def fetch_url(u, timeout=12):
    req = urllib.request.Request(u, headers={"User-Agent": "ATLAS-calendar/1", "Accept": "text/calendar, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 (https only, checked above)
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("That calendar file is larger than 5 MB.")
    return data.decode("utf-8", "replace")


def mask(src):
    if src["kind"] == "url":
        host = urllib.parse.urlparse(clean_url(src["value"])).hostname or "link"
        return host + " (secret link hidden)"
    return src["value"].replace("\\", "/").split("/")[-1]


class Calendar:
    def __init__(self, cfg, save, tz_name=lambda: "", fetch=fetch_url, now=time.time):
        self.cfg, self.save, self.tz_name, self.fetch, self.now = cfg, save, tz_name, fetch, now
        self.cache = {}      # id -> {t, events, error}

    # ---- sources
    def sources(self):
        return list((self.cfg.setdefault("calendar", {}).get("sources")) or [])

    def public_sources(self):
        out = []
        for s in self.sources():
            c = self.cache.get(s["id"], {})
            out.append({"id": s["id"], "name": s["name"], "kind": s["kind"], "hint": mask(s), "ok": bool(c) and not c.get("error"), "error": c.get("error", ""), "count": len(c.get("events", []))})
        return out

    def _store(self, srcs):
        self.cfg.setdefault("calendar", {})["sources"] = srcs
        self.save({"calendar": {"sources": srcs}})

    def add(self, name, value):
        value = (value or "").strip()
        name = re.sub(r"\s+", " ", (name or "").strip())[:40]
        if not value:
            raise ValueError("Paste the calendar link or the path of an .ics file.")
        if re.match(r"(?i)^(https?|webcal)://", value):
            value = check_public_url(clean_url(value))
            kind = "url"
        else:
            kind = "file"
            if not value.lower().endswith(".ics"):
                raise ValueError("A calendar file must end in .ics.")
        srcs = self.sources()
        if len(srcs) >= 8:
            raise ValueError("That's the most calendars (8). Remove one first.")
        if any(s["value"] == value for s in srcs):
            raise ValueError("That calendar is already added.")
        base = name or ("Calendar %d" % (len(srcs) + 1))
        sid = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-") or "calendar"
        ids = {s["id"] for s in srcs}
        n, cand = 2, sid
        while cand in ids:
            cand = "%s-%d" % (sid, n)
            n += 1
        srcs.append({"id": cand, "name": base, "kind": kind, "value": value})
        self._store(srcs)
        return cand

    def remove(self, key):
        key = (key or "").strip().lower()
        srcs = self.sources()
        keep = [s for s in srcs if s["id"] != key and s["name"].lower() != key]
        if len(keep) == len(srcs):
            return False
        self.cache = {k: v for k, v in self.cache.items() if k in {s["id"] for s in keep}}
        self._store(keep)
        return True

    # ---- reading
    def load(self, src, force=False):
        c = self.cache.get(src["id"])
        if c and not force and self.now() - c["t"] < CACHE_SECONDS:
            return c
        tz = local_tz(self.tz_name())
        try:
            if src["kind"] == "url":
                text = self.fetch(check_public_url(clean_url(src["value"])))
            else:
                import pathlib
                p = pathlib.Path(src["value"]).expanduser()
                if p.stat().st_size > MAX_BYTES:
                    raise ValueError("That calendar file is larger than 5 MB.")
                text = p.read_text(encoding="utf-8", errors="replace")
            if "BEGIN:VCALENDAR" not in text.upper():
                raise ValueError("That doesn't look like a calendar (.ics) file.")
            c = {"t": self.now(), "events": parse_ics(text, tz), "error": ""}
        except Exception as e:  # noqa  (never put the link in the message)
            msg = str(e) if isinstance(e, ValueError) else ("Couldn't read the calendar file." if src["kind"] == "file" else "Couldn't reach the calendar (offline, or the link changed).")
            c = {"t": self.now() - CACHE_SECONDS + 60, "events": (c or {}).get("events", []), "error": msg}
        self.cache[src["id"]] = c
        return c

    def events(self, start, end, force=False):
        """Occurrences between two aware datetimes from every source: dicts {title,start,end,all_day,location,source}."""
        out = []
        for src in self.sources():
            c = self.load(src, force)
            for e in expand(c["events"], start, end):
                out.append({"title": e["title"] or "(no title)", "start": e["occurs"], "end": e["occ_end"], "all_day": e["all_day"], "location": e["location"], "source": src["name"]})
        out.sort(key=lambda e: (e["start"], e["title"]))
        return out

    def day(self, d=None, force=False):
        tz = local_tz(self.tz_name())
        base = datetime.combine(d or datetime.now(tz).date(), datetime.min.time()).replace(tzinfo=tz)
        return self.events(base, base + timedelta(days=1), force)

    def now_dt(self):
        return datetime.now(local_tz(self.tz_name()))
