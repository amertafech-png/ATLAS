#!/bin/bash
# =====================================================================================
#  ATLAS-Setup.command  -  one-file installer for ATLAS on macOS (Intel + Apple Silicon)
#
#  Double-click in Finder.  First time only, macOS may say it can't verify the file
#  (it is not signed/notarized):  right-click > Open > Open,   or in Terminal run
#      xattr -d com.apple.quarantine ~/Downloads/ATLAS-Setup.command
#
#  What it does (asks before each big step):
#    1. checks Python 3.11+ (offers Homebrew or python.org if missing)
#    2. finds Hermes Agent, or installs it with its official installer if you say yes
#    3. installs ATLAS (bridge + HUD + voice models) and a standalone ATLAS.app in ~/Applications
#    4. connects ATLAS to Hermes (private key, local API, start) - no files to edit, nothing printed
#    5. optional: start ATLAS's bridge at login (LaunchAgent)
#  Signing in to your AI (ChatGPT / Claude / Grok / a local model) happens in ATLAS's first-run screen.
#  Options:  --uninstall   --yes (accept defaults, no prompts)   --extract-to DIR (just unpack)
#            --update (non-interactive in-place update: keeps your data/Hermes setup, restarts ATLAS)
# =====================================================================================
set -u
YES=0; UNINSTALL=0; UPDATE=0; EXTRACT_TO=""
while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) YES=1;;
    --uninstall) UNINSTALL=1;;
    --update) UPDATE=1; YES=1;;      # non-interactive in-place update (used by ATLAS's self-update)
    --extract-to) EXTRACT_TO="${2:-}"; shift;;
    -h|--help) sed -n '2,26p' "$0"; exit 0;;
  esac; shift
done

