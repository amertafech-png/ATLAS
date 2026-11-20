#!/usr/bin/env bash
# Links ATLAS opens must go to the user's DEFAULT browser. Edge/Chrome may only be started for ATLAS's own local app windows
# (--app=...), never for web links; Windows web links use Start-Process <url> via Open-DefaultBrowser, macOS uses `open <url>`.
# Also: the Windows AppUserModelID on the app window (ATLAS.ps1) must match the one on the shortcuts (installer-body.ps1).
set -euo pipefail
cd "$(dirname "$0")/.."
bad=0
# 1. a browser executable started without --app (would open a link in Edge/Chrome instead of the default browser)
while IFS= read -r line; do
  case "$line" in *--app=*|*'$appArgs'*|*Get-AppWindowArgs*) ;; *) echo "browser exe started for something other than an app window: $line"; bad=1 ;; esac
done < <(grep -nE 'Start-Process[^#]*-FilePath[^#]*\$(e|browser)\b' src/win/*.ps1 || true)
# 2. no literal web links handed to Start-Process directly (use Open-DefaultBrowser), no msedge: / microsoft-edge: link schemes
if grep -nE "Start-Process +['\"]https?://" src/win/*.ps1; then echo "use Open-DefaultBrowser for web links"; bad=1; fi
if grep -rnE "microsoft-edge:|msedge\.exe[^)]*https?://" src bridge/*.py index.html; then echo "links must not be forced into Edge"; bad=1; fi
# 3. macOS: `open -a <browser> <web link>` forces a browser; only plain `open <url>` (default browser) for links
if grep -nE 'open -a [^#]*https?://' src/mac/*.sh; then echo "macOS links must use plain open <url>"; bad=1; fi
# 4. the HUD routes outside links through the bridge's default-browser opener
grep -q "/api/open-url" index.html || { echo "index.html does not route links to /api/open-url"; bad=1; }
grep -q '"/api/open-url"' bridge/bridge.py || { echo "bridge has no /api/open-url"; bad=1; }
# 5. taskbar identity: same AppUserModelID in launcher and installer
a=$(grep -oE "^\\\$AtlasAppId = '[^']+'" src/win/ATLAS.ps1 || true); b=$(grep -oE "^\\\$AtlasAppId = '[^']+'" src/win/installer-body.ps1 || true)
if [ -z "$a" ] || [ "$a" != "$b" ]; then echo "AppUserModelID mismatch: launcher [$a] installer [$b]"; bad=1; fi
[ $bad = 0 ] && echo "ok: links open in the default browser; taskbar AppUserModelID matches" || exit 1
