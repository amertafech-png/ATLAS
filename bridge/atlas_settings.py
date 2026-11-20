"""ATLAS settings registry + local voice/chat intent handler.

ONE table (REGISTRY, plus the dynamic module and panel rows) describes every setting a person can change:
id, title, group, kind (bool|int|text|enum|list|action), allowed values, whether it needs a confirmation, where it
lives in the Settings screen (`ui`) and a few example phrases.  Three things read that same table:

  * the HUD Settings screen   (GET /api/settings; the General tab is drawn from it, the other tabs show the same ids)
  * the HTTP setter           (POST /api/settings/<id>; enforces the confirmation rule for every client)
  * the intent handler below  (runs inside /api/chat BEFORE the text is sent to Hermes, so "turn off wake word"
                               changes the setting directly instead of being a question for the AI)

Everything here is plain Python with no network use of its own; the bridge passes in an `Env` (config, saving,
profile updater, features, updater, ...), which is also how the tests drive it with fakes.

Confirmation rules (`confirm`): "always" = ask first, every time; "on" = ask only when switching it ON (opens the microphone
or the camera, lets ATLAS act on the computer); None = just do it.  A pending confirmation lives for 2 minutes and is answered
with yes / no in the next message (typed or spoken).
"""
import asyncio
import os
import re
import sys
import time
from pathlib import Path

PENDING_TTL = 120

NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen".split())}
YES = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "confirmed", "do it", "go ahead", "please do", "yes please",
       "yes do it", "yes go ahead", "sounds good", "affirmative", "proceed", "correct", "that's right", "absolutely"}
NO = {"no", "nope", "nah", "cancel", "never mind", "nevermind", "don't", "do not", "dont", "stop", "no thanks", "no thank you",
      "negative", "leave it", "forget it", "abort"}

PROVIDER_WORDS = {"chatgpt": "chatgpt", "chat gpt": "chatgpt", "openai": "chatgpt", "gpt": "chatgpt",
                  "claude": "claude", "anthropic": "claude",
                  "grok": "grok", "xai": "grok", "x ai": "grok",
                  "openrouter": "openrouter", "open router": "openrouter",
                  "ollama": "ollama", "local": "ollama", "a local model": "ollama", "local model": "ollama", "my local model": "ollama"}
PROVIDER_LABEL = {"chatgpt": "ChatGPT", "claude": "Claude", "grok": "Grok", "openrouter": "OpenRouter", "ollama": "a local model (Ollama)"}

VOICES = {"alan": "en_GB-alan-medium", "lessac": "en_US-lessac-medium", "amy": "en_US-amy-medium", "ryan": "en_US-ryan-medium",
          "jenny": "en_GB-jenny_dioco-medium"}
SPEEDS = {"slow": 1.2, "normal": 1.0, "fast": 0.85}

PANELS = {"clock": ("Clock", ["clock", "chronometer", "time"]), "weather": ("Weather", ["weather"]),
          "scanner": ("Scanner", ["scanner", "radar", "globe", "location scanner"]), "sports": ("Sports", ["sports", "scores"]),
          "system": ("System", ["system", "system stats", "cpu"]), "hermes": ("Hermes status", ["hermes", "hermes status", "ai status"])}

# optional modules that open the microphone / camera or let ATLAS act on the computer: asking first when they are switched ON
ADDON_CONFIRM = {"wake_word", "computer_control", "gestures", "telegram"}
MODULE_WORDS = {"wake_word": ["wake word", "wake phrase", "wakeword", "wake-word", "hands free mode", "hands-free mode"],
                "daily_brief": ["daily brief", "morning brief", "daily briefing"],
                "reminders": ["reminders", "reminders and timers", "timers"],
                "computer_control": ["computer control", "pc control"],
                "gestures": ["hand gestures", "gestures", "gesture control"],
                "telegram": ["telegram", "telegram phone access", "phone access"]}


class Setting:
    def __init__(self, id, title, group, kind, names=(), ui="general", confirm=None, lo=None, hi=None, choices=None,
                 examples=(), names_inv=(), desc=""):
        self.id, self.title, self.group, self.kind, self.ui, self.confirm = id, title, group, kind, ui, confirm
        self.names, self.names_inv = list(names), list(names_inv)
        self.lo, self.hi, self.choices, self.examples, self.desc = lo, hi, choices, list(examples), desc


