"""Computer control: ask ATLAS to open an app or a game - "open Steam", "launch Hades", "play Hollow Knight on Steam".

What it does
  * Looks through what is installed on THIS computer: Steam games (Steam's own library files), Epic Games (the Epic launcher's
    manifests), Windows Start Menu programs, or Mac applications.  Nothing is uploaded; the list stays in memory.
  * Matches the name you said against that list (forgiving of small slips).  If two things fit it asks "Which one?"; if it is
    only guessing it asks "Did you mean ...?"; and by default it asks "Open X? Say yes or no." before every launch
    (Settings -> Add-on modules -> "Ask before opening apps and games", or say "turn off launch confirmation").
  * Only things from that scan can be opened - there is no way to run a command, a script, a file path or a web address through
    this module.  It cannot close, move, delete or change anything.

HTTP (under /api/m/computer_control/):  GET status (counts and names found) | POST rescan.  There is deliberately no launch endpoint.
The matching, scanning and launching code is in bridge/atlas_launch.py (tests: bridge/dev/test_launch.py).
"""
import asyncio

import atlas_launch

META = {
    "title": "Computer control",
    "description": "Say \"open Steam\" or \"launch Hades\" and ATLAS opens it. It only opens apps and games it found installed on this computer "
                   "(Steam, Epic, Start Menu / Applications), asks before opening by default, and cannot run commands, close, move, or delete anything.",
    "consent": "Let ATLAS open apps and games for you?\n\nIt scans Steam, Epic Games, and your Start Menu (Windows) or Applications folder (Mac) "
               "to know what is installed, and opens the one you ask for. It can only open things it found in that scan - it cannot run "
               "commands or touch your files - and it asks \"Open X?\" before each launch unless you turn that off.",
    "requirements": [],
    "ui": False,
}

_S = {}


def _confirm_on(ctx):
    return bool((ctx.config.get("prefs") or {}).get("launch_confirm", True))


async def start(ctx):
    cat = atlas_launch.Catalog()
    _S["cat"] = cat
    _S["launcher"] = atlas_launch.Launcher(cat, confirm_on=lambda: _confirm_on(ctx))
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, cat.get, True)           # first scan in the background


async def stop(ctx):
    _S.clear()


def reset(ctx):
    if _S.get("launcher"):
        _S["launcher"].pending = None


def has_pending(ctx):
    return bool(_S.get("launcher") and _S["launcher"].pending)


async def intent(ctx, text):
    """Called by the voice/chat router for every request nothing else claimed.  Returns {reply, ...} or None."""
    lau = _S.get("launcher")
    if not lau:
        return None
    if lau.parse(text) or lau.pending:
        await asyncio.get_running_loop().run_in_executor(None, lau.cat.get)       # refresh a stale list off the event loop
    return await lau.handle(text)


async def handle(ctx, request):
    from aiohttp import web
    cat = _S.get("cat")
    if not cat:
        return web.json_response({"ok": False, "error": "not running"}, status=503)
    if request.method == "POST" and request.path.endswith("/rescan"):
        await asyncio.get_running_loop().run_in_executor(None, cat.get, True)
    es = cat.entries
    return web.json_response({"ok": True, "counts": cat.counts(), "scanned": cat.scanned, "confirm": _confirm_on(ctx),
                              "games": sorted(e["name"] for e in es if e["kind"] in ("steam", "epic"))[:200],
                              "apps": sorted(e["name"] for e in es if e["kind"] in ("shortcut", "app"))[:200]})
