"""Full screen: the setting defaults on, voice can enter and leave it, and the launchers add --kiosk only then.

Nothing is launched. The Mac launcher is run with ATLAS_LAUNCH_DRYRUN=1 (it prints the choice and exits
before starting the bridge or touching a browser). Relaunch is recorded to a file, not executed.
  python3 bridge/dev/test_fullscreen.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bridge"))
import atlas_settings as A  # noqa: E402
import atlas_window as W  # noqa: E402

fails = []


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " :: %s" % (info,)))
    if not ok:
        fails.append(name)


# the settings tests' fake env is enough; import its shape by rebuilding a tiny one
class Env:
    def __init__(self):
        self.cfg = {"profile": {"name": "", "city": "", "sports": []}, "assistant": {"name": "ATLAS"}, "tts": {"voice": "en_GB-alan-medium", "length_scale": 1.0}, "prefs": {}}
        self.saved = []
        self.features = type("F", (), {"describe": lambda self: [], "calls": []})()
        self.updater = type("U", (), {"enabled": True})()
        self.sport_names = {}
        self.modules = type("M", (), {"master_on": lambda self: True})()

    def save(self, patch):
        self.saved.append(patch)

    def assistant_name(self):
        return "ATLAS"

    def panels_available(self):
        return set()


env = Env()
S = A.Settings(env)
check("full screen defaults on when the choice was never saved", S.prefs_view()["fullscreen"] is True and S.get(S.by_id("window.fullscreen")) is True)

import asyncio  # noqa: E402


def say(text):
    return asyncio.run(S.handle(text))


r = say("exit full screen")
check("'exit full screen' turns it off and remembers it", r and env.cfg["prefs"].get("fullscreen") is False and "off" in r["reply"].lower() and r["changed"] == ["window.fullscreen"], r)
r = say("full screen")
check("'full screen' turns it back on", r and env.cfg["prefs"].get("fullscreen") is True and "F11" in r["reply"], r)
r = say("exit fullscreen")
check("'exit fullscreen' (one word) turns it off", env.cfg["prefs"].get("fullscreen") is False, r)
r = say("turn on kiosk mode")
check("'turn on kiosk mode' is the same switch", env.cfg["prefs"].get("fullscreen") is True, r)
r = say("turn off full screen")
check("'turn off full screen' turns it off", env.cfg["prefs"].get("fullscreen") is False, r)
check("Settings still reads it back as off", "off" in say("is full screen on")["reply"].lower())

# what the page should do, given how this window was opened
check("enter: a normal window tries the Fullscreen API first", W.plan(True, False, False, False) == "enter")
check("enter refused: relaunch into --kiosk (voice has no click)", W.plan(True, False, False, True) == "relaunch")
check("already kiosk and asked for full screen: leave the window alone", W.plan(True, True, False, False) == "none")
check("leave kiosk: relaunch without --kiosk", W.plan(False, True, False, False) == "relaunch")
check("leave the Fullscreen API: exit in place", W.plan(False, False, True, False) == "exit")
check("already windowed and asked to leave: nothing to close", W.plan(False, False, False, False) == "none")

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    app = td / "app"
    (app / "src" / "mac").mkdir(parents=True)
    (app / "src" / "win").mkdir(parents=True)
    (app / "src" / "mac" / "atlas-launch.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    (app / "src" / "win" / "ATLAS.ps1").write_text("# launcher\n", encoding="utf-8")
    bridge = app / "bridge"
    bridge.mkdir()
    log = td / "launch.log"
    os.environ["ATLAS_LAUNCH_LOG"] = str(log)
    os.environ["ATLAS_PLATFORM"] = "mac"
    try:
        ok, note = W.relaunch(bridge)
        text = log.read_text(encoding="utf-8") if log.exists() else ""
        check("mac relaunch is recorded, not executed", ok and note == "recorded" and "ATLAS_RELAUNCH=1" in text and "atlas-launch.sh" in text and "launch" in text, text)
        os.environ["ATLAS_PLATFORM"] = "windows"
        log.write_text("", encoding="utf-8")
        ok, note = W.relaunch(bridge)
        text = log.read_text(encoding="utf-8")
        check("windows relaunch records ATLAS.ps1 -Mode Launch", ok and "ATLAS.ps1" in text and "-Mode Launch" in text and "ATLAS_RELAUNCH=1" in text, text)
        os.environ["ATLAS_PLATFORM"] = "linux"
        ok, note = W.relaunch(bridge)
        check("linux does not relaunch a browser", ok is False and "Windows and Mac" in note, note)
    finally:
        os.environ.pop("ATLAS_LAUNCH_LOG", None)
        os.environ.pop("ATLAS_PLATFORM", None)

    home = td / "home"
    home.mkdir()
    script = str(ROOT / "src" / "mac" / "atlas-launch.sh")

    def dry(**extra):
        envr = os.environ.copy()
        envr["ATLAS_HOME"] = str(home)
        envr["ATLAS_LAUNCH_DRYRUN"] = "1"
        envr.pop("ATLAS_FULLSCREEN", None)
        envr.pop("ATLAS_RELAUNCH", None)
        envr.update(extra)
        r = subprocess.run(["bash", script], cwd=str(ROOT), env=envr, capture_output=True, text=True, timeout=20)
        out = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
        return r.returncode, out, r.stderr

    code, out, err = dry()
    check("mac launcher: full screen is the default (--kiosk and ?kiosk=1)", code == 0 and out.get("fullscreen") == "1" and out.get("kiosk") == "--kiosk" and out.get("url", "").endswith("/?kiosk=1"), (out, err))
    (home / "config.json").write_text('{"prefs":{"fullscreen": false}}\n', encoding="utf-8")
    code, out, err = dry()
    check("mac launcher: a saved off choice opens a normal app window", code == 0 and out.get("fullscreen") == "0" and out.get("kiosk") == "" and "kiosk=1" not in out.get("url", ""), (out, err))
    code, out, err = dry(ATLAS_FULLSCREEN="1")
    check("mac launcher: ATLAS_FULLSCREEN=1 overrides the saved choice", code == 0 and out.get("fullscreen") == "1" and out.get("kiosk") == "--kiosk", (out, err))

print("FAILED: %s" % fails if fails else "ALL PASSED")
sys.exit(1 if fails else 0)
