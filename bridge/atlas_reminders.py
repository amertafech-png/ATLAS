"""Spoken reminders and timers: "remind me at 3 to call Mike", "remind me tomorrow at 9 to send the report", "set a 10 minute timer".
Pure logic + a small JSON store (reminders.json in the ATLAS data folder, so they survive restarts).
Alerts are delivered by the bridge's scheduler as an SSE event to the HUD (chime + spoken + shown in the panel); they only sound while the HUD window is open,
and anything that came due while it was closed is delivered, marked late, the next time it opens."""
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta

WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
         "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
         "a": 1, "an": 1, "couple": 2, "few": 3}
UNITS = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600, "day": 86400}
UNIT_RE = r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)"
NUM_RE = r"(?:\d+(?:\.\d+)?|(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[ -](?:one|two|three|four|five|six|seven|eight|nine))?|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|an?|couple of|few)"
HALF_RE = r"(?:half an?|a half)"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MAX_ITEMS = 100


def to_num(tok):
    tok = tok.strip().lower().replace("-", " ")
    if re.fullmatch(r"\d+(?:\.\d+)?", tok):
        return float(tok)
    tok = tok.replace("couple of", "couple")
    parts = tok.split()
    if len(parts) == 2 and parts[0] in WORDS and parts[1] in WORDS and WORDS[parts[0]] >= 20:
        return WORDS[parts[0]] + WORDS[parts[1]]
    return WORDS.get(tok)


def parse_duration(text):
    """'10 minutes' / 'an hour and a half' / 'half an hour' / '1 hour 30 minutes' / '1h30' -> (seconds, (start, end)) or (None, None)."""
    t = text.lower()
    total, spans = 0.0, []
    def take(rx, fn):
        nonlocal t, total
        for m in list(re.finditer(rx, t)):
            v = fn(m)
            if v is None:
                continue
            total += v
            spans.append(m.span())
            t = t[:m.start()] + " " * (m.end() - m.start()) + t[m.end():]
    unit = lambda g: UNITS[re.sub(r"s$", "", g)]
    take(r"\b(%s)\s+(%s)\s+and\s+(?:a\s+)?half\b" % (NUM_RE, UNIT_RE), lambda m: (to_num(m.group(1)) + 0.5) * unit(m.group(2)) if to_num(m.group(1)) is not None else None)   # an hour and a half
    take(r"\b(%s)\s+and\s+(?:a\s+)?half\s+(%s)\b" % (NUM_RE, UNIT_RE), lambda m: (to_num(m.group(1)) + 0.5) * unit(m.group(2)) if to_num(m.group(1)) is not None else None)   # 2 and a half hours
    take(r"\b%s\s*(%s)\b" % (HALF_RE, UNIT_RE), lambda m: 0.5 * unit(m.group(1)))                                       # half an hour
    take(r"\b(\d+)\s*h(?:ours?|rs?)?\s*(\d+)\s*(?:m(?:in(?:ute)?s?)?)?\b", lambda m: int(m.group(1)) * 3600 + int(m.group(2)) * 60)      # 1h30
    take(r"\b(%s)\s*(%s)\b" % (NUM_RE, UNIT_RE), lambda m: to_num(m.group(1)) * unit(m.group(2)) if to_num(m.group(1)) is not None else None)
    if not spans:
        return None, None
    return total, (min(a for a, _ in spans), max(b for _, b in spans))


def clock_in(h, m, ampm, hint, day_is_today, now):
    """-> (hour24, minute) choosing am/pm when it wasn't said."""
    if ampm == "am":
        return (0 if h == 12 else h), m
    if ampm == "pm":
        return (12 if h == 12 else h + 12), m
    if h >= 13 or h == 0:
        return h % 24, m
    if h == 12:
        return (0 if hint == "am" else 12), m
    if hint == "am":
        return h, m
    if hint == "pm":
        return h + 12, m
    if day_is_today:
        for hh in (h, h + 12):
            if hh * 60 + m > now.hour * 60 + now.minute + 0:
                return hh, m
        return h, m                      # both passed today: tomorrow morning
    return (h, m) if 7 <= h <= 11 else (h + 12, m)


