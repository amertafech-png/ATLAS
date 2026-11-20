"""Settings registry + voice/chat intent handler (atlas_settings.py), driven with a fake environment (no network, no bridge).
Run:  python bridge/dev/test_settings.py"""
import asyncio
import copy
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import atlas_settings as A  # noqa: E402

fails = []


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " :: %s" % (info,)))
    if not ok:
        fails.append(name)


class FakeFeatures:
    def __init__(self):
        self.f = {k: {"id": k, "title": t, "description": "d", "installed": True, "enabled": False, "status": "off", "missing": [], "error": "", "ui": False, "consent": c, "settings": {}}
                  for k, t, c in [("wake_word", "Wake word", "mic"), ("daily_brief", "Daily brief", ""), ("reminders", "Reminders & timers", ""),
                                  ("computer_control", "Computer control", ""), ("gestures", "Hand gestures", ""), ("telegram", "Telegram phone access", "")]}
        self.calls = []

    def describe(self):
        return [dict(v) for v in self.f.values()]

    async def set_enabled(self, fid, on, settings=None):
        self.calls.append((fid, on))
        self.f[fid]["enabled"] = on
        self.f[fid]["status"] = "running" if on else "off"


class FakeUpdater:
    def __init__(self):
        self.enabled, self.avail, self.installed = True, False, False

    def set_enabled(self, on):
        self.enabled = bool(on)

    async def check(self, force=False):
        return None

    def state(self):
        return {"available": self.avail, "latest": "9.9.9", "current": "1.3.0"}

    async def install(self):
        self.installed = True


class FakeEnv:
    def __init__(self):
        self.cfg = {"profile": {"name": "", "city": "", "label": "", "sports": []}, "assistant": {"name": "ATLAS"}, "tts": {"voice": "en_GB-alan-medium", "length_scale": 1.0}}
        self.saved = []
        self.features, self.updater = FakeFeatures(), FakeUpdater()
        self.sport_names = {"soccer": "Soccer", "nfl": "NFL", "nba": "NBA", "f1": "F1", "college football": "College football"}
        self.auto = False
        self.voice_reloads = 0
        self.did_reset = False

    def save(self, patch):
        self.saved.append(copy.deepcopy(patch))

    def assistant_name(self):
        return self.cfg["assistant"]["name"]

    def update_profile(self, body):
        p = self.cfg["profile"]
        if "name" in body:
            p["name"] = body["name"]
        if "city" in body:
            p["city"] = body["city"]
            p["label"] = body["city"] + (", Texas" if body["city"] == "Dallas" else "")
        if "sports" in body:
            p["sports"] = copy.deepcopy(body["sports"])
        if "assistant" in body:
            self.cfg["assistant"] = {"name": body["assistant"]["name"]}
        return p, [], ""

    def provider_label(self):
        return "ChatGPT"

    def voice_changed(self):
        self.voice_reloads += 1

    def autostart_get(self):
        return self.auto

    def autostart_set(self, on):
        self.auto = on

    def uninstall_hint(self):
        return "To uninstall run the installer with uninstall. I haven't deleted anything."

    def reset(self):
        self.did_reset = True
        self.cfg["profile"] = {"name": "", "city": "", "label": "", "sports": []}
        self.cfg["prefs"] = {}


def fresh():
    env = FakeEnv()
    return env, A.Settings(env)


def run(coro):
    return asyncio.run(coro)


def say(S, text):
    return run(S.handle(text))


# ------------------------------------------------------------------ 1. every registry entry is reachable by at least one phrase
env, S = fresh()
reg = S.registry()
ids = [s.id for s in reg]
check("registry ids are unique", len(ids) == len(set(ids)), ids)
check("registry covers profile, assistant, sports, wake word, mute, conversation + window, AI provider, voice, modules, panels, login, updates, reset, uninstall",
      all(i in ids for i in ["user.name", "user.city", "user.sports", "assistant.name", "module.wake_word", "mute", "conversation.on", "conversation.secs", "ai.provider",
                             "voice.name", "voice.speed", "login", "updates.auto", "updates.check", "updates.install", "reset", "uninstall", "module.computer_control", "module.telegram",
                             "panel.weather", "panel.sports", "panel.system"]))
