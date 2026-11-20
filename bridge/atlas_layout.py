"""ATLAS panel slots: the dashboard has six slots (three on the left, three on the right) and each slot shows one panel.
This file is pure logic (no I/O): the catalog of panels, the layout rules (swap / replace / hide / show / reset) and the
phrase parser ("replace the scanner with my calendar", "put sports where weather is", "what panels can I use", "reset my layout").
atlas_settings.py wires it to the settings registry; the HUD (index.html) draws whatever layout the bridge reports."""
import re

SLOTS = ["L1", "L2", "L3", "R1", "R2", "R3"]
SLOT_LABEL = {"L1": "top left", "L2": "middle left", "L3": "bottom left", "R1": "top right", "R2": "middle right", "R3": "bottom right"}
SLOT_WORDS = {"L1": ["top left", "upper left", "first left"], "L2": ["middle left", "center left", "centre left", "left middle", "second left"],
              "L3": ["bottom left", "lower left", "last left", "third left"], "R1": ["top right", "upper right", "first right"],
              "R2": ["middle right", "center right", "centre right", "right middle", "second right"],
              "R3": ["bottom right", "lower right", "last right", "third right"]}
DEFAULT_LAYOUT = {"L1": "clock", "L2": "weather", "L3": "scanner", "R1": "sports", "R2": "system", "R3": "hermes"}

# id -> (title, spoken aliases).  Every entry is a panel the HUD really has (built in); `source` says where it comes from.
CATALOG = {
    "clock": ("Clock", ["clock", "chronometer", "time", "date"]),
    "weather": ("Weather", ["weather", "forecast"]),
    "scanner": ("Scanner", ["scanner", "radar", "globe", "location scanner", "scanner and globe"]),
    "sports": ("Sports", ["sports", "scores", "sports scores", "teams", "game scores"]),
    "system": ("System", ["system", "system stats", "cpu", "system monitor", "performance"]),
    "calendar": ("Calendar", ["calendar", "my calendar", "schedule", "agenda", "events"]),
    "brief": ("Daily brief", ["daily brief", "brief", "my brief", "briefing"]),
    "reminders": ("Reminders", ["reminders", "reminder", "timers", "timer", "alarms", "reminders and timers", "timers and reminders"]),
    "hermes": ("Hermes status", ["hermes", "hermes status", "ai status", "status"]),
}