def parse_when(text, now):
    """Find the time in `text` -> (due datetime | None, remaining text, kind) ; kind 'duration' | 'clock' | ''."""
    t = " " + re.sub(r"\s+", " ", text.strip()) + " "
    low = t.lower().replace("\u2019", "'")
    dur, span = parse_duration(low)
    if dur is not None:
        mm = re.search(r"\b(?:in|after|within)\s+$", low[:span[0]])
        start = mm.start() if mm else span[0]
        rest = (t[:start] + " " + t[span[1]:])
        return now + timedelta(seconds=dur), tidy(rest), "duration"
    spans = []
    def take(rx):
        m = re.search(rx, low)
        if m:
            spans.append(m.span())
        return m
    day, hint, date = None, None, None
    if take(r"\bday after tomorrow\b"):
        date = (now + timedelta(days=2)).date()
    elif take(r"\btomorrow\b"):
        date = (now + timedelta(days=1)).date()
    elif take(r"\btonight\b"):
        date, hint = now.date(), "pm"
    elif take(r"\btoday\b"):
        date = now.date()
    m = take(r"\b(?:this|in the|tomorrow)\s+(morning|afternoon|evening)\b|\b(morning|afternoon|evening)\b(?! of)") if False else None
    m = re.search(r"\b(?:this |in the |tomorrow )?(morning|afternoon|evening)\b", low)
    pod = None
    if m and not (m.start() > 0 and low[m.start() - 1:m.start()] == "-"):
        pod = m.group(1)
        spans.append(m.span())
        hint = "am" if pod == "morning" else "pm"
        if date is None and not re.search(r"\btomorrow\b", low):
            date = None
    m = re.search(r"\b(?:on |next |this )?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", low)
    if m:
        wd = WEEKDAYS.index(m.group(1))
        delta = (wd - now.weekday()) % 7
        if delta == 0 and (m.group(0).startswith("next") or False):
            delta = 7
        if m.group(0).startswith("next") and delta < 7 and delta != 0 and False:
            delta += 7
        date = (now + timedelta(days=delta)).date() if date is None else date
        spans.append(m.span())
    h = mi = None
    ampm = None
    ct = None
    for rx, kind in ((r"\bat\s+noon\b|\bnoon\b", "noon"), (r"\bat\s+midnight\b|\bmidnight\b", "mid"),
                     (r"\b(?:at\s+)?half past\s+(\w+)", "half"), (r"\b(?:at\s+)?quarter past\s+(\w+)", "qp"), (r"\b(?:at\s+)?quarter to\s+(\w+)", "qt"),
                     (r"\b(?:at\s+)?(\d{1,2}):(\d{2})\s*(a\.?m\.?|p\.?m\.?)?", "hm"), (r"\b(?:at\s+)?(\d{1,2})\s*(a\.?m\.?|p\.?m\.?)(?![a-z])", "hap"),
                     (r"\bat\s+(\d{1,2})(?:\s*o'?clock)?(?!\d|:)", "h"), (r"\b(\d{1,2})\s*o'?clock\b", "h"),
                     (r"\bat\s+(%s)(?:\s+(%s))?\s*(a\.?m\.?|p\.?m\.?)?(?![a-z])" % ("|".join(k for k, v in WORDS.items() if 1 <= v <= 12 and k not in ("a", "an")), "|".join(k for k, v in WORDS.items() if v >= 10 and k not in ("a", "an", "few", "couple"))), "words")):
        m = re.search(rx, low)
        if not m:
            continue
        ct = kind
        spans.append(m.span())
        if kind == "noon":
            h, mi, ampm = 12, 0, "pm"
        elif kind == "mid":
            h, mi, ampm = 12, 0, "am"
        elif kind in ("half", "qp", "qt"):
            hv = to_num(m.group(1)) if not m.group(1).isdigit() else int(m.group(1))
            if hv is None:
                spans.pop(); ct = None; continue
            hv = int(hv)
            if kind == "half":
                h, mi = hv, 30
            elif kind == "qp":
                h, mi = hv, 15
            else:
                h, mi = (hv - 1) or 12, 45
        elif kind == "hm":
            h, mi = int(m.group(1)), int(m.group(2))
            ampm = (m.group(3) or "")[:1].replace("a", "am").replace("p", "pm") or None
        elif kind == "hap":
            h, mi = int(m.group(1)), 0
            ampm = "am" if m.group(2).startswith("a") else "pm"
        elif kind == "h":
            h, mi = int(m.group(1)), 0
        else:
            h = int(to_num(m.group(1)))
            mi = int(to_num(m.group(2))) if m.group(2) else 0
            ampm = (m.group(3) or "")[:1].replace("a", "am").replace("p", "pm") or None
        break
    if h is None and ct is None and date is None and pod is None:
        return None, text, ""
    if h is not None and (h > 24 or mi > 59 or (h == 24 and mi)):
        return None, text, ""
    if h is None:
        h, mi, ampm = {"morning": (9, 0, "am"), "afternoon": (3, 0, "pm"), "evening": (6, 0, "pm")}.get(pod, (9, 0, "am")) if pod else ((8, 0, "pm") if hint == "pm" and date == now.date() else (9, 0, "am"))
    today = (date is None) or date == now.date()
    hh, mm_ = clock_in(h, mi, ampm, hint, today, now)
    d = date or now.date()
    due = datetime.combine(d, datetime.min.time()).replace(tzinfo=now.tzinfo) + timedelta(hours=hh, minutes=mm_)
    if due <= now:
        if date is None or date == now.date():
            due += timedelta(days=1) if not any(re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", low[a:b]) for a, b in spans) else timedelta(days=7)
        else:
            due += timedelta(days=7) if re.search(r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", low) else timedelta(0)
    spans.sort()
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    out, last = "", 0
    for a, b in merged:
        out += t[last:a] + " "
        last = b
    out += t[last:]
    return due, tidy(out), "clock"


def tidy(s):
    s = re.sub(r"\s+", " ", s).strip(" ,.;:-")
    s = re.sub(r"^(?:and|at|on|in|to|that|about|for)\s+", "", s, flags=re.I)
    s = re.sub(r"\s+(?:at|on|in|and|to|for|by)$", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip(" ,.;:-")


FLIP = [(r"\bmyself\b", "yourself"), (r"\bmy\b", "your"), (r"\bmine\b", "yours"), (r"\bI'm\b", "you're"), (r"\bI've\b", "you've"), (r"\bI'll\b", "you'll"), (r"\bI\b", "you"), (r"\bme\b", "you")]


def second_person(text):
    for rx, rep in FLIP:
        text = re.sub(rx, rep, text, flags=re.I)
    return text


def say_clock(dt):
    return dt.strftime("%I:%M %p").lstrip("0").replace(":00 ", " ")


def say_when(due, now):
    d = (due.date() - now.date()).days
    day = "today" if d == 0 else "tomorrow" if d == 1 else due.strftime("%A") if d < 7 else due.strftime("%A %B %-d")
    return "%s at %s" % (day, say_clock(due))


def say_dur(sec, adj=False):
    """Plain spoken length: '10 minutes', '1 hour 30 minutes', '90 seconds'.  adj=True -> '10-minute' / '90-minute' for 'a 90-minute timer'."""
    sec = int(round(sec))
    if sec < 60 or (sec < 120 and sec % 60) or (adj and sec < 600 and sec % 60):
        n, u = sec, "second"
    elif sec < 3600:
        m, s_ = divmod(sec, 60)
        if s_ and sec < 600:
            return "%d minute%s %d second%s" % (m, "" if m == 1 else "s", s_, "" if s_ == 1 else "s")
        n, u = m, "minute"
    elif sec < 86400 * 2:
        h, m = divmod(sec // 60, 60)
        if m and adj:
            n, u = sec // 60, "minute"
        elif m:
            return "%d hour%s %d minute%s" % (h, "" if h == 1 else "s", m, "" if m == 1 else "s")
        else:
            n, u = h, "hour"
    else:
        n, u = sec // 86400, "day"
    return ("%d-%s" % (n, u)) if adj else "%d %s%s" % (n, u, "" if n == 1 else "s")


class Store:
    def __init__(self, path, now=time.time):
        self.path, self.now = path, now
        self.items = []
        self.load()

    def load(self):
        try:
            j = json.loads(open(self.path, encoding="utf-8").read())
            self.items = [i for i in j.get("items", []) if isinstance(i, dict) and "due" in i][:MAX_ITEMS]
        except Exception:  # noqa
            self.items = []

    def save(self):
        tmp = str(self.path) + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"items": self.items}, f, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def add(self, kind, text, due, label=""):
        self.prune()
        if sum(1 for i in self.items if not i.get("fired")) >= 50:
            raise ValueError("That's a lot of reminders (50). Cancel a few first.")
        it = {"id": uuid.uuid4().hex[:8], "kind": kind, "text": text, "label": label, "due": float(due), "created": self.now(), "fired": 0, "dismissed": 0}
        self.items.append(it)
        self.save()
        return it

    def active(self):
        return sorted((i for i in self.items if not i["fired"]), key=lambda i: i["due"])

    def alerts(self):
        return sorted((i for i in self.items if i["fired"] and not i["dismissed"]), key=lambda i: i["fired"])

    def get(self, rid):
        return next((i for i in self.items if i["id"] == rid), None)

    def cancel(self, rid):
        n = len(self.items)
        self.items = [i for i in self.items if i["id"] != rid]
        self.save()
        return len(self.items) != n

    def dismiss(self, rid):
        i = self.get(rid)
        if i:
            i["dismissed"] = self.now()
            self.save()
        return bool(i)

    def prune(self):
        cut = self.now() - 24 * 3600
        self.items = [i for i in self.items if not (i["fired"] and (i["dismissed"] or i["fired"] < cut))][-MAX_ITEMS:]

    def due(self, has_clients=True):
        """Items to deliver now.  With no HUD open nothing is delivered (it waits, and arrives marked late)."""
        if not has_clients:
            return []
        t = self.now()
        out = [i for i in self.items if not i["fired"] and i["due"] <= t]
        return sorted(out, key=lambda i: i["due"])

    def mark_fired(self, rid):
        i = self.get(rid)
        if i:
            i["fired"] = self.now()
            self.save()


def alert_payload(it, now_ts, tz=None):
    late = max(0, now_ts - it["due"])
    if it["kind"] == "timer":
        nm = it.get("label") or ""
        speak = "Your %s timer is done." % (nm or say_dur(it["due"] - it["created"], True))
        if it.get("text"):
            speak = "Your timer for %s is done." % second_person(it["text"])
    else:
        speak = "Reminder: %s." % second_person(it["text"]).rstrip(".")
    if late > 120:
        t = datetime.fromtimestamp(it["due"], tz)
        speak += " That was for %s." % say_clock(t)
    return {"id": it["id"], "kind": it["kind"], "text": it["text"], "label": it.get("label", ""), "speak": speak, "late": int(late), "due": it["due"]}


def view(it, now_ts, tz=None):
    d = datetime.fromtimestamp(it["due"], tz)
    return {"id": it["id"], "kind": it["kind"], "text": it["text"], "label": it.get("label", ""), "due": it["due"], "due_label": say_clock(d), "left": max(0, int(it["due"] - now_ts)),
            "fired": bool(it["fired"]), "total": int(it["due"] - it["created"])}


# ============================================================== voice / chat
class Reminders:
    def __init__(self, store, now_dt, assistant_name=lambda: "ATLAS"):
        self.store, self.now_dt, self.assistant_name = store, now_dt, assistant_name
        self.pending = None            # {"kind": "when"|"text"|"cancel_all"|"which", ...,"ts"}

    def clean(self, text):
        t = re.sub(r"[\"!?;]+", " ", str(text or "").replace("\u2019", "'"))
        t = re.sub(r"\s+", " ", t).strip(" .,:")
        t = re.sub(r"^(?:hey |ok |okay )?%s[ ,:]+" % re.escape(self.assistant_name()), "", t, flags=re.I)
        t = re.sub(r"^(?:please|can you|could you|would you|will you|just)\s+", "", t, flags=re.I)
        t = re.sub(r"\s+(?:please|for me)$", "", t, flags=re.I)
        return t

    def parse(self, text):
        raw = self.clean(text)
        low = raw.lower()
        # timers
        durx = r"((?:%s|%s)(?:\s*(?:and\s+)?(?:%s|%s)\s*%s)*)" % (NUM_RE, HALF_RE, NUM_RE, HALF_RE, "")
        if re.search(r"\btimers?\b|\bcount ?down\b|\balarm\b", low) and not re.match(r"(?:cancel|stop|delete|remove|clear|kill|turn off|what|which|list|show|how|read)", low):
            sec, sp = parse_duration(low)
            if sec:
                lab = ""
                m = re.search(r"\b(?:for|called|named|to)\s+(?!%s)(.+)$" % NUM_RE, low[sp[1]:]) if False else None
                after = re.sub(r"^\W*(?:timer|alarm|countdown)\W*", "", low[sp[1]:]).strip()
                m = re.match(r"(?:for|called|named|to|labell?ed)\s+(.+)$", after)
                lab = m.group(1) if m else ""
                if not lab:
                    before = low[:sp[0]]
                    m = re.search(r"\btimer (?:called|named|for) (.+?)\s*(?:for|of)?\s*$", before)
                    lab = m.group(1) if m else ""
                return {"op": "timer", "seconds": sec, "label": lab.strip(" .,")}
            if re.search(r"\b(?:set|start|create|make|begin)\b", low):
                return {"op": "timer_ask"}
        # reminders  ("at 6 pm remind me to pick up the kids" -> "remind me to pick up the kids at 6 pm")
        pm = re.match(r"((?:at|in|on|tomorrow|tonight|this|later|next)\b[^,]*?),?\s+(?:please )?remind me (?:to |that |about )?(.+)$", raw, flags=re.I)
        if pm:
            raw = "remind me to %s %s" % (pm.group(2).strip(), pm.group(1).strip())
        m = re.match(r"(?:set (?:a |an )?(?:new )?reminder (?:for|to|that)|(?:add|create|make) (?:a |an )?(?:new )?reminder (?:for|to|that)?|remind me|remind us|reminder|reminder me)\s*(.*)", raw, flags=re.I)
        if m:
            return {"op": "remind", "body": m.group(1).strip()}
        # list / remaining / cancel
        if re.fullmatch(r"(?:how (?:much|long)|what(?:'s| is))(?: time| of the timer)?(?: is)? (?:left|remaining)(?: on| for| in| of)?(?: my| the| that)?(?: (?:\w+ ){0,2}timer)?|how long (?:until|till|before) (?:my|the) (?:\w+ )?(?:timer|reminder)(?: (?:goes off|is done|is up|rings))?|timer (?:status|left)|how(?:'s| is) (?:my|the) timer(?: doing)?", low):
            return {"op": "left"}
        if re.search(r"\b(?:reminders?|timers?|alarms?)\b", low) and re.match(r"(?:what|which|list|show|read|tell me|give me|do i have|have i got|any|are there|how many)\b", low) and not re.search(r"\b(?:set|panel|settings?|volume|sound)\b", low):
            return {"op": "list", "scope": "timer" if re.search(r"timer|alarm", low) and not re.search(r"reminder", low) else "reminder" if "reminder" in low and "timer" not in low else "all"}
        if re.fullmatch(r"what am i (?:supposed|meant) to (?:do|remember)|am i forgetting anything", low):
            return {"op": "list", "scope": "all"}
        m = re.fullmatch(r"(?:cancel|stop|delete|remove|clear|kill|turn off|dismiss|forget|get rid of|never ?mind(?: about)?|disregard)(?: all)?(?: of)?(?: my| the| that| this| those| these| your)?(?: (?:all|every))?\s*(.*)", low)
        if m and re.search(r"\b(?:reminders?|timers?|alarms?)\b|^(?:the )?(?:reminder|timer)", low) and not re.search(r"\b(?:conversation|calendar|music|mute|wake|settings?|listening|updates?|chimes?|sounds?|volume|beeps?|panel)\b", low):
            rest = m.group(1).strip()
            allw = bool(re.search(r"\ball\b|\bevery\b|\beverything\b", low))
            kind = "timer" if re.search(r"timer|alarm", low) else "reminder" if re.search(r"reminder", low) else "all"
            rest = re.sub(r"^(?:reminders?|timers?|alarms?)(?: (?:about|to|for|called|named))?\s*", "", rest).strip()
            rest = re.sub(r"\b(?:reminders?|timers?|alarms?)\b", "", rest).strip()
            rest = re.sub(r"^(?:about|to|for|called|named|that says|the one (?:about|to|for))\s+", "", rest).strip()
            return {"op": "cancel", "all": allw, "kind": kind, "what": rest}
        return None

    # ---- helpers
    def _t(self):
        return time.time()

    def _items(self, kind="all"):
        a = self.store.active()
        return [i for i in a if kind == "all" or i["kind"] == kind]

    def _r(self, reply, changed=("reminders",), pending=False):
        return {"reply": reply, "changed": list(changed), "ui": None, "pending": pending}

    def describe_item(self, i, now):
        due = datetime.fromtimestamp(i["due"], now.tzinfo)
        if i["kind"] == "timer":
            return "%s timer%s, %s left" % (i["label"] or say_dur(i["due"] - i["created"], True) if not (i["label"] or "").endswith("timer") else i["label"], (" for " + i["text"]) if i.get("text") else "", say_dur(max(0, i["due"] - now.timestamp())))
        return "%s, %s" % (i["text"], say_when(due, now))

    # ---- handle
    async def handle(self, text):
        now = self.now_dt()
        if self.pending and time.time() - self.pending["ts"] > 120:
            self.pending = None
        low = self.clean(text).lower()
        if self.pending:
            p, self.pending = self.pending, None
            if p["kind"] == "when":
                due, rest, _k = parse_when(self.clean(text), now)
                if due:
                    return self._add_reminder(p["text"], due, now)
                if low in ("no", "never mind", "nevermind", "cancel", "forget it", "nothing"):
                    return self._r("Okay, no reminder.", [])
                self.pending = {"kind": "when", "text": p["text"], "ts": time.time()}
                return self._r("I didn't catch when. Say something like in 20 minutes, at 3 PM, or tomorrow at 9.", [], True)
            if p["kind"] == "text":
                if low in ("no", "never mind", "nevermind", "cancel", "forget it", "nothing"):
                    return self._r("Okay, no reminder.", [])
                return self._add_reminder(self.clean(text), p["due"], now)
            if p["kind"] == "cancel_all":
                if low in ("yes", "yeah", "yep", "yes please", "sure", "ok", "okay", "do it", "go ahead", "confirm"):
                    n = 0
                    for i in p["ids"]:
                        n += bool(self.store.cancel(i))
                    return self._r("Done. I cancelled %d." % n)
                return self._r("Okay, I left them.", [])
            if p["kind"] == "which":
                it = self.parse("cancel " + low) if False else None
                hits = [i for i in p["items"] if low and (low in (self.store.get(i) or {}).get("text", "").lower() or low in ((self.store.get(i) or {}).get("label") or "").lower())]
                if len(hits) == 1:
                    self.store.cancel(hits[0])
                    return self._r("Okay, cancelled.")
                if low in ("all", "all of them", "both", "everything"):
                    for i in p["items"]:
                        self.store.cancel(i)
                    return self._r("Okay, I cancelled all of them.")
                # not an answer: fall through to normal parsing
        it = self.parse(text)
        if not it:
            return None
        op = it["op"]
        if op == "timer":
            sec = it["seconds"]
            if sec < 1 or sec > 7 * 86400:
                return self._r("A timer can be from 1 second to 7 days.", [])
            lab = it["label"]
            now_ts = time.time()
            ti = self.store.add("timer", lab, now_ts + sec, "")
            return self._r("Okay, a %s timer%s, starting now." % (say_dur(sec, True), (" for " + second_person(lab)) if lab else ""))
        if op == "timer_ask":
            return self._r("How long should the timer be? Say something like 10 minutes.", [])
        if op == "remind":
            body = it["body"]
            due, rest, kind = parse_when(body, now)
            if not body:
                self.pending = {"kind": "when", "text": "", "ts": time.time()}
                return self._r("What should I remind you about, and when?", [], True)
            if due is None:
                self.pending = {"kind": "when", "text": tidy(body), "ts": time.time()}
                return self._r("When should I remind you to %s? Say something like in 20 minutes, or at 3 PM." % second_person(tidy(body)), [], True)
            if not rest:
                self.pending = {"kind": "text", "due": due, "ts": time.time()}
                return self._r("What should I remind you about %s?" % (say_when(due, now) if kind == "clock" else "then"), [], True)
            return self._add_reminder(rest, due, now)
        if op == "list":
            sc = it["scope"]
            items = self._items(sc if sc != "all" else "all")
            what = {"timer": "timers", "reminder": "reminders", "all": "reminders or timers"}[sc]
            if not items:
                return self._r("You don't have any %s set." % what, [])
            lines = [self.describe_item(i, now) for i in items[:6]]
            return self._r("You have %d %s: %s%s." % (len(items), what if len(items) != 1 else what.rstrip("s"), "; ".join(lines), "; and more" if len(items) > 6 else ""), [])
        if op == "left":
            tm = self._items("timer")
            if not tm:
                nxt = self._items("reminder")
                return self._r("You don't have a timer running." + ((" Your next reminder is %s." % self.describe_item(nxt[0], now)) if nxt else ""), [])
            return self._r("; ".join("%s left on the %s timer" % (say_dur(max(0, i["due"] - time.time())), i["label"] or say_dur(i["due"] - i["created"], True)) for i in tm[:4]) + ".", [])
        if op == "cancel":
            pool = self._items(it["kind"] if it["kind"] != "all" else "all")
            if it["what"]:
                w = it["what"].lower()
                hits = [i for i in pool if w in i["text"].lower() or w in (i.get("label") or "").lower() or (i["kind"] == "timer" and w in say_dur(i["due"] - i["created"]).lower())]
                if not hits:
                    nums = [i for i in pool if i["kind"] == "timer" and (parse_duration(w)[0] or -1) == round(i["due"] - i["created"])]
                    hits = nums
                if not hits:
                    return self._r("I couldn't find one about %s. Say what reminders do I have." % it["what"], [])
                pool = hits
            if not pool:
                return self._r("You don't have any %s to cancel." % ("timers" if it["kind"] == "timer" else "reminders" if it["kind"] == "reminder" else "reminders or timers"), [])
            if len(pool) == 1 or (it["all"] and False):
                self.store.cancel(pool[0]["id"])
                return self._r("Okay, I cancelled %s." % ("your timer" if pool[0]["kind"] == "timer" else "the reminder to " + second_person(pool[0]["text"])))
            if it["all"]:
                self.pending = {"kind": "cancel_all", "ids": [i["id"] for i in pool], "ts": time.time()}
                return self._r("Cancel all %d? Say yes or no." % len(pool), [], True)
            self.pending = {"kind": "which", "items": [i["id"] for i in pool], "ts": time.time()}
            return self._r("Which one: %s? Or say all." % "; ".join(self.describe_item(i, now) for i in pool[:5]), [], True)
        return None

    def _add_reminder(self, text, due, now):
        text = tidy(text)
        if not text:
            self.pending = {"kind": "text", "due": due, "ts": time.time()}
            return self._r("What should I remind you about?", [], True)
        try:
            self.store.add("reminder", text, due.timestamp())
        except ValueError as e:
            return self._r(str(e), [])
        secs = (due - now).total_seconds()
        when = ("in " + say_dur(secs)) if secs < 3600 and False else say_when(due, now)
        return self._r("Okay, I'll remind you to %s %s." % (second_person(text), when))