check("every entry says where it lives in Settings (ui) and has a title/group", all(s.ui in ("profile", "features", "ai", "general") and s.title and s.group for s in reg))
unreached = []
for s in reg:
    if not s.examples:
        unreached.append((s.id, "no example"))
        continue
    for ex in s.examples:
        it = S.parse(ex)
        got = (it or {}).get("id") or ("user.sports" if (it or {}).get("op") == "sports" else None)
        if not it or got != s.id:
            unreached.append((s.id, ex, it))
check("every registry entry is reached by each of its example phrases", not unreached, unreached)
bad = []
for s in reg:
    for n in s.names:
        r, inv = S.resolve(n)
        if r is None or r.id != s.id or inv:
            bad.append((s.id, n, r and r.id))
check("every friendly name resolves back to its own setting", not bad, bad)
bad = []
for s in reg:
    if s.kind == "bool":
        for n in s.names:
            it = S.parse("turn off " + n)
            if not it or it.get("id") != s.id:
                bad.append((s.id, n, it))
check("'turn off <name>' works for every on/off setting and every one of its names", not bad, bad[:6])
check("describe() (what the Settings screen draws) lists every entry with value/kind", [d["id"] for d in S.describe()] == ids)

# ------------------------------------------------------------------ 2. the requested examples
env, S = fresh()
env.features.f["wake_word"]["enabled"] = True
r = say(S, "turn off wake word")
check("'turn off wake word' switches it off directly (no confirmation)", env.features.calls == [("wake_word", False)] and not r["pending"], r)
check("...tells you it can't hear 'Hey ATLAS' now and how to turn it back on (push-to-talk, chat, Settings)",
      "can't hear" in r["reply"] and "push-to-talk" in r["reply"] and "chat" in r["reply"] and "Settings" in r["reply"], r["reply"])
r = say(S, "turn on wake word")
check("'turn on wake word' asks first (it opens the microphone)", r["pending"] and "microphone" in r["reply"] and env.features.calls == [("wake_word", False)], r)
r = say(S, "no")
check("'no' cancels and nothing changes", not env.features.f["wake_word"]["enabled"] and "left it" in r["reply"], r)
r = say(S, "hey atlas turn on the wake word")
r = say(S, "yes")
check("'yes' then turns it on", env.features.f["wake_word"]["enabled"] and "Wake word is on" in r["reply"], r)
check("a confirmed mic change is reported as changed (HUD refreshes)", "module.wake_word" in r["changed"])
env.features.f["wake_word"]["enabled"] = False
r = say(S, "turn on wake word"); check("pending again", r["pending"])
r = say(S, "what's the weather")
check("an unrelated message cancels the pending confirmation and goes to Hermes", r is None and S.pending is None)
r = say(S, "yes")
check("a stray 'yes' later does NOT switch the mic on", not env.features.f["wake_word"]["enabled"] and r is None, r)
r = say(S, "turn on wake word")
S.pending["ts"] -= 500
r = say(S, "yes")
check("an old confirmation (> 2 min) expires", not env.features.f["wake_word"]["enabled"] and r is None)