SELF="$0"
case "$SELF" in /*) ;; *) SELF="$(pwd)/$SELF";; esac

extract_payload() {   # $1 = destination dir
  mkdir -p "$1" || return 1
  local dec=""
  if echo QQ== | base64 --decode >/dev/null 2>&1; then dec="base64 --decode"
  elif echo QQ== | base64 -D >/dev/null 2>&1; then dec="base64 -D"
  elif echo QQ== | base64 -d >/dev/null 2>&1; then dec="base64 -d"
  else dec="openssl base64 -d -A"; fi
  awk 'f{print} /^__ATLAS_PAYLOAD__$/{f=1}' "$SELF" | $dec | tar -xzf - -C "$1"
}
if [ -n "$EXTRACT_TO" ]; then extract_payload "$EXTRACT_TO" && echo "Extracted to $EXTRACT_TO"; exit $?; fi

# ---------- helpers ----------
if [ -t 1 ]; then B=$'\033[1m'; C=$'\033[1;36m'; G=$'\033[1;32m'; Y=$'\033[1;33m'; R=$'\033[1;31m'; N=$'\033[0m'; else B=""; C=""; G=""; Y=""; R=""; N=""; fi
step() { printf '\n%s==> %s%s\n' "$C" "$*" "$N"; }
ok()   { printf '  %s[ok]%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s[!]%s  %s\n' "$Y" "$N" "$*"; }
err()  { printf '  %s[x]%s  %s\n' "$R" "$N" "$*"; }
ask() {   # ask "question" default(y|n)  -> 0 = yes
  local q="$1" def="${2:-y}" a hint="[Y/n]"
  [ "$def" = n ] && hint="[y/N]"
  if [ "$YES" = 1 ]; then [ "$def" = y ]; return; fi
  printf "%s" "  $q $hint "; read -r a 2>/dev/null </dev/tty || a=""
  a="$(printf '%s' "$a" | tr 'A-Z' 'a-z')"
  [ -z "$a" ] && a="$def"
  [ "$a" = y ] || [ "$a" = yes ]
}
pause_exit() { local code="${1:-0}"
  if [ "$UPDATE" = 1 ] && [ "$code" != 0 ] && [ "$IS_MAC" = 1 ]; then
    osascript -e 'display dialog "The ATLAS update did not finish. Open ATLAS again, or re-run ATLAS-Setup.command. Log: ~/Library/Application Support/ATLAS/logs/update.log" with title "ATLAS" buttons {"OK"} default button 1 with icon caution' >/dev/null 2>&1
  fi
  if [ "$YES" != 1 ] && [ -t 0 ]; then echo; printf "%s" "Press Return to close this window..."; read -r _ 2>/dev/null </dev/tty; fi; exit "$code"; }

IS_MAC=0; [ "$(uname -s)" = Darwin ] && IS_MAC=1
if [ "$IS_MAC" = 0 ] && [ "${ATLAS_ALLOW_LINUX:-}" != 1 ]; then
  err "ATLAS-Setup.command is for macOS. (Set ATLAS_ALLOW_LINUX=1 only for testing.)"; exit 1
fi
if [ "$IS_MAC" = 1 ]; then DATA="${ATLAS_HOME:-$HOME/Library/Application Support/ATLAS}"; else DATA="${ATLAS_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/ATLAS}"; fi
APPDIR="$DATA/app"
APP_BUNDLE_DIR="${ATLAS_APPS_DIR:-$HOME/Applications}"
BUNDLE="$APP_BUNDLE_DIR/ATLAS.app"
PLIST_LABEL="com.atlas.bridge"
PLIST="${ATLAS_LAUNCHAGENTS_DIR:-$HOME/Library/LaunchAgents}/$PLIST_LABEL.plist"
HERMES_HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# Gatekeeper: if this file still carries the quarantine flag, clear it (we are running, so the user allowed it)
if [ "$IS_MAC" = 1 ] && xattr -p com.apple.quarantine "$SELF" >/dev/null 2>&1; then
  xattr -d com.apple.quarantine "$SELF" >/dev/null 2>&1 || true
fi

get_port() {
  local p=8765 f x
  for f in "$APPDIR/bridge/config.json" "$DATA/config.json"; do
    [ -f "$f" ] && x=$(grep -o '"port"[[:space:]]*:[[:space:]]*[0-9]*' "$f" | head -1 | grep -o '[0-9]*$') && [ -n "$x" ] && p="$x"
  done
  [ -n "${JARVIS_PORT:-}" ] && p="$JARVIS_PORT"
  echo "$p"
}

# ---------- upgrade from MODI (v1.1.x and older): the product is now called ATLAS ----------   # legacy-modi
# Where the old version kept things (legacy-modi)
OLD_DATA="${MODI_HOME:-}"                                                                                       # legacy-modi
if [ -z "$OLD_DATA" ]; then if [ "$IS_MAC" = 1 ]; then OLD_DATA="$HOME/Library/Application Support/MODI"; else OLD_DATA="${XDG_DATA_HOME:-$HOME/.local/share}/MODI"; fi; fi   # legacy-modi
OLD_BUNDLE="${MODI_APPS_DIR:-$HOME/Applications}/MODI.app"                                                      # legacy-modi
OLD_LABEL="com.modi.bridge"                                                                                     # legacy-modi
OLD_PLIST="${MODI_LAUNCHAGENTS_DIR:-$HOME/Library/LaunchAgents}/$OLD_LABEL.plist"                               # legacy-modi
MIGRATED_LOGIN=0
migrate_from_old_name() {
  [ "$OLD_DATA" != "$DATA" ] || return 0
  [ -d "$OLD_DATA" ] || [ -d "$OLD_BUNDLE" ] || [ -f "$OLD_PLIST" ] || return 0
  step "Upgrading from MODI: ATLAS keeps your settings, notes and voices"   # legacy-modi
  # 1. stop the old bridge (same port as before) and anything still running from the old folder
  local oport=8765 x f
  f="$OLD_DATA/config.json"
  [ -f "$f" ] && x=$(grep -o '"port"[[:space:]]*:[[:space:]]*[0-9]*' "$f" | head -1 | grep -o '[0-9]*$') && [ -n "$x" ] && oport="$x"
  [ -n "${JARVIS_PORT:-}" ] && oport="$JARVIS_PORT"
  if curl -fsS --max-time 2 "http://127.0.0.1:$oport/api/health" >/dev/null 2>&1; then
    curl -fsS --max-time 3 -X POST "http://127.0.0.1:$oport/api/quit" >/dev/null 2>&1
    for i in $(seq 1 40); do curl -fsS --max-time 1 "http://127.0.0.1:$oport/api/health" >/dev/null 2>&1 || break; sleep 0.5; done
  fi
  pkill -f "$OLD_DATA/app/" >/dev/null 2>&1 || true
  # 2. old login item
  if [ -f "$OLD_PLIST" ]; then
    [ "$IS_MAC" = 1 ] && launchctl bootout "gui/$(id -u)/$OLD_LABEL" >/dev/null 2>&1
    rm -f "$OLD_PLIST"; MIGRATED_LOGIN=1; ok "old login item removed (ATLAS will set up its own)"
  fi
  # 3. old app
  if [ -d "$OLD_BUNDLE" ]; then rm -rf "$OLD_BUNDLE"; ok "old app removed ($OLD_BUNDLE)"; fi
  # 4. data: config, notes, voices, speech models, window profile ... move the whole folder
  if [ -d "$OLD_DATA" ]; then
    if [ ! -e "$DATA" ] || [ -z "$(ls -A "$DATA" 2>/dev/null)" ]; then
      rmdir "$DATA" 2>/dev/null; mkdir -p "$(dirname "$DATA")"
      if mv "$OLD_DATA" "$DATA"; then ok "your data moved to $DATA"; else err "could not move $OLD_DATA - your old data is untouched"; return 0; fi
    else
      cp -Rn "$OLD_DATA/." "$DATA/" 2>/dev/null; rm -rf "$OLD_DATA/app"
      warn "ATLAS data already existed; copied what was missing. The old folder $OLD_DATA can be deleted when you are happy."
    fi
    # the Python environment moves fine; if it does not start, it is simply rebuilt below
    if [ -x "$APPDIR/.venv/bin/python" ] && ! "$APPDIR/.venv/bin/python" -c "import aiohttp, psutil" >/dev/null 2>&1; then rm -rf "$APPDIR/.venv"; fi
  fi
}

# ---------- uninstall ----------
do_uninstall() {
  step "Uninstalling ATLAS"
  local port; port=$(get_port)
  curl -fsS --max-time 3 -X POST "http://127.0.0.1:$port/api/quit" >/dev/null 2>&1 && ok "bridge stopped"
  if [ -f "$PLIST" ]; then
    [ "$IS_MAC" = 1 ] && launchctl bootout "gui/$(id -u)/$PLIST_LABEL" >/dev/null 2>&1
    rm -f "$PLIST"; ok "login item removed"
  fi
  [ -d "$BUNDLE" ] && rm -rf "$BUNDLE" && ok "removed $BUNDLE"
  [ -d "$APPDIR" ] && rm -rf "$APPDIR" && ok "removed $APPDIR"
  if [ -d "$DATA" ] && ask "Also delete ATLAS's saved data (notes, voice models, speech models, window profile, logs)?" n; then
    rm -rf "$DATA"; ok "removed $DATA"
  fi
  echo
  echo "  Hermes Agent itself was NOT touched. The API key ATLAS added (API_SERVER_KEY in $HERMES_HOME_DIR/.env) stays, so other tools keep working."
  if command -v hermes >/dev/null 2>&1 && ask "Turn Hermes' local API server off again (hermes config set API_SERVER_ENABLED false + restart gateway)?" n; then
    hermes config set API_SERVER_ENABLED false >/dev/null 2>&1 && hermes gateway restart --detach --yes >/dev/null 2>&1; ok "Hermes API server disabled"
  fi
  ok "ATLAS uninstalled."
  pause_exit 0
}

clear_screen_banner() {
  printf '%s\n' "$C"'    _  _____ _        _    ____'"$N"
  printf '%s\n' "$C"'   / \|_   _| |      / \  / ___|'"$N"'   Personal command interface for Hermes Agent'
  printf '%s\n' "$C"'  / _ \ | | | |     / _ \ \___ \'"$N"'   macOS installer'
  printf '%s\n' "$C"' / ___ \| | | |___ / ___ \ ___) |'"$N"
  printf '%s\n' "$C"'/_/   \_\_| |_____/_/   \_\____/'"$N"
}
clear_screen_banner

if [ "$UNINSTALL" = 1 ]; then do_uninstall; fi
if [ "$YES" != 1 ] && [ -z "${ATLAS_NO_MENU:-}" ]; then
  echo; echo "  1) Install / update ATLAS"; echo "  2) Uninstall ATLAS"; echo "  3) Quit"
  printf "%s" "  Choose [1]: "; read -r choice 2>/dev/null </dev/tty || choice=1
  case "${choice:-1}" in 2) do_uninstall;; 3) exit 0;; esac
fi

migrate_from_old_name

# ---------- 1. Python ----------
step "1/5  Checking Python 3.11+"
py_ok() { "$1" -c 'import sys, venv, ssl; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; }
find_python() {
  local c p
  for c in python3.13 python3.12 python3.11 python3; do
    p="$(command -v "$c" 2>/dev/null)" || continue
    # /usr/bin/python3 is Apple's stub: calling it can pop up the Xcode tools installer -> skip it unless the tools exist
    if [ "$p" = /usr/bin/python3 ] && ! xcode-select -p >/dev/null 2>&1; then continue; fi
    py_ok "$p" && { echo "$p"; return 0; }
  done
  for p in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 \
           /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11 \
           /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.11/bin/python3; do
    [ -x "$p" ] && py_ok "$p" && { echo "$p"; return 0; }
  done
  return 1
}
PY="$(find_python)" || PY=""
if [ "$UPDATE" = 1 ] && [ -x "$APPDIR/.venv/bin/python" ]; then
  ok "update mode: using the existing Python environment"
elif [ -z "$PY" ]; then
  warn "No Python 3.11 or newer found."
  if command -v brew >/dev/null 2>&1; then
    if ask "Install Python 3.12 with Homebrew now?" y; then brew install python@3.12; fi
  else
    echo "  Homebrew was not found. Options:"
    echo "    1) Install Homebrew (official script, asks for your Mac password) and then Python 3.12"
    echo "    2) Open python.org so you can download the macOS installer yourself"
    echo "    3) Cancel"
    if [ "$YES" = 1 ]; then pchoice=3; else printf "%s" "  Choose [2]: "; read -r pchoice 2>/dev/null </dev/tty || pchoice=2; fi
    case "${pchoice:-2}" in
      1) /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
           && { for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && eval "$($b shellenv)"; done; brew install python@3.12; };;
      2) open "https://www.python.org/downloads/macos/" 2>/dev/null; echo "  Install Python, then run this installer again."; pause_exit 1;;
      *) pause_exit 1;;
    esac
  fi
  PY="$(find_python)" || PY=""
  [ -n "$PY" ] || { err "Python 3.11+ is still not available. Install it (https://www.python.org/downloads/macos/) and re-run."; pause_exit 1; }
fi
[ -n "$PY" ] && ok "Python: $PY ($("$PY" -c 'import sys;print(".".join(map(str,sys.version_info[:3])))'))"

# ---------- 2. Hermes ----------
# Hermes is only *found* here, never configured: ATLAS connects to it itself (key, API server, start, AI sign-in) in step 4
# and in its first-run screen.  The Hermes installer may put `hermes` in ~/.local/bin, which a shortcut/LaunchAgent does not have on PATH.
find_hermes() {
  local d
  for d in "$HERMES_HOME_DIR/bin" "$HERMES_HOME_DIR/hermes-agent/.hermes/bin" "$HOME/.local/bin" /opt/homebrew/bin /usr/local/bin; do
    [ -d "$d" ] && case ":$PATH:" in *":$d:"*) ;; *) PATH="$PATH:$d";; esac
  done
  export PATH; hash -r 2>/dev/null
  command -v hermes >/dev/null 2>&1
}
step "2/5  Hermes Agent"
find_hermes && HERMES_OK=1 || HERMES_OK=0
if [ "$HERMES_OK" = 0 ] && [ "$UPDATE" != 1 ]; then
  warn "Hermes Agent is not installed."
  if ask "Install it now with the official installer (https://hermes-agent.nousresearch.com/install.sh)?" y; then
    tmpi="$(mktemp "${TMPDIR:-/tmp}/hermes-install.XXXXXX")"
    if curl -fsSL https://hermes-agent.nousresearch.com/install.sh -o "$tmpi"; then bash "$tmpi" --non-interactive || warn "the Hermes installer reported a problem"; else err "download failed"; fi
    rm -f "$tmpi"
    find_hermes && HERMES_OK=1 || HERMES_OK=0
  fi
fi
if [ "$HERMES_OK" = 1 ]; then ok "hermes: $(command -v hermes)"
else warn "Hermes is not installed (yet). That's fine - ATLAS's first screen installs it with one click."; fi

# ---------- 3. Install ATLAS ----------
step "3/5  Installing ATLAS to $APPDIR"
port="$(get_port)"
if curl -fsS --max-time 2 "http://127.0.0.1:$port/api/health" >/dev/null 2>&1; then
  curl -fsS --max-time 2 -X POST "http://127.0.0.1:$port/api/quit" >/dev/null 2>&1
  for i in $(seq 1 40); do curl -fsS --max-time 1 "http://127.0.0.1:$port/api/health" >/dev/null 2>&1 || break; sleep 0.5; done
fi
mkdir -p "$APPDIR" "$DATA/logs"
stage="$(mktemp -d "${TMPDIR:-/tmp}/atlas-payload.XXXXXX")"
extract_payload "$stage" || { err "could not unpack the embedded files (is the installer file complete?)"; pause_exit 1; }
rm -rf "$APPDIR/bridge" "$APPDIR/assets" "$APPDIR/mac" "$APPDIR/win" "$APPDIR/index.html"
cp -R "$stage"/. "$APPDIR"/ && rm -rf "$stage"
chmod +x "$APPDIR/mac/"*.sh
ok "files installed"

if [ ! -x "$APPDIR/.venv/bin/python" ]; then "$PY" -m venv "$APPDIR/.venv" || { err "could not create the Python environment"; pause_exit 1; }; fi
VPY="$APPDIR/.venv/bin/python"
"$VPY" -m pip install --quiet --upgrade pip >/dev/null 2>&1
echo "  installing Python packages (aiohttp, faster-whisper, piper-tts, psutil...) - a few minutes the first time"
PIPER_OK=1
if ! "$VPY" -m pip install --quiet -r "$APPDIR/bridge/requirements.txt"; then
  warn "full install failed - retrying without Piper (ATLAS will speak with the built-in macOS voice)"
  grep -vi '^piper' "$APPDIR/bridge/requirements.txt" > "$DATA/requirements-nopiper.txt"
  "$VPY" -m pip install --quiet -r "$DATA/requirements-nopiper.txt" || { err "pip install failed. See messages above."; pause_exit 1; }
  PIPER_OK=0
fi
"$VPY" -c 'import piper' >/dev/null 2>&1 || PIPER_OK=0
if [ "$PIPER_OK" = 0 ] && [ "$IS_MAC" = 1 ]; then
  "$VPY" - "$DATA/config.json" <<'PYEOF'
import json, os, sys
p = sys.argv[1]
try: c = json.load(open(p))
except Exception: c = {}
c.setdefault("tts", {})["engine"] = "say"
json.dump(c, open(p, "w"), indent=2)
PYEOF
  ok "voice: macOS 'say' (Piper is not available on this Mac)"
elif [ "$PIPER_OK" = 1 ]; then ok "voice: Piper (offline neural voice)"; fi
ok "Python packages installed"
"$VPY" "$APPDIR/bridge/bridge.py" --install-deps || warn "some optional feature packages could not be installed (see Settings in ATLAS)"
if [ "$UPDATE" != 1 ] && [ "$YES" != 1 ] && ! grep -q '"profile"' "$DATA/config.json" 2>/dev/null; then
  echo "  A few personal touches (all optional - press Return to skip; you can change them later in ATLAS > Settings):"
  printf "%s" "  Name your assistant (Return keeps ATLAS; 1-20 characters): "; read -r p_aname 2>/dev/null </dev/tty || p_aname=""
  printf "%s" "  What should your assistant call you? "; read -r p_name 2>/dev/null </dev/tty || p_name=""
  printf "%s" "  Your city, for weather (e.g. Austin, Texas): "; read -r p_city 2>/dev/null </dev/tty || p_city=""
  printf "%s" "  Favorite sports, comma separated (Soccer, NFL, NBA, MLB, NHL, College football, F1, or your own): "; read -r p_sports 2>/dev/null </dev/tty || p_sports=""
  set -- --set-profile
  [ -n "$p_aname" ] && set -- "$@" --assistant "$p_aname"
  [ -n "$p_name" ] && set -- "$@" --name "$p_name"
  [ -n "$p_city" ] && set -- "$@" --city "$p_city"
  OLDIFS="$IFS"; IFS=','
  for sp in $p_sports; do
    sp="$(printf '%s' "$sp" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [ -z "$sp" ] && continue
    sp_lg=""
    case "$(printf '%s' "$sp" | tr 'A-Z' 'a-z')" in
      soccer) printf "%s" "  $sp - leagues (e.g. Premier League, Serie A; Return to skip): "; IFS="$OLDIFS" read -r sp_lg 2>/dev/null </dev/tty || sp_lg="";;
    esac
    printf "%s" "  $sp - favorite teams, comma separated (Return to skip): "; IFS="$OLDIFS" read -r sp_tm 2>/dev/null </dev/tty || sp_tm=""
    set -- "$@" --sport "$sp|$sp_lg|$sp_tm"
  done
  IFS="$OLDIFS"
  if [ "$#" -gt 1 ]; then "$VPY" "$APPDIR/bridge/bridge.py" "$@" || warn "could not save your profile - set it in ATLAS > Settings"; fi
fi
if [ "$UPDATE" != 1 ] && ask "Download the speech models now (Whisper 'base' ~150 MB + Piper voice ~60 MB)? Otherwise they download on first use." y; then
  "$VPY" "$APPDIR/bridge/bridge.py" --prefetch || warn "model download had problems - ATLAS will retry on first use"
fi

# ---------- standalone app bundle ----------
mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources"
cp "$APPDIR/assets/atlas.icns" "$BUNDLE/Contents/Resources/atlas.icns"
cat > "$BUNDLE/Contents/Info.plist" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>ATLAS</string>
  <key>CFBundleDisplayName</key><string>ATLAS</string>
  <key>CFBundleIdentifier</key><string>com.atlas.app</string>
  <key>CFBundleExecutable</key><string>ATLAS</string>
  <key>CFBundleIconFile</key><string>atlas</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>ATLAS listens for your voice commands.</string>
  <key>NSCameraUsageDescription</key><string>ATLAS uses the camera when you ask it to look.</string>
</dict></plist>
PLISTEOF
cat > "$BUNDLE/Contents/MacOS/ATLAS" <<LAUNCHEOF
#!/bin/bash
exec /bin/bash "$APPDIR/mac/atlas-launch.sh" launch
LAUNCHEOF
chmod +x "$BUNDLE/Contents/MacOS/ATLAS"
[ "$IS_MAC" = 1 ] && xattr -dr com.apple.quarantine "$BUNDLE" >/dev/null 2>&1
[ "$IS_MAC" = 1 ] && touch "$BUNDLE"
ok "app created: $BUNDLE"

# ---------- 4. Connect Hermes ----------
step "4/5  Connecting ATLAS to Hermes (nothing to type)"
if [ "$HERMES_OK" = 1 ]; then
  "$VPY" "$APPDIR/bridge/bridge.py" --connect-hermes || warn "Could not finish connecting to Hermes now - ATLAS's first screen has a 'Fix it' button for that."
else warn "skipped - Hermes is not installed yet"; fi

# ---------- 5. login item ----------
step "5/5  Start at login (optional)"
LOGIN=0
if [ "$MIGRATED_LOGIN" = 1 ]; then LOGIN=1
elif [ "$UPDATE" = 1 ]; then [ -f "$PLIST" ] && LOGIN=1
elif ask "Start ATLAS's bridge automatically when you log in (the window still opens only when you launch ATLAS)?" n; then LOGIN=1; fi
if [ "$LOGIN" = 1 ]; then
  mkdir -p "$(dirname "$PLIST")"
  cat > "$PLIST" <<LAEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$PLIST_LABEL</string>
  <key>ProgramArguments</key><array><string>$VPY</string><string>$APPDIR/bridge/bridge.py</string></array>
  <key>WorkingDirectory</key><string>$APPDIR/bridge</string>
  <key>RunAtLoad</key><true/>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>StandardOutPath</key><string>$DATA/logs/bridge.log</string>
  <key>StandardErrorPath</key><string>$DATA/logs/bridge.log</string>
</dict></plist>
LAEOF
  if [ "$IS_MAC" = 1 ]; then launchctl bootout "gui/$(id -u)/$PLIST_LABEL" >/dev/null 2>&1; launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || warn "launchctl could not load it now; it will start at next login"; fi
  ok "login item installed ($PLIST)"
else
  [ "$UPDATE" != 1 ] && [ -f "$PLIST" ] && { [ "$IS_MAC" = 1 ] && launchctl bootout "gui/$(id -u)/$PLIST_LABEL" >/dev/null 2>&1; rm -f "$PLIST"; ok "existing login item removed"; }
fi

if [ "$UPDATE" = 1 ]; then
  bash "$APPDIR/mac/atlas-launch.sh" bridge
  ok "ATLAS updated - the open ATLAS window reloads by itself"
  pause_exit 0
fi
echo
printf '%s ATLAS is installed.%s\n' "$G" "$N"
echo "  Open it from:  $BUNDLE   (Launchpad/Spotlight: \"ATLAS\"; drag it to the Dock if you like)"
echo "  First launch: macOS will ask for Microphone / Camera access - click Allow (for the browser window ATLAS opens)."
echo "  If macOS says ATLAS.app 'can't be opened': right-click it > Open, or  xattr -dr com.apple.quarantine \"$BUNDLE\""
echo "  Uninstall:    run this installer again and choose 2, or  bash ATLAS-Setup.command --uninstall"
if [ "$IS_MAC" = 1 ] && ask "Launch ATLAS now?" y; then open "$BUNDLE"; fi
pause_exit 0
exit 0
__ATLAS_PAYLOAD__