def static_registry():
    S = Setting
    return [
        # ---- Profile tab
        S("user.name", "Your name", "Profile", "text", ["my name", "name", "user name", "my first name"], ui="profile",
          examples=["set my name to Adam", "call me Adam"], desc="What ATLAS calls you."),
        S("user.city", "City", "Profile", "text", ["my city", "city", "my location", "location", "my town", "hometown", "weather city"], ui="profile",
          examples=["set my city to Dallas", "I live in Dallas"], desc="Used for the weather and local time."),
        S("user.sports", "Sports and teams", "Profile", "list", ["my sports", "sports", "my teams", "teams", "favorite teams", "favourite teams", "followed teams"],
          ui="profile", confirm="remove-sport", examples=["follow Juventus in soccer", "remove soccer", "stop following Juventus", "what sports do I follow"],
          desc="Sports and teams on the Sports panel."),
        S("assistant.name", "Assistant name", "Profile", "text", ["your name", "assistant name", "assistant's name", "the assistant's name", "name of the assistant", "assistant"],
          ui="profile", examples=["change your name to Nova", "rename yourself to Nova"], desc="What you call ATLAS; the wake phrase is \"Hey <name>\"."),
        # ---- General tab: conversation, sound, voice
        S("conversation.on", "Conversation mode", "Conversation", "bool", ["conversation mode", "conversation", "follow up listening", "follow-up listening", "follow up mode", "follow-up mode"],
          ui="features", examples=["turn off conversation mode", "turn on conversation mode"], desc="Listen again for a few seconds after ATLAS answers."),
        S("conversation.secs", "Listening window", "Conversation", "int", ["listening window", "conversation window", "conversation duration", "conversation length", "follow up window", "follow-up window", "conversation timeout", "conversation time"],
          ui="features", lo=3, hi=15, examples=["set the listening window to 10 seconds", "make the conversation window 8 seconds"], desc="Seconds ATLAS waits for you to start talking (3-15)."),
        S("mute", "Mute (no spoken replies)", "Sound and voice", "bool", ["mute", "muted"], names_inv=["spoken replies", "voice replies", "speech", "sound", "audio", "speaking", "talking", "voice output"],
          examples=["mute", "unmute", "turn off spoken replies"], desc="Replies stay on screen but are not read aloud."),
        S("voice.name", "Voice", "Sound and voice", "enum", ["voice", "speaking voice", "tts voice", "your voice", "assistant voice"], choices=sorted(VOICES),
          examples=["use the lessac voice", "set my voice to amy"], desc="Which built-in voice reads replies (downloads once when first used)."),
        S("voice.speed", "Speaking speed", "Sound and voice", "enum", ["speaking speed", "speech speed", "voice speed", "talking speed", "speed"], choices=list(SPEEDS),
          examples=["speak faster", "set the speaking speed to slow"], desc="slow, normal or fast."),
        # ---- AI connection tab
        S("ai.provider", "AI provider", "AI connection", "enum", ["ai provider", "my ai provider", "provider", "ai", "my ai", "ai connection", "model provider", "ai service", "ai model"],
          ui="ai", confirm="always", choices=sorted(set(PROVIDER_WORDS.values())), examples=["switch to Claude", "change my AI provider to ChatGPT"],
          desc="Changing it opens the sign-in screen for that AI."),
        # ---- system
        S("login", "Start when I sign in", "System", "bool", ["start at login", "start at sign in", "start at sign-in", "start when i log in", "start on login", "launch at login", "run at login", "startup", "start at startup"],
          examples=["turn on start at login", "turn off start at login"], desc="Starts the ATLAS background part when you sign in to your computer."),
        S("updates.auto", "Automatic update checks", "System", "bool", ["automatic updates", "auto updates", "auto update", "automatic update checks", "update checks", "updates", "auto-updates"],
          examples=["turn off automatic updates", "turn on automatic updates"], desc="Check for new ATLAS versions in the background."),
        S("updates.check", "Check for updates", "System", "action", ["check for updates"], examples=["check for updates"], desc="Looks for a new ATLAS version now."),
        S("updates.install", "Install the update", "System", "action", ["install the update", "install updates"], confirm="always", examples=["install the update"],
          desc="Downloads and installs a waiting update, then restarts ATLAS."),
        S("reset", "Reset settings", "System", "action", ["reset settings", "reset my settings", "factory reset"], confirm="always", examples=["reset my settings"],
          desc="Puts your name, city, sports and preferences back to the defaults (your AI sign-in is kept)."),
        S("uninstall", "Uninstall ATLAS", "System", "action", ["uninstall", "uninstall atlas"], confirm="always", examples=["uninstall ATLAS"],
          desc="Explains how to remove ATLAS (it never deletes itself without you running the uninstaller)."),
    ]


def build_registry(env):
    """The static settings + one row per optional module + one per dashboard panel."""
    reg = static_registry()
    for f in env.features.describe():
        fid = f["id"]
        words = list(MODULE_WORDS.get(fid, [])) + [f["title"].lower(), fid.replace("_", " ")]
        if fid == "wake_word":
            words += ["hey " + env.assistant_name().lower()]
        reg.append(Setting("module." + fid, f["title"], "Add-on modules", "bool", words, ui="features",
                           confirm="on" if (fid in ADDON_CONFIRM or f.get("consent")) else None,
                           examples=["turn off " + f["title"].lower(), "turn on " + f["title"].lower()], desc=f.get("description", "")))
    for pid, (title, words) in PANELS.items():
        reg.append(Setting("panel." + pid, title + " panel", "Panel layout", "bool", [w + " panel" for w in words] + [w + " tile" for w in words],
                           ui="general", examples=["hide the %s panel" % pid, "show the %s panel" % pid], desc="Show or hide this tile on the dashboard."))
    return reg


# ------------------------------------------------------------------ text helpers
def clean_utterance(text):
    t = str(text or "").replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    t = re.sub(r"[\"!?;]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .,:")
    return t


def strip_polite(t, name):
    t = re.sub(r"^(?:(?:hey|ok|okay)\s+)?" + re.escape(name) + r"\b[ ,:.]*", "", t, flags=re.I)
    prev = None
    while prev != t:
        prev = t
        t = re.sub(r"^(?:please|can you|could you|would you|will you|i want you to|i would like you to|i'd like you to|go ahead and|just|kindly|now|then|and)\b[ ,]*", "", t, flags=re.I)
    t = re.sub(r"[ ,]*(?:please|for me|right now|now|thanks|thank you)$", "", t, flags=re.I)
    return t.strip(" .,")