env, S = fresh()
r = say(S, "mute"); check("'mute' -> mute pref on, persisted", env.cfg["prefs"]["mute"] is True and {"prefs": {"mute": True}} in env.saved and "text only" in r["reply"], r)
r = say(S, "unmute"); check("'unmute'", env.cfg["prefs"]["mute"] is False)
r = say(S, "turn off spoken replies"); check("'turn off spoken replies' = mute (inverted wording)", env.cfg["prefs"]["mute"] is True)
r = say(S, "turn on the sound"); check("'turn on the sound' = unmute", env.cfg["prefs"]["mute"] is False)
r = say(S, "turn off conversation mode"); check("'turn off conversation mode'", env.cfg["prefs"]["conversation_on"] is False and "off" in r["reply"] and r["changed"] == ["conversation.on"], r)
r = say(S, "turn on conversation mode"); check("'turn on conversation mode'", env.cfg["prefs"]["conversation_on"] is True and "6 seconds" in r["reply"], r)
r = say(S, "set the listening window to 10 seconds"); check("'set the listening window to 10 seconds'", env.cfg["prefs"]["conversation_secs"] == 10, r)
r = say(S, "make the conversation window eight seconds"); check("number words work", env.cfg["prefs"]["conversation_secs"] == 8, r)
r = say(S, "set the listening window to 40 seconds"); check("out-of-range is refused politely (3-15) and not saved", env.cfg["prefs"]["conversation_secs"] == 8 and "3 to 15" in r["reply"], r)
r = say(S, "change your name to Nova"); check("'change your name to Nova' -> assistant name", env.cfg["assistant"]["name"] == "Nova" and "Hey Nova" in r["reply"], r)
check("the registry follows the new name (wake phrase 'hey nova')", S.resolve("hey nova")[0].id == "module.wake_word")
r = say(S, "call me Adam"); check("'call me Adam'", env.cfg["profile"]["name"] == "Adam", r)
r = say(S, "set my city to Dallas"); check("'set my city to Dallas' with a spoken confirmation", env.cfg["profile"]["city"] == "Dallas" and "Dallas" in r["reply"], r)
r = say(S, "what is my city set to"); check("'what is my city set to'", "Dallas" in r["reply"], r)
r = say(S, "what's my name"); check("'what's my name'", "Adam" in r["reply"], r)
r = say(S, "what's your name"); check("'what's your name'", "Nova" in r["reply"], r)
r = say(S, "what is the listening window set to"); check("'what is the listening window set to'", "8 seconds" in r["reply"], r)
r = say(S, "is conversation mode on"); check("'is conversation mode on'", "on" in r["reply"], r)
r = say(S, "what are my settings")
check("settings overview mentions each group", all(w.lower() in r["reply"].lower() for w in ["Adam", "Dallas", "Nova", "sports", "mute", "conversation mode", "listening window", "ChatGPT", "wake word", "hidden panels"]), r["reply"])

