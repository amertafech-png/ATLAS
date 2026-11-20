"""Open web links in the user's DEFAULT browser (not the browser the ATLAS app window happens to run in).

The HUD and the installer window are Edge/Chrome "app mode" windows, so a plain <a target=_blank> inside them opens in that browser
(often Edge) instead of the browser the user chose.  The HUD sends such links here instead and the bridge hands them to the OS:
  Windows  os.startfile(url)  -> ShellExecute "open" -> the registered default https handler
  macOS    open <url>         -> the default browser
  Linux    xdg-open <url>
Only plain http(s) addresses are accepted (no file:, javascript:, custom schemes, spaces or control characters).
ATLAS_OPEN_URL_LOG=<file> (tests) records the address instead of opening anything."""
import os
import re
import subprocess
import sys
from urllib.parse import urlsplit

_OK = re.compile(r"https?://[^\s\x00-\x1f\x7f\"<>\\^`{|}]{1,2000}", re.I)


def valid(url):
    if not isinstance(url, str) or len(url) > 2048 or not _OK.fullmatch(url):
        return False
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    return u.scheme.lower() in ("http", "https") and bool(u.hostname)


def open_default(url):
    """Returns (ok, message)."""
    if not valid(url):
        return False, "That isn't a web address I can open."
    log = os.environ.get("ATLAS_OPEN_URL_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(url + "\n")
        return True, "recorded"
    try:
        if os.name == "nt":
            os.startfile(url)  # noqa: S606 - validated http(s) URL; ShellExecute uses the user's default browser
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, "opened"
    except Exception as e:  # noqa
        return False, "Couldn't open your browser (%s)." % type(e).__name__