def norm(s):
    s = str(s or "").lower().replace("\u2019", "'")
    s = re.sub(r"[^a-z0-9' \-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_bool(s):
    s = norm(s)
    if s in ("on", "true", "yes", "enabled", "enable", "active", "1", "shown", "visible", "show"):
        return True
    if s in ("off", "false", "no", "disabled", "disable", "inactive", "0", "hidden", "hide"):
        return False
    return None


def parse_int(s):
    s = norm(s)
    m = re.search(r"\d+", s)
    if m:
        return int(m.group())
    for w in s.split():
        if w in NUMBER_WORDS:
            return NUMBER_WORDS[w]
    return None


def say_list(items):
    items = list(items)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


class Intent(dict):
    """{id, op: get|set|list|help|run|sports, value, ...}"""


# ------------------------------------------------------------------ the handler
class Settings:
    """env needs:  cfg (dict)  save(patch)  assistant_name()  features (describe()/async set_enabled(fid,on))  updater (enabled, state(), set_enabled(), async check(), async install())
                   update_profile(body)->(profile,cands,msg) [blocking]  sport_names (dict)  voice_changed()  autostart_get()/autostart_set(bool)->str|None  uninstall_hint()  reset()"""

    def __init__(self, env):
        self.env = env
        self.pending = None

    # ---- registry
    def registry(self):
        return build_registry(self.env)

    def by_id(self, sid):
        return next((s for s in self.registry() if s.id == sid), None)

    def resolve(self, name, kinds=None, reg=None, inverted_ok=True):
        """Name a person said -> (Setting, inverted) or (None, False)."""
        reg = reg or self.registry()
        n = norm(name)
        cands = []
        for pre in ("", "the ", "my ", "your ", "a ", "an "):
            if pre and n.startswith(pre):
                cands.append(n[len(pre):])
        cands.insert(0, n)
        out = []
        for c in list(cands):
            out.append(c)
            for suf in (" feature", " module", " setting", " option", " mode", " function"):
                if c.endswith(suf):
                    out.append(c[:-len(suf)])
        for c in out:
            for s in reg:
                if kinds and s.kind not in kinds:
                    continue
                if c in [norm(x) for x in s.names] or c == norm(s.id):
                    return s, False
            if inverted_ok:
                for s in reg:
                    if c in [norm(x) for x in s.names_inv]:
                        return s, True
        return None, False

    # ---- values
    def get(self, s):
        e, cfg = self.env, self.env.cfg
        prefs = cfg.get("prefs") or {}
        if s.id == "user.name":
            return (cfg.get("profile") or {}).get("name", "")
        if s.id == "user.city":
            p = cfg.get("profile") or {}
            return p.get("label") or p.get("city") or ""
        if s.id == "user.sports":
            return (cfg.get("profile") or {}).get("sports") or []
        if s.id == "assistant.name":
            return e.assistant_name()
        if s.id == "conversation.on":
            return bool(prefs.get("conversation_on", True))
        if s.id == "conversation.secs":
            return int(prefs.get("conversation_secs", 6))
        if s.id == "mute":
            return bool(prefs.get("mute", False))
        if s.id == "voice.name":
            cur = (cfg.get("tts") or {}).get("voice", "")
            return next((k for k, v in VOICES.items() if v == cur), cur)
        if s.id == "voice.speed":
            ls = float((cfg.get("tts") or {}).get("length_scale", 1.0))
            return min(SPEEDS, key=lambda k: abs(SPEEDS[k] - ls))
        if s.id == "ai.provider":
            return e.provider_label() if hasattr(e, "provider_label") else ""
        if s.id == "login":
            return bool(e.autostart_get())
        if s.id == "updates.auto":
            return bool(e.updater.enabled)
        if s.id.startswith("module."):
            f = next((f for f in e.features.describe() if f["id"] == s.id[7:]), None)
            return bool(f and f["enabled"])
        if s.id.startswith("panel."):
            return s.id[6:] not in (prefs.get("panels_hidden") or [])
        return None

    def describe(self):
        """Everything the HUD needs to draw the settings that are driven by the registry."""
        out = []
        for s in self.registry():
            d = {"id": s.id, "title": s.title, "group": s.group, "kind": s.kind, "ui": s.ui, "desc": s.desc,
                 "confirm": s.confirm or "", "examples": s.examples, "value": self.get(s) if s.kind != "action" else None}
            if s.kind == "int":
                d["lo"], d["hi"] = s.lo, s.hi
            if s.kind == "enum":
                d["choices"] = s.choices
            out.append(d)
        return out

    def prefs_view(self):
        p = self.env.cfg.get("prefs") or {}
        return {"conversation_on": bool(p.get("conversation_on", True)), "conversation_secs": int(p.get("conversation_secs", 6)),
                "mute": bool(p.get("mute", False)), "panels_hidden": list(p.get("panels_hidden") or [])}

    def _pref(self, key, value):
        prefs = self.env.cfg.setdefault("prefs", {})
        prefs[key] = value
        self.env.save({"prefs": {key: value}})

    # ---- confirmation text
    def needs_confirm(self, s, value):
        if s.confirm == "always":
            return True
        if s.confirm == "on" and value is True:
            return True
        return False

    def confirm_prompt(self, s, value):
        a = self.env.assistant_name()
        if s.id == "module.wake_word":
            return ("Turning on the wake word keeps your microphone open so I can hear \"Hey %s\". It listens on this computer only - nothing is recorded or sent anywhere. "
                    "Do you want me to turn it on? Say yes or no." % a)
        if s.id.startswith("module."):
            return "%s is an add-on that %s Switch it on? Say yes or no." % (s.title, {
                "module.computer_control": "lets me open apps and files on this computer (anything destructive still asks first).",
                "module.gestures": "uses your webcam.", "module.telegram": "lets your phone talk to me through your own Telegram bot."}.get(s.id, "can act on its own."))
        if s.id == "ai.provider":
            return "Switch your AI to %s? I'll open the sign-in screen so you can connect it. Say yes or no." % PROVIDER_LABEL.get(value, str(value))
        if s.id == "updates.install":
            return "Install the waiting update and restart %s? Say yes or no." % a
        if s.id == "reset":
            return "Reset your name, city, sports and preferences to the defaults? Your AI sign-in stays. This can't be undone. Say yes or no."
        if s.id == "uninstall":
            return "Do you want to uninstall %s? I'll tell you how; I won't delete anything myself. Say yes or no." % a
        return "Change %s? Say yes or no." % s.title.lower()

    # ================================================================== parse (no side effects)
    def parse(self, text):
        name = self.env.assistant_name()
        t = strip_polite(clean_utterance(text), name)
        if not t:
            return None
        reg = self.registry()
        low = norm(t)

        if re.fullmatch(r"(?:what are|what is|what's|whats|show|list|read|tell me|give me)(?: me)?(?: out)?(?: all)?(?: of)?(?: my| the| your)?(?: current)? settings(?: are| right now)?", low) \
                or low in ("what are my settings", "settings", "what settings do i have", "read my settings"):
            return Intent(op="list")
        if re.fullmatch(r"what (?:can|could) i (?:change|set|control|configure)(?: by voice| with my voice)?|what settings can i (?:change|set|control)|what can i say(?: to change settings)?|help with settings", low):
            return Intent(op="help")

        m = re.fullmatch(r"(mute|unmute)(?: yourself| the sound| the audio| sound| audio| voice| replies| spoken replies| the voice)?", low)
        if m:
            return Intent(op="set", id="mute", value=(m.group(1) == "mute"))
        m = re.fullmatch(r"(?:speak|talk) (faster|slower|normally|more slowly|quicker)", low)
        if m:
            return Intent(op="set", id="voice.speed", value={"faster": "fast", "quicker": "fast", "slower": "slow", "more slowly": "slow", "normally": "normal"}[m.group(1)])

        # ---- sports / teams
        it = self._parse_sports(t, low)
        if it:
            return it

        # ---- queries
        m = re.fullmatch(r"(?:what|which) (?:sports|teams)(?: am i| do i)? (?:following|follow|have|tracking)", low) or re.fullmatch(r"(?:what|which) (?:sports|teams)(?: do i| am i)? (?:follow|following)", low)
        if m:
            return Intent(op="get", id="user.sports")
        m = re.fullmatch(r"(?:am i|are you) (muted|mute)", low)
        if m:
            return Intent(op="get", id="mute")
        m = re.fullmatch(r"is (?:my |the |your )?(.+?) (on|off|enabled|disabled|muted|shown|hidden|visible)", low)
        if m:
            s, inv = self.resolve(m.group(1), reg=reg)
            if s and s.kind == "bool":
                return Intent(op="get", id=s.id)
        m = re.fullmatch(r"(?:what is|what's|whats|what are|which is|which|how long is|how is|tell me|read me|read out|give me)\s+(.+?)(?: currently| right now| set to| set at| set for| at the moment)*", low)
        if m:
            x = m.group(1)
            s, inv = self.resolve(x, reg=reg)
            if s and s.kind != "action":
                return Intent(op="get", id=s.id)

        # ---- updates / reset / uninstall (actions)
        if re.fullmatch(r"(?:check|look) for (?:an? )?(?:new )?updates?(?: available)?|are there any updates?|is there an? update(?: available)?|is there a new version(?: of .+)?|check (?:if )?(?:there is |there's )?an update", low):
            return Intent(op="run", id="updates.check")
        if re.fullmatch(r"(?:install|apply|download|get) (?:the |an? |any |all )?(?:waiting |new |available |latest )?updates?|update yourself|update " + re.escape(norm(name)), low):
            return Intent(op="run", id="updates.install")
        if re.fullmatch(r"(?:reset|restore)(?: all| everything in)?(?: my| the| your)? (?:settings|preferences|defaults)(?: to (?:the )?(?:defaults?|factory settings))?|factory reset|reset " + re.escape(norm(name)), low):
            return Intent(op="run", id="reset")
        if re.fullmatch(r"uninstall(?: " + re.escape(norm(name)) + r"| yourself|)|remove " + re.escape(norm(name)) + r" from (?:my|this) (?:computer|pc|laptop|mac)|delete " + re.escape(norm(name)), low):
            return Intent(op="run", id="uninstall")

        # ---- AI provider
        m = re.fullmatch(r"(?:switch|change|go|move|swap)(?: (?:my|the|your))?(?: ai| assistant| model| brain)?(?: provider| connection| service)?(?: over)? to (.+)", low) \
            or re.fullmatch(r"(?:use|sign in to|log in to|login to|connect|connect to|start using) (.+)", low) \
            or re.fullmatch(r"(?:set|change|switch)(?: (?:my|the|your))? (?:ai|ai provider|provider|ai connection|model provider|ai service) (?:to|as) (.+)", low) \
            or re.fullmatch(r"(?:sign in|log in|login) (?:with|using) (.+)", low)
        if m:
            p = PROVIDER_WORDS.get(re.sub(r"^(?:the |my |a |an )", "", m.group(1)).strip()) or PROVIDER_WORDS.get(m.group(1).strip())
            if p:
                return Intent(op="set", id="ai.provider", value=p)
        if re.fullmatch(r"(?:change|switch|set up|pick)(?: (?:my|the|your))? (?:ai|ai provider|provider|ai connection|model provider|ai service)", low):
            return Intent(op="get", id="ai.provider", ask_choice=True)

        # ---- voice
        m = re.fullmatch(r"(?:use|switch to|change to|try)(?: (?:the|a|my))? (.+?) voice", low)
        if m and (m.group(1).strip() in VOICES):
            return Intent(op="set", id="voice.name", value=m.group(1).strip())

        # ---- names, city
        m = re.fullmatch(r"(?:call me|please call me|you can call me|my name is|i am called|i'm called|set my name to)\s+(.+)", t, flags=re.I)
        if m and len(m.group(1)) <= 40:
            return Intent(op="set", id="user.name", value=m.group(1).strip(" ."))
        m = re.fullmatch(r"(?:call yourself|rename yourself(?: to| as)?|your name is(?: now)?|from now on (?:your name is|call yourself|you are called)|you are now called|i'll call you|i will call you|i want to call you)\s+(.+)", t, flags=re.I)
        if m:
            return Intent(op="set", id="assistant.name", value=m.group(1).strip(" ."))
        m = re.fullmatch(r"(?:my city is|my location is|i live in|i'm from|i am from|set my city to)\s+(.+)", t, flags=re.I)
        if m:
            return Intent(op="set", id="user.city", value=m.group(1).strip(" ."))

        # ---- hide / show panels (accept the bare panel name)
        m = re.fullmatch(r"(hide|show|display|remove|bring back)\s+(?:the\s+|my\s+)?(.+)", low)
        if m:
            x = m.group(2)
            s, _ = self.resolve(x, reg=reg)
            if not s or not s.id.startswith("panel."):
                s, _ = self.resolve(re.sub(r"\s+(?:panel|tile|widget)$", "", x) + " panel", reg=reg)
            if s and s.id.startswith("panel."):
                return Intent(op="set", id=s.id, value=(m.group(1) not in ("hide", "remove")))

        # ---- on / off
        m = re.fullmatch(r"(?:turn|switch|set|put|flip)\s+(on|off)\s+(.+)", low)
        if m:
            s, inv = self.resolve(m.group(2), reg=reg)
            if s and s.kind == "bool":
                return Intent(op="set", id=s.id, value=(m.group(1) == "on") != inv)
        m = re.fullmatch(r"(?:turn|switch|set|put|flip)\s+(.+?)\s+(on|off)", low)
        if m:
            s, inv = self.resolve(m.group(1), reg=reg)
            if s and s.kind == "bool":
                return Intent(op="set", id=s.id, value=(m.group(2) == "on") != inv)
        m = re.fullmatch(r"(enable|disable|activate|deactivate|start using|stop using|stop listening for|start listening for|stop|start|quit|end)\s+(.+)", low)
        if m:
            s, inv = self.resolve(m.group(2), reg=reg)
            if s and s.kind == "bool":
                on = m.group(1) in ("enable", "activate", "start using", "start listening for", "start")
                return Intent(op="set", id=s.id, value=on != inv)
        m = re.fullmatch(r"(?:set|change|update|make|put)\s+((?:my |the |your )?.+?)\s+(?:to|as|equal to|=)\s+(.+)", t, flags=re.I)
        if m:
            s, inv = self.resolve(m.group(1), reg=reg)
            if s and s.kind != "action":
                v = m.group(2).strip(" .")
                if s.kind == "bool":
                    b = parse_bool(v)
                    if b is not None:
                        return Intent(op="set", id=s.id, value=b != inv)
                else:
                    return Intent(op="set", id=s.id, value=v)
        m = re.fullmatch(r"(?:set|make|change)\s+((?:my |the |your )?.+?)\s+(?:to\s+)?(\d+|[a-z]+)\s*(?:seconds?|secs?|s)", low)
        if m:
            s, _ = self.resolve(m.group(1), kinds=("int",), reg=reg)
            if s:
                return Intent(op="set", id=s.id, value=m.group(2))
        return None

    # ---- sports grammar
    def _sport_in(self, x):
        x = norm(x)
        for k, v in list(self.env.sport_names.items()) + [(s.lower(), s) for s in ("Soccer", "Tennis", "Golf", "Cricket", "Rugby", "Basketball", "Baseball", "Hockey", "Volleyball", "Boxing", "UFC", "Cycling", "Formula 1", "NASCAR", "WNBA", "NWSL")]:
            if x == norm(k):
                return v
        return None

    def _parse_sports(self, t, low):
        cfg_sports = (self.env.cfg.get("profile") or {}).get("sports") or []
        m = re.fullmatch(r"(?:remove|delete|drop|unfollow|stop following|stop tracking|stop showing|stop watching|no more|clear|get rid of|take off|stop caring about)\s+(?:my |the |all my |all |every )?(.+?)(?:\s+from\s+(?:my |the )?(.+))?", t, flags=re.I)
        if m:
            x, frm = m.group(1).strip(), (m.group(2) or "").strip()
            sporty = bool(frm and re.search(r"sport|team|favou?rite|following|follow|league|panel", frm)) or low.startswith(("unfollow", "stop following", "stop tracking", "stop showing", "stop watching", "stop caring"))
            if re.fullmatch(r"(?:all )?(?:of )?(?:my )?(?:sports|teams|sports and teams|sports teams|favou?rite teams|favou?rites|followed teams|everything)", x) and (sporty or x in ("sports", "teams", "sports and teams")):
                return Intent(op="sports", action="clear")
            hit = self._find_followed(x, cfg_sports, frm)
            if hit:
                return Intent(op="sports", action="remove", **hit)
            if sporty or self._sport_in(x):
                return Intent(op="sports", action="missing", what=x)
            return None
        m = re.fullmatch(r"(?:add|follow|start following|start tracking|track|i follow|i support|i'm a fan of|i am a fan of)\s+(.+)", t, flags=re.I)
        if m:
            body = m.group(1).strip()
            if re.fullmatch(r"(?:an? )?(?:new )?(?:alarm|reminder|timer|note|event|meeting|task|item|song|playlist)\b.*", body, flags=re.I):
                return None
            mm = re.fullmatch(r"(.+?)\s+to\s+(?:my\s+)?(.+?)\s+(leagues?|teams?|conferences?)", body, flags=re.I)
            if mm and not self._is_reminder_like(mm.group(1)):
                sp = self._sport_in(mm.group(2))
                if sp:
                    return Intent(op="sports", action="add_" + ("league" if mm.group(3).lower().startswith(("league", "conf")) else "team"), sport=sp, name=mm.group(1).strip())
            explicit = False
            mm = re.fullmatch(r"(.+?)\s+(?:to|in|on)\s+(?:my\s+)?(?:sports?|teams?|favou?rites?|list|followed teams|sports panel|sports list)", body, flags=re.I)
            if mm:
                body, explicit = mm.group(1).strip(), True
            mm = re.fullmatch(r"(.+?)\s+(?:in|for)\s+(?:the\s+)?(.+)", body, flags=re.I)
            if mm and self._sport_in(mm.group(2)):
                return Intent(op="sports", action="add_team", sport=self._sport_in(mm.group(2)), name=mm.group(1).strip())
            sp = self._sport_in(body)
            if sp:
                return Intent(op="sports", action="add_sport", sport=sp)
            if (explicit or low.startswith(("follow", "start following", "i follow", "i support", "i'm a fan", "i am a fan"))) and 1 <= len(body.split()) <= 4:
                return Intent(op="sports", action="add_team", sport="", name=body)
            return None
        return None

    def _is_reminder_like(self, s):
        return bool(re.search(r"\b(?:milk|eggs|bread|meeting|reminder|alarm|timer)\b", s, flags=re.I))

    def _find_followed(self, x, sports, frm=""):
        nx = norm(x)
        for e in sports:                                   # a whole sport
            if norm(e["sport"]) == nx or (self._sport_in(x) or "").lower() == e["sport"].lower():
                return {"sport": e["sport"], "kind": "sport"}
        frm_sp = self._sport_in(frm) if frm else None
        for e in sports:                                   # an exact team or league
            if frm_sp and e["sport"].lower() != frm_sp.lower():
                continue
            for kind, key in (("team", "teams"), ("league", "leagues")):
                for v in e.get(key) or []:
                    if norm(v) == nx:
                        return {"sport": e["sport"], "kind": kind, "name": v}
        subs = []
        for e in sports:
            for kind, key in (("team", "teams"), ("league", "leagues")):
                for v in e.get(key) or []:
                    if len(nx) >= 3 and (nx in norm(v).split() or norm(v).startswith(nx) or nx in norm(v)):
                        subs.append({"sport": e["sport"], "kind": kind, "name": v})
        return subs[0] if len(subs) == 1 else None

    # ================================================================== act
    async def handle(self, text):
        """Returns None (not a settings request: send it to Hermes) or
        {reply, changed:[ids], ui:{...}|None, pending:bool}"""
        self._expire()
        t = strip_polite(clean_utterance(text), self.env.assistant_name())
        low = norm(t)
        if self.pending:
            if low in YES or low in {y + " please" for y in YES}:
                p, self.pending = self.pending, None
                return await self._commit(p)
            if low in NO:
                self.pending = None
                return {"reply": "Okay, I left it as it is.", "changed": [], "ui": None, "pending": False}
            self.pending = None            # anything else = they moved on
        it = self.parse(text)
        if not it:
            return None
        return await self.execute(it)

    def _expire(self):
        if self.pending and time.time() - self.pending["ts"] > PENDING_TTL:
            self.pending = None

    def _ask(self, s, value, prompt=None, extra=None, op="set"):
        self.pending = {"ts": time.time(), "intent": Intent(op=op, id=s.id, value=value, **(extra or {}))}
        return {"reply": prompt or self.confirm_prompt(s, value), "changed": [], "ui": None, "pending": True}

    async def _commit(self, p):
        it = p["intent"]
        if it.get("op") == "sports":
            return await self._sports_apply(it)
        return await self.execute(it, confirmed=True)

    async def execute(self, it, confirmed=False):
        op = it["op"]
        e = self.env
        if op == "list":
            return self._r(self._overview())
        if op == "help":
            return self._r("You can say things like: turn off the wake word, mute, turn off conversation mode, set the listening window to 10 seconds, change your name to Nova, "
                           "set my city to Dallas, remove soccer, stop following Juventus, switch to Claude, hide the weather panel, turn off automatic updates, "
                           "or what are my settings. Risky ones, like switching the AI, turning the microphone on or resetting, always ask you first.")
        if op == "sports":
            return await self._sports_exec(it)
        s = self.by_id(it["id"])
        if not s:
            return None
        if op == "get":
            if it.get("ask_choice"):
                return self._r("Which AI do you want: ChatGPT, Claude, Grok, OpenRouter, or a local model? Say, for example, switch to Claude.")
            return self._r(self._read(s))
        if op == "run":
            return await self._run_action(s, confirmed)
        # ---- set
        try:
            value = self._coerce(s, it["value"])
        except ValueError as err:
            return self._r(str(err))
        if self.needs_confirm(s, value) and not confirmed:
            return self._ask(s, value)
        return await self._apply(s, value)

    def _r(self, reply, changed=(), ui=None):
        return {"reply": reply, "changed": list(changed), "ui": ui, "pending": False}

    def _coerce(self, s, v):
        if s.kind == "bool":
            if isinstance(v, bool):
                return v
            b = parse_bool(v)
            if b is None:
                raise ValueError("Say on or off for %s." % s.title.lower())
            return b
        if s.kind == "int":
            n = v if isinstance(v, int) and not isinstance(v, bool) else parse_int(v)
            if n is None:
                raise ValueError("Tell me a number of seconds between %d and %d." % (s.lo, s.hi))
            if n < s.lo or n > s.hi:
                raise ValueError("%s can be %d to %d seconds. Tell me a number in that range." % (s.title, s.lo, s.hi))
            return n
        if s.kind == "enum":
            vv = norm(v)
            if s.id == "ai.provider":
                p = PROVIDER_WORDS.get(vv)
                if not p:
                    raise ValueError("I know ChatGPT, Claude, Grok, OpenRouter and local models. Which one?")
                return p
            vv = re.sub(r"\s*voice$", "", vv)
            if s.id == "voice.speed":
                vv = {"slow": "slow", "slowly": "slow", "slower": "slow", "normal": "normal", "medium": "normal", "fast": "fast", "quick": "fast", "faster": "fast"}.get(vv, vv)
            if vv not in s.choices:
                raise ValueError("%s can be %s." % (s.title, say_list(s.choices)))
            return vv
        if s.kind == "text":
            return str(v).strip()
        return v

    # ---- apply a (confirmed, coerced) value
    async def _apply(self, s, value):
        e = self.env
        a = e.assistant_name()
        sid = s.id
        loop = asyncio.get_running_loop()
        if sid == "mute":
            self._pref("mute", bool(value))
            return self._r("Muted. I'll reply in text only. Say or type unmute to hear me again." if value else "Unmuted. I'll read my replies aloud again.", ["mute"])
        if sid == "conversation.on":
            self._pref("conversation_on", bool(value))
            return self._r("Conversation mode is on. After I answer, I'll listen for your reply for %d seconds." % self.get(self.by_id("conversation.secs")) if value
                           else "Conversation mode is off. You'll need to press the mic or use the wake word each time.", ["conversation.on"])
        if sid == "conversation.secs":
            self._pref("conversation_secs", int(value))
            return self._r("Done. I'll wait %d seconds for your reply after I answer." % value, ["conversation.secs"])
        if sid in ("user.name", "user.city", "assistant.name"):
            body = {"user.name": {"name": value}, "user.city": {"city": value}, "assistant.name": {"assistant": {"name": value}}}[sid]
            try:
                prof, cands, msg = await loop.run_in_executor(None, e.update_profile, body)
            except Exception as err:  # noqa
                return self._r(str(err) if str(err) else "I couldn't change that.")
            if sid == "user.name":
                return self._r("Okay, I'll call you %s." % prof.get("name", value), [sid])
            if sid == "user.city":
                return self._r("Your city is now %s. The weather will follow." % (prof.get("label") or prof.get("city") or value), [sid])
            na = e.assistant_name()
            return self._r("Done - I'm %s now. The wake phrase is Hey %s." % (na, na), [sid])
        if sid == "voice.name":
            e.cfg.setdefault("tts", {})["voice"] = VOICES[value]
            e.save({"tts": {"voice": VOICES[value]}})
            e.voice_changed()
            return self._r("Okay, I'll use the %s voice. The first time can take a moment while it downloads." % value, [sid])
        if sid == "voice.speed":
            e.cfg.setdefault("tts", {})["length_scale"] = SPEEDS[value]
            e.save({"tts": {"length_scale": SPEEDS[value]}})
            return self._r("Okay, I'll speak at %s speed." % value, [sid])
        if sid == "ai.provider":
            return self._r("Opening the sign-in for %s." % PROVIDER_LABEL.get(value, value), [], ui={"open": "ai", "provider": value})
        if sid == "login":
            note = await loop.run_in_executor(None, e.autostart_set, bool(value))
            return self._r(note or ("I'll start in the background when you sign in." if value else "I won't start when you sign in anymore."), ["login"])
        if sid == "updates.auto":
            e.updater.set_enabled(bool(value))
            return self._r("Automatic update checks are on." if value else "Automatic update checks are off. You can still say check for updates.", [sid])
        if sid.startswith("module."):
            fid = sid[7:]
            f = next((f for f in e.features.describe() if f["id"] == fid), None)
            if not f or not f["installed"]:
                return self._r("%s isn't available in this version." % s.title)
            try:
                await e.features.set_enabled(fid, bool(value))
            except Exception as err:  # noqa
                return self._r("I couldn't change %s: %s" % (s.title.lower(), err))
            f = next((f for f in e.features.describe() if f["id"] == fid), {})
            if value:
                extra = ""
                if f.get("status") == "needs_install":
                    extra = " It still needs a one-time download - open Settings, Features, and press Install."
                elif f.get("error"):
                    extra = " It reported a problem: %s" % f["error"]
                return self._r("%s is on.%s" % (s.title, extra), [sid])
            if fid == "wake_word":
                return self._r("Wake word is off, so I can't hear \"Hey %s\" anymore. To turn it back on, use the mic button (push-to-talk), type it in chat, or switch it on in Settings, Features." % a, [sid])
            return self._r("%s is off." % s.title, [sid])
        if sid.startswith("panel."):
            hidden = [p for p in (e.cfg.get("prefs") or {}).get("panels_hidden", [])]
            pid = sid[6:]
            if value and pid in hidden:
                hidden.remove(pid)
            if not value and pid not in hidden:
                hidden.append(pid)
            self._pref("panels_hidden", hidden)
            return self._r("The %s panel is %s." % (PANELS[pid][0].lower(), "back" if value else "hidden"), [sid])
        return self._r("I can't change that one by voice yet.")

    async def _run_action(self, s, confirmed):
        e = self.env
        if s.id == "updates.check":
            st = await e.updater.check(force=True)
            st = e.updater.state()
            if st.get("available"):
                return self._r("There's a new version, %s. Say install the update when you're ready." % (st.get("latest") or "available"))
            return self._r("You're up to date on version %s." % st.get("current", ""))
        if s.confirm == "always" and not confirmed:
            return self._ask(s, True, op="run")
        if s.id == "updates.install":
            st = e.updater.state()
            if not st.get("available"):
                return self._r("There's no update waiting. Say check for updates first.")
            try:
                await e.updater.install()
            except Exception as err:  # noqa
                return self._r("I couldn't install it: %s" % err)
            return self._r("Installing the update now. I'll restart in a moment.", ["updates.install"])
        if s.id == "reset":
            e.reset()
            return self._r("Done. Your name, city, sports and preferences are back to the defaults.", ["reset", "user.name", "user.city", "user.sports", "assistant.name", "mute", "conversation.on", "conversation.secs"])
        if s.id == "uninstall":
            return self._r(e.uninstall_hint())
        return None

    # ---- read-outs
    def _read(self, s):
        v = self.get(s)
        t = s.title.lower()
        if s.id == "user.name":
            return "I call you %s." % v if v else "I don't have your name yet. Say call me, and then your name."
        if s.id == "user.city":
            return "Your city is %s." % v if v else "You haven't set a city yet. Say set my city to, and then the city."
        if s.id == "assistant.name":
            return "My name is %s." % v
        if s.id == "user.sports":
            return self._sports_text(v)
        if s.id == "mute":
            return "I'm muted. I'll answer in text only." if v else "I'm not muted. I read my replies aloud."
        if s.id == "ai.provider":
            return "Your AI is %s." % v if v else "Your AI isn't connected yet."
        if s.kind == "bool":
            if s.id.startswith("panel."):
                return "The %s is %s." % (t, "shown" if v else "hidden")
            return "%s is %s." % (s.title, "on" if v else "off")
        if s.kind == "int":
            return "%s is %d seconds." % (s.title, v)
        return "%s is %s." % (s.title, v)

    def _sports_text(self, sports):
        if not sports:
            return "You're not following any sports yet."
        parts = []
        for e in sports:
            bits = list(e.get("teams") or []) + list(e.get("leagues") or [])
            parts.append(e["sport"] + (" (%s)" % say_list(bits) if bits else ""))
        return "You follow " + say_list(parts) + "."

    def _overview(self):
        bits = []
        for sid in ("user.name", "user.city", "assistant.name"):
            s = self.by_id(sid)
            v = self.get(s)
            bits.append("%s: %s" % (s.title.lower(), v or "not set"))
        sp = self.get(self.by_id("user.sports"))
        bits.append("sports: " + (say_list([x["sport"] for x in sp]) if sp else "none"))
        for sid in ("conversation.on", "conversation.secs", "mute", "ai.provider", "voice.name", "voice.speed", "login", "updates.auto"):
            s = self.by_id(sid)
            v = self.get(s)
            bits.append("%s: %s" % (s.title.lower(), ("on" if v else "off") if s.kind == "bool" else ("%d seconds" % v if s.kind == "int" else (v or "not connected"))))
        for s in self.registry():
            if s.id.startswith("module."):
                bits.append("%s: %s" % (s.title.lower(), "on" if self.get(s) else "off"))
        hid = (self.env.cfg.get("prefs") or {}).get("panels_hidden") or []
        bits.append("hidden panels: " + (say_list(hid) if hid else "none"))
        return "Here are your settings. " + "; ".join(bits) + "."

    # ---- sports execution
    async def _sports_exec(self, it):
        act = it["action"]
        sports = list((self.env.cfg.get("profile") or {}).get("sports") or [])
        if act == "missing":
            return self._r("You're not following %s." % it["what"])
        if act == "clear":
            if not sports:
                return self._r("You're not following any sports yet.")
            self.pending = {"ts": time.time(), "intent": Intent(op="sports", action="clear_confirmed")}
            return {"reply": "Remove all %d of your sports and their teams? Say yes or no." % len(sports), "changed": [], "ui": None, "pending": True}
        if act == "remove" and it["kind"] == "sport":
            e = next(x for x in sports if x["sport"] == it["sport"])
            n = len(e.get("teams") or []) + len(e.get("leagues") or [])
            self.pending = {"ts": time.time(), "intent": Intent(op="sports", action="remove_sport_confirmed", sport=it["sport"])}
            return {"reply": "Remove %s%s from your sports? Say yes or no." % (it["sport"], (" and its %d team%s or league%s" % (n, "" if n == 1 else "s", "" if n == 1 else "s")) if n else ""),
                    "changed": [], "ui": None, "pending": True}
        return await self._sports_apply(it)

    async def _sports_apply(self, it):
        e = self.env
        act = it["action"]
        sports = [dict(x, teams=list(x.get("teams") or []), leagues=list(x.get("leagues") or [])) for x in ((e.cfg.get("profile") or {}).get("sports") or [])]
        reply = ""
        if act == "clear_confirmed":
            sports, reply = [], "Done. You're not following any sports now."
        elif act == "remove_sport_confirmed":
            sports = [x for x in sports if x["sport"] != it["sport"]]
            reply = "Removed %s." % it["sport"] + ("" if sports else " You're not following any sports now.")
        elif act == "remove":                                  # one team or league
            for x in sports:
                if x["sport"] == it["sport"]:
                    key = "teams" if it["kind"] == "team" else "leagues"
                    x[key] = [v for v in x[key] if v != it["name"]]
            reply = "Okay, you're no longer following %s." % it["name"]
        elif act == "add_sport":
            if any(x["sport"].lower() == it["sport"].lower() for x in sports):
                return self._r("You already follow %s." % it["sport"])
            if len(sports) >= 8:
                return self._r("You can follow up to 8 sports. Remove one first.")
            sports.append({"sport": it["sport"], "leagues": [], "teams": []})
            reply = "Now following %s. Open Settings, Profile to add teams, or say follow a team in %s." % (it["sport"], it["sport"])
        elif act in ("add_team", "add_league"):
            sp = it.get("sport") or ""
            if not sp:
                if len(sports) == 1:
                    sp = sports[0]["sport"]
                else:
                    return self._r("Which sport is %s for? Say, for example, follow %s in soccer." % (it["name"], it["name"]))
            tgt = next((x for x in sports if x["sport"].lower() == sp.lower()), None)
            if not tgt:
                if len(sports) >= 8:
                    return self._r("You can follow up to 8 sports. Remove one first.")
                tgt = {"sport": sp, "leagues": [], "teams": []}
                sports.append(tgt)
            key = "teams" if act == "add_team" else "leagues"
            if any(v.lower() == it["name"].lower() for v in tgt[key]):
                return self._r("You already follow %s." % it["name"])
            if len(tgt[key]) >= (10 if key == "teams" else 6):
                return self._r("That's the most %s for %s. Remove one first." % (key, tgt["sport"]))
            tgt[key].append(it["name"])
            reply = "Now following %s in %s." % (it["name"], tgt["sport"])
        loop = asyncio.get_running_loop()
        try:
            prof, _c, _m = await loop.run_in_executor(None, e.update_profile, {"sports": sports})
        except Exception as err:  # noqa
            return self._r(str(err) or "I couldn't save that.")
        return self._r(reply, ["user.sports"])


# ------------------------------------------------------------------ start at login (the same login items the installers create)
def _platform():
    return os.environ.get("ATLAS_PLATFORM") or ("windows" if os.name == "nt" else "mac" if sys.platform == "darwin" else "linux")


def startup_dir():
    if os.environ.get("ATLAS_STARTUP_DIR"):
        return Path(os.environ["ATLAS_STARTUP_DIR"])
    return Path(os.environ.get("APPDATA") or Path.home()) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def launchagents_dir():
    return Path(os.environ.get("ATLAS_LAUNCHAGENTS_DIR") or (Path.home() / "Library" / "LaunchAgents"))


PLIST_LABEL = "com.atlas.bridge"


def autostart_get(plat=None):
    plat = plat or _platform()
    if plat == "windows":
        d = startup_dir()
        return (d / "ATLAS bridge.lnk").exists() or (d / "ATLAS bridge.cmd").exists()
    if plat == "mac":
        return (launchagents_dir() / (PLIST_LABEL + ".plist")).exists()
    return False


def autostart_set(on, bridge_dir, data_dir, plat=None):
    """Create / remove the same login item the installer makes. Returns a sentence for the user, or None for the default one."""
    plat = plat or _platform()
    bridge_dir = Path(bridge_dir)
    if plat == "windows":
        d = startup_dir()
        d.mkdir(parents=True, exist_ok=True)
        for f in (d / "ATLAS bridge.lnk", d / "ATLAS bridge.cmd"):
            if f.exists():
                f.unlink()
        if on:
            ps = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            ps1 = bridge_dir.parent / "win" / "ATLAS.ps1"
            line = 'start "" /min "%s" -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "%s" -Mode Bridge' % (ps, ps1)
            (d / "ATLAS bridge.cmd").write_text("@echo off\r\n" + line + "\r\n", encoding="ascii", errors="replace")
        return None
    if plat == "mac":
        p = launchagents_dir() / (PLIST_LABEL + ".plist")
        if on:
            p.parent.mkdir(parents=True, exist_ok=True)
            logs = Path(data_dir) / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            p.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                         '<plist version="1.0"><dict>\n  <key>Label</key><string>%s</string>\n  <key>ProgramArguments</key><array><string>%s</string><string>%s</string></array>\n'
                         '  <key>WorkingDirectory</key><string>%s</string>\n  <key>RunAtLoad</key><true/>\n'
                         '  <key>StandardOutPath</key><string>%s</string>\n  <key>StandardErrorPath</key><string>%s</string>\n</dict></plist>\n'
                         % (PLIST_LABEL, sys.executable, bridge_dir / "bridge.py", bridge_dir, logs / "bridge.log", logs / "bridge.log"), encoding="utf-8")
        elif p.exists():
            p.unlink()
        return None
    return "Start at login isn't set up on this kind of computer."