# modules + risky confirmations
env, S = fresh()
r = say(S, "turn on computer control"); check("add-on module ON needs a confirmation", r["pending"] and not env.features.f["computer_control"]["enabled"], r)
say(S, "yes"); check("...then it is on", env.features.f["computer_control"]["enabled"])
r = say(S, "turn off computer control"); check("add-on module OFF needs no confirmation", not r["pending"] and not env.features.f["computer_control"]["enabled"])
r = say(S, "turn on the daily brief"); check("a harmless module (daily brief) switches on directly", not r["pending"] and env.features.f["daily_brief"]["enabled"], r)
r = say(S, "turn on telegram"); check("telegram ON asks", r["pending"]); say(S, "cancel")
r = say(S, "enable hand gestures"); check("gestures (webcam) ON asks", r["pending"]); say(S, "no")
# AI provider
r = say(S, "switch to Claude"); check("AI provider switch asks first", r["pending"] and "Claude" in r["reply"], r)
r = say(S, "yes"); check("...then opens the sign-in flow for it", r["ui"] == {"open": "ai", "provider": "claude"}, r)
r = say(S, "change my AI provider to ChatGPT"); check("'change my AI provider to ChatGPT' asks", r["pending"]); say(S, "no")
r = say(S, "change my ai provider"); check("'change my AI provider' lists the choices", "ChatGPT, Claude" in r["reply"] and not r["pending"], r)
# reset / uninstall / updates
r = say(S, "reset my settings"); check("reset asks first", r["pending"] and not env.did_reset)
say(S, "no"); check("...no -> nothing reset", not env.did_reset)
say(S, "reset my settings"); r = say(S, "yes"); check("...yes -> reset", env.did_reset and "defaults" in r["reply"], r)
r = say(S, "uninstall ATLAS"); check("uninstall asks first", r["pending"])
r = say(S, "yes"); check("...then explains how, deletes nothing", "uninstall" in r["reply"] and "haven't deleted" in r["reply"], r)
r = say(S, "check for updates"); check("'check for updates' (no update)", "up to date" in r["reply"], r)
env.updater.avail = True
r = say(S, "check for updates"); check("'check for updates' (new version)", "9.9.9" in r["reply"], r)
r = say(S, "install the update"); check("installing an update asks first", r["pending"] and not env.updater.installed)
say(S, "yes"); check("...yes -> installs", env.updater.installed)
r = say(S, "turn off automatic updates"); check("automatic updates off", env.updater.enabled is False, r)
r = say(S, "turn on start at login"); check("start at login on", env.auto is True, r)
r = say(S, "use the lessac voice"); check("voice change saves + reloads the engine", env.cfg["tts"]["voice"] == "en_US-lessac-medium" and env.voice_reloads == 1, r)
r = say(S, "speak faster"); check("speak faster", env.cfg["tts"]["length_scale"] == 0.85, r)
r = say(S, "set the speaking speed to slow"); check("speed slow", env.cfg["tts"]["length_scale"] == 1.2)
r = say(S, "hide the weather panel"); check("hide the weather panel", env.cfg["prefs"]["panels_hidden"] == ["weather"], r)
r = say(S, "show the weather panel"); check("show the weather panel", env.cfg["prefs"]["panels_hidden"] == [], r)
r = say(S, "turn off the sports panel"); check("'turn off the sports panel'", env.cfg["prefs"]["panels_hidden"] == ["sports"])
r = say(S, "hide system"); check("bare panel name works with hide", "system" in env.cfg["prefs"]["panels_hidden"])

# ------------------------------------------------------------------ 3. sports
env, S = fresh()
r = say(S, "follow soccer"); check("follow a sport", [x["sport"] for x in env.cfg["profile"]["sports"]] == ["Soccer"], r)
r = say(S, "follow Juventus"); check("'follow Juventus' with exactly one sport goes under it", env.cfg["profile"]["sports"][0]["teams"] == ["Juventus"], r)
r = say(S, "add Arsenal to my teams"); check("'add Arsenal to my teams'", "Arsenal" in env.cfg["profile"]["sports"][0]["teams"], r)
r = say(S, "add Serie A to my soccer leagues"); check("add a league", env.cfg["profile"]["sports"][0]["leagues"] == ["Serie A"], r)
r = say(S, "follow the Lakers in NBA"); check("team in another sport creates the sport", [x["sport"] for x in env.cfg["profile"]["sports"]] == ["Soccer", "NBA"], r)
r = say(S, "follow Cowboys"); check("a team with several sports and none named asks which", "Which sport" in r["reply"], r)
r = say(S, "what sports do I follow"); check("'what sports do I follow'", "Soccer" in r["reply"] and "Juventus" in r["reply"] and "NBA" in r["reply"], r)
r = say(S, "stop following Juventus"); check("'stop following Juventus' removes just the team, no confirmation", "Juventus" not in env.cfg["profile"]["sports"][0]["teams"] and not r["pending"] and r["changed"] == ["user.sports"], r)
check("...and keeps the sport and its other teams", env.cfg["profile"]["sports"][0]["teams"] == ["Arsenal"])
r = say(S, "unfollow arsenal"); check("'unfollow arsenal' (case-insensitive)", env.cfg["profile"]["sports"][0]["teams"] == [])
r = say(S, "stop following Barcelona"); check("a team you don't follow is reported", "not following Barcelona" in r["reply"], r)
r = say(S, "remove soccer"); check("'remove soccer' asks first (whole sport)", r["pending"] and "Soccer" in r["reply"] and len(env.cfg["profile"]["sports"]) == 2, r)
r = say(S, "no"); check("...no -> kept", len(env.cfg["profile"]["sports"]) == 2)
say(S, "remove soccer"); r = say(S, "yes"); check("...yes -> removed and persisted", [x["sport"] for x in env.cfg["profile"]["sports"]] == ["NBA"], r)
r = say(S, "remove the nba from my sports"); check("'remove the NBA from my sports' asks", r["pending"], r)
r = say(S, "yes"); check("last removal leaves an empty sports list", env.cfg["profile"]["sports"] == [] and "not following any sports" in r["reply"], r)
r = say(S, "what sports do I follow"); check("empty state is spoken", "not following any" in r["reply"], r)
say(S, "follow soccer"); say(S, "follow nfl")
r = say(S, "remove all my sports"); check("'remove all my sports' asks", r["pending"], r)
say(S, "yes"); check("...clears everything", env.cfg["profile"]["sports"] == [])

