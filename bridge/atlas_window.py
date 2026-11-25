"""Full-screen HUD window.

The Windows and Mac launchers start the ATLAS window with Chromium's --kiosk switch when
prefs.fullscreen is on (the default). That is a real full screen: no title bar, and on Windows
the taskbar hides. The window still uses ATLAS's private browser profile. Web links are not
opened inside it; the HUD posts them to /api/open-url and the operating system opens the
default browser.

F11, Esc, the Settings switch and the phrases "full screen" / "exit full screen" call
/api/window/fullscreen. plan() says what the page should do:

  * enter    - the page calls the Fullscreen API (a click or key just happened)
  * exit     - the page leaves the Fullscreen API (Esc does this; it does not need a new click)
  * relaunch - the window was started with --kiosk, which Chromium will not leave in place,
               so the launcher is started again (ATLAS_RELAUNCH=1) and opens the other way
  * none     - the screen already matches the choice; only the saved setting changed

ATLAS_LAUNCH_LOG=<file> records the relaunch command and does not start it (tests).
ATLAS_NO_BOOT=1 (the dev tests) also does not start it.
"""
import os
import subprocess
import sys
from pathlib import Path


def plan(want_on, launched_kiosk, api_active, api_failed=False):
    """want_on: the choice just saved. launched_kiosk: this window's URL has ?kiosk=1.
    api_active: document.fullscreenElement was set. api_failed: requestFullscreen() refused."""
    if want_on:
        if launched_kiosk or api_active:
            return "none"
        if api_failed:
            return "relaunch"
        return "enter"
    if launched_kiosk:
        return "relaunch"
    if api_active:
        return "exit"
    return "none"


def _platform():
    forced = os.environ.get("ATLAS_PLATFORM")
    if forced in ("windows", "mac", "linux"):
        return forced
    if os.name == "nt" or sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def find_launcher(bridge_dir):
    """Installed layout is app/win and app/mac. A git checkout is bridge/../src/win and src/mac."""
    app = Path(bridge_dir).parent
    plat = _platform()
    if plat == "windows":
        names = ("win/ATLAS.ps1", "src/win/ATLAS.ps1")
    elif plat == "mac":
        names = ("mac/atlas-launch.sh", "src/mac/atlas-launch.sh")
    else:
        return None
    for rel in names:
        p = app / rel
        if p.is_file():
            return p
    return None


def relaunch_command(bridge_dir):
    """The exact command the bridge starts, or None when this computer has no launcher."""
    path = find_launcher(bridge_dir)
    if path is None:
        return None
    if _platform() == "windows":
        ps = os.environ.get("ATLAS_POWERSHELL") or "powershell.exe"
        return [ps, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-File", str(path), "-Mode", "Launch"]
    if _platform() == "mac":
        return ["/bin/bash", str(path), "launch"]
    return None


def relaunch(bridge_dir):
    """Returns (ok, note). Does not start a browser when a test asked us not to."""
    cmd = relaunch_command(bridge_dir)
    if not cmd:
        return False, "Full screen can be changed here, but opening the window again is only set up on Windows and Mac."
    line = "ATLAS_RELAUNCH=1 " + " ".join(cmd)
    log = os.environ.get("ATLAS_LAUNCH_LOG")
    if log or os.environ.get("ATLAS_NO_BOOT"):
        if log:
            with open(log, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return True, "recorded" if log else "skipped"
    env = os.environ.copy()
    env["ATLAS_RELAUNCH"] = "1"
    subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return True, "relaunching"
