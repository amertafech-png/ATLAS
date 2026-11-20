#!/usr/bin/env bash
# ATLAS's app windows (HUD + installer) must use a PRIVATE browser profile and must not let Edge sign the user in / show its
# "we are now syncing your browsing data" popup. Checks the Windows launcher, the Windows installer and the macOS scripts.
set -euo pipefail
cd "$(dirname "$0")/.."
bad=0
fn() { awk -v n="function $2" 'index($0,n)==1{p=1} p{print} p&&/^}/{exit}' "$1"; }
for f in src/win/ATLAS.ps1 src/win/installer-body.ps1; do
  args=$(fn "$f" 'Get-AppWindowArgs(')
  [ -n "$args" ] || { echo "$f: no Get-AppWindowArgs"; bad=1; continue; }
  for sw in -- '--user-data-dir=' '--no-first-run' '--no-default-browser-check' '--disable-sync' '--disable-background-mode' 'msImplicitSignin'; do
    [ "$sw" = "--" ] && continue
    grep -q -- "$sw" <<<"$args" || { echo "$f: Get-AppWindowArgs lacks $sw"; bad=1; }
  done
  [ "$(grep -o -- '--disable-features=' <<<"$args" | wc -l)" -le 1 ] || { echo "$f: more than one --disable-features (Chromium keeps only the last)"; bad=1; }
  order=$(fn "$f" 'Find-Browser {' | grep -o "@('chrome', 'msedge', 'brave')" || true)
  [ -n "$order" ] || { echo "$f: Find-Browser must try Chrome before Edge"; bad=1; }
  # every browser start goes through Get-AppWindowArgs (no hand-rolled argument lists without the sign-in switches)
  if grep -nE 'Start-Process[^#]*-FilePath[^#]*\$(b|browser|e)(\.Path)?\b' "$f" | grep -v 'Get-AppWindowArgs'; then echo "$f: browser started without Get-AppWindowArgs"; bad=1; fi
done
for n in 'Get-DefaultBrowserKind {' 'Find-BrowserExe(' 'Find-Browser {' 'Get-AppWindowArgs('; do
  [ "$(fn src/win/ATLAS.ps1 "$n" | grep -v '^#')" = "$(fn src/win/installer-body.ps1 "$n" | grep -v '^#')" ] || { echo "function $n differs between ATLAS.ps1 and installer-body.ps1"; bad=1; }
done
grep -q "Join-Path \$Data 'browser-profile'" src/win/ATLAS.ps1 || { echo "HUD must use the 1.4.2 browser-profile folder"; bad=1; }
# macOS: every app window has a private profile, no sync, and the Edge implicit sign-in switch
for f in src/mac/atlas-launch.sh src/mac/installer-head.sh; do
  grep -q -- '--app=' "$f" || continue
  for sw in '--user-data-dir=' '--disable-sync' 'msImplicitSignin'; do grep -q -- "$sw" "$f" || { echo "$f lacks $sw"; bad=1; }; done
done
[ $bad = 0 ] && echo "ok: app windows use a private profile with sign-in/sync switched off (Chrome first, Edge implicit sign-in off)" || exit 1