# ------------------------------------------------------------------ 4. things that are NOT settings go to Hermes untouched
env, S = fresh()
not_settings = ["what's the weather in Dallas", "what is the capital of France", "remove the file from my desktop", "add milk to my shopping list", "turn on the lights",
                "set a timer for 5 minutes", "stop", "thanks", "that's all", "what time is it", "tell me a joke", "delete the old backups", "what is my favorite color",
                "how is the weather", "open spotify", "play some music", "what's the score of the game", "remind me to call mom", "who won the Juventus game", "show me my calendar",
                "hide my embarrassment", "change the subject", "what are my options for dinner", "stop the music", "start a timer", "use the force", "set an alarm for 7",
                "what is the system uptime", "tell me about the weather panel design", "is it raining", "is the store open", "remove soccer balls from my cart"]
leaks = [(t, say(S, t)) for t in not_settings]
leaks = [(t, r["reply"]) for t, r in leaks if r is not None]
check("ordinary requests are not hijacked", not leaks, leaks)
check("nothing was saved by those", env.saved == [] and env.features.calls == [])

# ------------------------------------------------------------------ 5. start at login (files only; platforms are forced so this runs anywhere)
import os  # noqa: E402
with tempfile.TemporaryDirectory() as td:
    os.environ["ATLAS_STARTUP_DIR"] = str(Path(td) / "Startup")
    os.environ["ATLAS_LAUNCHAGENTS_DIR"] = str(Path(td) / "LA")
    check("windows: off at first", not A.autostart_get("windows"))
    A.autostart_set(True, Path(td) / "app" / "bridge", td, "windows")
    cmd = (Path(td) / "Startup" / "ATLAS bridge.cmd")
    txt = cmd.read_text() if cmd.exists() else ""
    check("windows: on -> Startup\\ATLAS bridge.cmd runs ATLAS.ps1 -Mode Bridge (ASCII, hidden)", A.autostart_get("windows") and "ATLAS.ps1" in txt and "-Mode Bridge" in txt and "-WindowStyle Hidden" in txt and txt.isascii(), txt)
    A.autostart_set(False, Path(td) / "app" / "bridge", td, "windows")
    check("windows: off removes it", not A.autostart_get("windows"))
    A.autostart_set(True, Path(td) / "app" / "bridge", td, "mac")
    pl = (Path(td) / "LA" / "com.atlas.bridge.plist")
    check("mac: on -> LaunchAgent plist with RunAtLoad", A.autostart_get("mac") and "RunAtLoad" in pl.read_text() and "com.atlas.bridge" in pl.read_text())
    A.autostart_set(False, Path(td) / "app" / "bridge", td, "mac")
    check("mac: off removes it", not A.autostart_get("mac"))
    check("linux: says it isn't set up", "isn't set up" in (A.autostart_set(True, td, td, "linux") or ""))

print("FAILED: %s" % fails if fails else "ALL PASSED")
sys.exit(1 if fails else 0)
