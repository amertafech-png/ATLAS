"""Single place for MODI's version and its GitHub release repository.
Bump VERSION for every release (tools/build.py reads it too).  Change REPO if the project moves.
Users/tests can override the repo with config `update.repo` (see bridge.py DEFAULTS)."""
VERSION = "1.2.0"
REPO = "amertafech-png/modi"                 # <owner>/<repo> on GitHub that publishes MODI releases
API_BASE = "https://api.github.com"
ASSETS = {"windows": "MODI-Setup.bat", "mac": "MODI-Setup.command"}   # installer asset per platform
CHECKSUMS_ASSET = "SHA256SUMS"
