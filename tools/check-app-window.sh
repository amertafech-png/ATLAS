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
# Full screen is opt-in --kiosk on the HUD window only. The installer window stays a small app window.
# --kiosk is added beside --app=, never instead of it, so web links are not opened in that window.
grep -F -q 'Get-AppWindowArgs $browser.Kind $openUrl $appdata' src/win/ATLAS.ps1 || { echo "HUD launch must pass the full-screen choice into Get-AppWindowArgs"; bad=1; }
grep -q 'kiosk=1' src/win/ATLAS.ps1 || { echo "Windows HUD must mark a kiosk window with ?kiosk=1"; bad=1; }
if grep -n 'Get-AppWindowArgs' src/win/installer-body.ps1 | grep -q '\$true'; then echo "installer window must not request kiosk"; bad=1; fi
grep -q 'kiosk=1' src/mac/atlas-launch.sh || { echo "mac launcher must mark a kiosk window"; bad=1; }
grep -q -- '--kiosk' src/mac/atlas-launch.sh || { echo "mac launcher must be able to pass --kiosk"; bad=1; }
grep -q 'open "$URL"' src/mac/atlas-launch.sh || { echo "mac fallback must stay a plain open of the HUD url"; bad=1; }
if grep -q -- '--kiosk' src/mac/installer-head.sh; then echo "mac installer must stay windowed"; bad=1; fi
# macOS: every app window has a private profile, no sync, and the Edge implicit sign-in switch
for f in src/mac/atlas-launch.sh src/mac/installer-head.sh; do
  grep -q -- '--app=' "$f" || continue
  for sw in '--user-data-dir=' '--disable-sync' 'msImplicitSignin'; do grep -q -- "$sw" "$f" || { echo "$f lacks $sw"; bad=1; }; done
done
[ $bad = 0 ] && echo "ok: app windows use a private profile with sign-in/sync switched off (Chrome first, Edge implicit sign-in off); HUD full screen is --kiosk, installer stays windowed" || exit 1
