#!/usr/bin/env bash
# Windows installer: in window (GUI) mode no console may stay open behind the installer window (1.4.2).
# The visible copy starts a hidden copy (conhost + powershell -WindowStyle Hidden), waits for 'ui-up', then exits; 'ui-failed' or a
# crash falls back to the visible console. Silent modes (/update, -Yes, -ExtractTo) must never be relaunched.
set -euo pipefail
cd "$(dirname "$0")/.."
f=src/win/installer-body.ps1; bad=0
need() { grep -qF -- "$1" "$f" || { echo "$f: missing: $1"; bad=1; }; }
need "elseif ((-not \$Yes) -and (-not \$ExtractTo) -and (\$env:OS -eq 'Windows_NT')"
need "-WindowStyle Hidden -File"
need "System32\\conhost.exe"
need "Set-ChildState 'ui-up'"
need "Set-ChildState 'ui-failed'"
need "if (\$state -eq 'ui-up') { \$relaunched = \$true; break }"
need "if (\$relaunched) { exit 0 }"
need "Start-Transcript -LiteralPath \$SetupLog"
need "\$env:ATLAS_SETUP_NOHIDE = '1'"
# the relaunch block must run before the installer window / payload work starts
r=$(grep -n "ATLAS_SETUP_CHILD = '1'" "$f" | head -1 | cut -d: -f1); u=$(grep -n "^if (\$gui) {" "$f" | head -1 | cut -d: -f1)
[ -n "$r" ] && [ -n "$u" ] && [ "$r" -lt "$u" ] || { echo "relaunch must come before the GUI starts"; bad=1; }
[ $bad = 0 ] && echo "ok: the installer hides its console in window mode (visible fallback, silent modes untouched)" || exit 1
