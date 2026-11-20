#!/bin/bash
# ATLAS launcher (macOS). Called by ATLAS.app. Starts the bridge if needed, waits for it,
# then opens the standalone app window (Chrome/Edge/Brave/... in --app mode).
#   atlas-launch.sh [launch|bridge|stop]
MODE="${1:-launch}"
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$(dirname "$HERE")"                                   # .../ATLAS/app
case "$(uname -s)" in
  Darwin) DEFAULT_DATA="$HOME/Library/Application Support/ATLAS" ;;
  *)      DEFAULT_DATA="${XDG_DATA_HOME:-$HOME/.local/share}/ATLAS" ;;
esac
DATA="${ATLAS_HOME:-$DEFAULT_DATA}"
LOGS="$DATA/logs"; mkdir -p "$LOGS"
PY="${ATLAS_PY:-$APP/.venv/bin/python}"

PORT=8765
for f in "$APP/bridge/config.json" "$DATA/config.json"; do
  [ -f "$f" ] && p=$(grep -o '"port"[[:space:]]*:[[:space:]]*[0-9]*' "$f" | head -1 | grep -o '[0-9]*$') && [ -n "$p" ] && PORT="$p"
done
[ -n "${JARVIS_PORT:-}" ] && PORT="$JARVIS_PORT"
URL="http://127.0.0.1:$PORT"

healthy() { curl -fsS --max-time 2 "$URL/api/health" >/dev/null 2>&1; }
alert() {
  if [ "$(uname -s)" = Darwin ]; then
    osascript -e "display dialog \"$1\" with title \"ATLAS\" buttons {\"OK\"} default button 1 with icon caution" >/dev/null 2>&1
  else echo "ATLAS: $1" >&2; fi
}

if [ "$MODE" = stop ]; then
  curl -fsS --max-time 3 -X POST "$URL/api/quit" >/dev/null 2>&1
  exit 0
fi

if ! healthy; then
  [ -x "$PY" ] || { alert "ATLAS is not installed correctly (Python environment missing). Run ATLAS-Setup.command again."; exit 1; }
  export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
  nohup "$PY" "$APP/bridge/bridge.py" >>"$LOGS/bridge.log" 2>&1 &
  disown 2>/dev/null
  i=0; while [ $i -lt 240 ]; do healthy && break; sleep 0.5; i=$((i+1)); done
  healthy || { alert "ATLAS's bridge did not start. See $LOGS/bridge.log"; exit 1; }
fi
[ "$MODE" = bridge ] && exit 0

if [ "$(uname -s)" = Darwin ]; then
  for name in "Google Chrome" "Microsoft Edge" "Brave Browser" "Chromium" "Vivaldi" "Opera"; do
    if [ -d "/Applications/$name.app" ] || [ -d "$HOME/Applications/$name.app" ]; then
      mkdir -p "$DATA/appdata"
      # private ATLAS profile; no sign-in / sync prompts. Edge: also no implicit Microsoft-account sign-in (its "syncing" popup)
      extra=""; [ "$name" = "Microsoft Edge" ] && extra="--disable-features=msImplicitSignin,msEdgeImplicitSignin,msIdentityImplicitSignin,msEdgeSyncConsent"
      exec open -n -a "$name" --args "--app=$URL" "--user-data-dir=$DATA/appdata" "--window-size=1600,900" \
           --no-first-run --no-default-browser-check --disable-sync --disable-signin-promo --disable-background-mode \
           --autoplay-policy=no-user-gesture-required $extra
    fi
  done
  exec open "$URL"          # no Chromium-based browser: default browser tab (Safari: File > Add to Dock for an app-like window)
else
  exec xdg-open "$URL"
fi
