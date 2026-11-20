"""Single place for ATLAS's version and its GitHub release repository.
Bump VERSION for every release (tools/build.py reads it too).  Change REPO if the project moves.
Users/tests can override the repo with config `update.repo` (see bridge.py DEFAULTS)."""
VERSION = "1.3.0"
REPO = "amertafech-png/atlas"                 # <owner>/<repo> on GitHub that publishes ATLAS releases
API_BASE = "https://api.github.com"
ASSETS = {"windows": "ATLAS-Setup.bat", "mac": "ATLAS-Setup.command"}   # installer asset per platform
CHECKSUMS_ASSET = "SHA256SUMS"
# Releases ALSO carry copies named like this so installs older than the rename (<= 1.1.x, which look for MODI-Setup.* on this repo) can still self-update.   # legacy-modi
LEGACY_ASSETS = {"windows": "MODI-Setup.bat", "mac": "MODI-Setup.command"}   # legacy-modi