def norm(s):
    s = str(s or "").lower().replace("\u2019", "'")
    s = re.sub(r"[^a-z0-9' ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def strip_det(x):
    x = norm(x)
    x = re.sub(r"^(?:the|my|your|a|an|our)\s+", "", x)
    x = re.sub(r"\s+(?:panel|tile|widget|card|module|slot|spot|section)$", "", x)
    return x.strip()


def panel_id(name, available=None):
    """A spoken panel name -> panel id (or None).  `available` limits the answer to panels that can be used."""
    n = strip_det(name)
    if not n:
        return None
    for pid, (title, aliases) in CATALOG.items():
        if n == pid or n == norm(title) or n in aliases:
            return pid if (available is None or pid in available) else None
    return None


def known_panel(name):
    """Is it a panel name at all (even one that cannot be used right now)?"""
    return panel_id(name, None)


def slot_id(name):
    n = strip_det(name)
    n = re.sub(r"^(?:in|into|at|on|to)\s+", "", n)
    n = re.sub(r"^(?:the|my)\s+", "", n)
    n = re.sub(r"\s+(?:corner|position|area|side)$", "", n)
    for sid, words in SLOT_WORDS.items():
        if n in words:
            return sid
    return None


def get_layout(prefs):
    """The saved layout merged over the defaults.  An old 'panels_hidden' list (v1.3) becomes empty slots."""
    lay = dict(DEFAULT_LAYOUT)
    saved = (prefs or {}).get("layout")
    if isinstance(saved, dict):
        for s in SLOTS:
            if s in saved:
                v = saved[s]
                lay[s] = v if (v in CATALOG) else None
    for pid in (prefs or {}).get("panels_hidden") or []:
        for s in SLOTS:
            if lay[s] == pid and not isinstance(saved, dict):
                lay[s] = None
    return lay


def where(layout, pid):
    return next((s for s in SLOTS if layout.get(s) == pid), None)


def place(layout, pid, slot):
    """Put `pid` in `slot`.  If pid is already on screen the two panels swap; otherwise the old occupant is simply taken off screen.
    Returns (new_layout, displaced_panel_or_None, swapped_bool)."""
    lay = dict(layout)
    old = lay.get(slot)
    cur = where(lay, pid)
    if cur == slot:
        return lay, None, False
    if cur:
        lay[cur], lay[slot] = old, pid
        return lay, old, bool(old)
    lay[slot] = pid
    return lay, old, False


def hide(layout, pid):
    lay = dict(layout)
    s = where(lay, pid)
    if s:
        lay[s] = None
    return lay, s


def title(pid):
    return CATALOG[pid][0]


def describe(layout, available):
    on = [(SLOT_LABEL[s], title(layout[s])) for s in SLOTS if layout.get(s)]
    empty = [SLOT_LABEL[s] for s in SLOTS if not layout.get(s)]
    off = [title(p) for p in CATALOG if p in available and where(layout, p) is None]
    return on, empty, off


def parse(low, available):
    """low = normalised utterance.  Returns a dict {op: 'layout', act: ..., ...} or None.
    acts: replace(a,b) swap(a,b) place(panel,slot) hide(panel) show(panel) reset list where(panel|slot)"""
    if re.fullmatch(r"(?:reset|restore|undo|revert)(?: all)?(?: my| the| your)? (?:layout|panels|panel layout|dashboard|screen layout|panel slots)(?: to (?:the )?(?:default|defaults|normal|original))?|put (?:my |the )?(?:panels|layout) back(?: to (?:the )?(?:default|normal|original))?|(?:default|original) layout", low):
        return {"op": "layout", "act": "reset"}
    if re.fullmatch(r"(?:what|which) (?:panels|widgets|tiles|cards)(?: can| could| do)? (?:i|you|we)(?: use| have| choose| pick| put| show| add| get)?(?: on (?:the )?(?:screen|dashboard))?|(?:what|which) panels(?: are)? (?:there|available|installed)|list(?: all)?(?: the| my)? (?:available )?(?:panels|widgets|tiles)|what can i put on (?:the |my )?(?:screen|dashboard)|show me (?:the |my )?(?:available |possible )?panels|what panels are there", low):
        return {"op": "layout", "act": "list"}
    if re.fullmatch(r"(?:what(?:'s| is)|show|tell me|read)(?: me)?(?: on)?(?: my| the)? (?:screen )?(?:layout|panel layout|panel arrangement|dashboard layout|current layout)|what(?:'s| is) (?:on )?(?:my )?screen|where (?:is|are) everything|how is (?:my )?(?:screen|dashboard) (?:laid out|arranged)", low):
        return {"op": "layout", "act": "layout"}
    m = re.fullmatch(r"what(?:'s| is) (?:in|on|at) (?:the |my )?(.+?)(?: slot| spot| panel| corner)?", low)
    if m and slot_id(m.group(1)):
        return {"op": "layout", "act": "slot_get", "slot": slot_id(m.group(1))}
    m = re.fullmatch(r"where(?:'s| is) (?:the |my )?(.+?)(?: panel| tile)?", low)
    if m and known_panel(m.group(1)):
        return {"op": "layout", "act": "where", "panel": known_panel(m.group(1))}
    m = re.fullmatch(r"(?:replace|swap out|switch out|change|exchange)\s+(?:the |my )?(.+?)(?: panel| tile)?\s+(?:with|for|by|to|using)\s+(?:the |my )?(.+?)(?: panel| tile)?", low)
    if m and known_panel(m.group(1)) and known_panel(m.group(2)):
        return {"op": "layout", "act": "replace", "a": known_panel(m.group(1)), "b": known_panel(m.group(2))}
    m = re.fullmatch(r"(?:swap|switch|exchange|trade)\s+(?:the |my )?(.+?)(?: panel| tile)?\s+(?:and|with|for)\s+(?:the |my )?(.+?)(?: panel| tile)?", low)
    if m and known_panel(m.group(1)) and known_panel(m.group(2)):
        return {"op": "layout", "act": "swap", "a": known_panel(m.group(1)), "b": known_panel(m.group(2))}
    m = re.fullmatch(r"(?:put|move|place|show|display|bring|set|stick|slide)\s+(?:the |my )?(.+?)(?: panel| tile)?\s+(?:where|in place of|instead of|over|on top of|in the place of|in the spot of|in the slot of)\s+(?:the |my )?(.+?)(?: panel| tile)?(?: is| are| was)?(?: now)?", low)
    if m and known_panel(m.group(1)) and known_panel(m.group(2)):
        return {"op": "layout", "act": "replace", "a": known_panel(m.group(2)), "b": known_panel(m.group(1))}
    m = re.fullmatch(r"(?:put|move|place|show|display|set|stick|slide|bring)\s+(?:the |my )?(.+?)(?: panel| tile)?\s+(?:in|into|at|on|to)\s+(?:the |my )?(.+?)(?: slot| spot| panel| corner| position| area)?", low)
    if m and known_panel(m.group(1)) and slot_id(m.group(2)):
        return {"op": "layout", "act": "place", "panel": known_panel(m.group(1)), "slot": slot_id(m.group(2))}
    m = re.fullmatch(r"(hide|remove|get rid of|close|take away|take off|turn off|dismiss)\s+(?:the |my )?(.+?)(?: panel| tile| widget)?(?: from (?:the |my )?(?:screen|dashboard))?", low)
    if m and known_panel(m.group(2)) and (m.group(2).strip() not in ("sports", "teams") or "panel" in low or "tile" in low or "screen" in low or "dashboard" in low):
        return {"op": "layout", "act": "hide", "panel": known_panel(m.group(2))}
    m = re.fullmatch(r"(show|display|bring back|bring up|add|turn on|open)\s+(?:the |my )?(.+?)(?: panel| tile| widget)?(?: (?:to|on) (?:the |my )?(?:screen|dashboard))?", low)
    if m and known_panel(m.group(2)) and (re.search(r"panel|tile|widget|screen|dashboard", low) or m.group(1) in ("show", "bring back", "display") and m.group(2) in ("clock", "weather", "scanner", "system", "hermes", "calendar", "reminders", "brief", "daily brief")):
        return {"op": "layout", "act": "show", "panel": known_panel(m.group(2))}
    return None
