#!/bin/bash
# =====================================================================================
#  MODI-Setup.command  -  one-file installer for MODI on macOS (Intel + Apple Silicon)
#
#  Double-click in Finder.  First time only, macOS may say it can't verify the file
#  (it is not signed/notarized):  right-click > Open > Open,   or in Terminal run
#      xattr -d com.apple.quarantine ~/Downloads/MODI-Setup.command
#
#  What it does (asks before each big step):
#    1. checks Python 3.11+ (offers Homebrew or python.org if missing)
#    2. installs Hermes Agent with its official installer if it is not installed
#    3. runs `hermes setup` so Hermes can sign in to YOUR AI account
#    4. optional: Ollama + local fallback model
#    5. turns on Hermes' local API with a fresh random key (never printed) + restarts the gateway
#    6. installs MODI (bridge + HUD + voice models) and a standalone MODI.app in ~/Applications
#    7. optional: start MODI's bridge at login (LaunchAgent)
#  Options:  --uninstall   --yes (accept defaults, no prompts)   --extract-to DIR (just unpack)
#            --update (non-interactive in-place update: keeps your data/Hermes setup, restarts MODI)
# =====================================================================================
set -u
YES=0; UNINSTALL=0; UPDATE=0; EXTRACT_TO=""
while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) YES=1;;
    --uninstall) UNINSTALL=1;;
    --update) UPDATE=1; YES=1;;      # non-interactive in-place update (used by MODI's self-update)
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
  awk 'f{print} /^__MODI_PAYLOAD__$/{f=1}' "$SELF" | $dec | tar -xzf - -C "$1"
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
    osascript -e 'display dialog "The MODI update did not finish. Open MODI again, or re-run MODI-Setup.command. Log: ~/Library/Application Support/MODI/logs/update.log" with title "MODI" buttons {"OK"} default button 1 with icon caution' >/dev/null 2>&1
  fi
  if [ "$YES" != 1 ] && [ -t 0 ]; then echo; printf "%s" "Press Return to close this window..."; read -r _ 2>/dev/null </dev/tty; fi; exit "$code"; }

IS_MAC=0; [ "$(uname -s)" = Darwin ] && IS_MAC=1
if [ "$IS_MAC" = 0 ] && [ "${MODI_ALLOW_LINUX:-}" != 1 ]; then
  err "MODI-Setup.command is for macOS. (Set MODI_ALLOW_LINUX=1 only for testing.)"; exit 1
fi
if [ "$IS_MAC" = 1 ]; then DATA="${MODI_HOME:-$HOME/Library/Application Support/MODI}"; else DATA="${MODI_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/MODI}"; fi
APPDIR="$DATA/app"
APP_BUNDLE_DIR="${MODI_APPS_DIR:-$HOME/Applications}"
BUNDLE="$APP_BUNDLE_DIR/MODI.app"
PLIST_LABEL="com.modi.bridge"
PLIST="${MODI_LAUNCHAGENTS_DIR:-$HOME/Library/LaunchAgents}/$PLIST_LABEL.plist"
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

# ---------- uninstall ----------
do_uninstall() {
  step "Uninstalling MODI"
  local port; port=$(get_port)
  curl -fsS --max-time 3 -X POST "http://127.0.0.1:$port/api/quit" >/dev/null 2>&1 && ok "bridge stopped"
  if [ -f "$PLIST" ]; then
    [ "$IS_MAC" = 1 ] && launchctl bootout "gui/$(id -u)/$PLIST_LABEL" >/dev/null 2>&1
    rm -f "$PLIST"; ok "login item removed"
  fi
  [ -d "$BUNDLE" ] && rm -rf "$BUNDLE" && ok "removed $BUNDLE"
  [ -d "$APPDIR" ] && rm -rf "$APPDIR" && ok "removed $APPDIR"
  if [ -d "$DATA" ] && ask "Also delete MODI's saved data (notes, voice models, speech models, window profile, logs)?" n; then
    rm -rf "$DATA"; ok "removed $DATA"
  fi
  echo
  echo "  Hermes Agent itself was NOT touched. MODI's API key and API_SERVER_* settings remain in $HERMES_HOME_DIR/.env."
  if command -v hermes >/dev/null 2>&1 && ask "Turn Hermes' local API server off again (hermes config set API_SERVER_ENABLED false + restart gateway)?" n; then
    hermes config set API_SERVER_ENABLED false >/dev/null 2>&1 && hermes gateway restart >/dev/null 2>&1; ok "Hermes API server disabled"
  fi
  ok "MODI uninstalled."
  pause_exit 0
}

clear_screen_banner() {
  printf '%s\n' "${C}  __  __  ___  ____ ___${N}"
  printf '%s\n' "${C} |  \\/  |/ _ \\|  _ \\_ _|${N}   Personal command interface for Hermes Agent"
  printf '%s\n' "${C} | |\\/| | | | | | | | |${N}   macOS installer"
  printf '%s\n' "${C} |_|  |_|\\___/|____/___|${N}"
}
clear_screen_banner

if [ "$UNINSTALL" = 1 ]; then do_uninstall; fi
if [ "$YES" != 1 ] && [ -z "${MODI_NO_MENU:-}" ]; then
  echo; echo "  1) Install / update MODI"; echo "  2) Uninstall MODI"; echo "  3) Quit"
  printf "%s" "  Choose [1]: "; read -r choice 2>/dev/null </dev/tty || choice=1
  case "${choice:-1}" in 2) do_uninstall;; 3) exit 0;; esac
fi

# ---------- 1. Python ----------
step "1/7  Checking Python 3.11+"
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

if [ "$UPDATE" = 1 ]; then
  step "Update mode: Hermes setup is left as it is"
else
# ---------- 2. Hermes ----------
step "2/7  Hermes Agent"
if ! command -v hermes >/dev/null 2>&1 && [ ! -x "$HERMES_HOME_DIR/hermes-agent/venv/bin/hermes" ]; then
  warn "Hermes Agent is not installed."
  if ask "Install it now with the official installer (https://hermes-agent.nousresearch.com/install.sh)?" y; then
    tmpi="$(mktemp "${TMPDIR:-/tmp}/hermes-install.XXXXXX")"
    if curl -fsSL https://hermes-agent.nousresearch.com/install.sh -o "$tmpi"; then bash "$tmpi"; else err "download failed"; fi
    rm -f "$tmpi"
    hash -r; export PATH="$HOME/.local/bin:$PATH"
  fi
fi
if command -v hermes >/dev/null 2>&1; then
  ok "hermes: $(command -v hermes)"; HERMES_OK=1
else
  warn "Hermes CLI not found - MODI will be installed, but run the Hermes installer and 'hermes setup' before using it."; HERMES_OK=0
fi

# ---------- 3. hermes setup ----------
step "3/7  Sign in to your AI provider (hermes setup)"
if [ "$HERMES_OK" = 1 ]; then
  if [ ! -f "$HERMES_HOME_DIR/config.yaml" ]; then
    warn "Hermes has not been set up yet."
    if ask "Run 'hermes setup' now (choose your model and sign in with YOUR account)?" y; then hermes setup || warn "hermes setup did not finish - you can re-run it later"; fi
  else
    ok "Hermes is already configured"
    if ask "Run 'hermes setup' again to change model/login?" n; then hermes setup || true; fi
  fi
else warn "skipped (no Hermes)"; fi

# ---------- 4. Ollama ----------
step "4/7  Optional: offline fallback model (Ollama)"
if command -v ollama >/dev/null 2>&1 || [ -d /Applications/Ollama.app ]; then ok "Ollama is already installed"
elif ask "Install Ollama so Hermes has an offline fallback model?" n; then
  if command -v brew >/dev/null 2>&1; then brew install ollama && { brew services start ollama >/dev/null 2>&1 || true; }
  else open "https://ollama.com/download" 2>/dev/null; echo "  Install Ollama from the page that opened, then run:  ollama pull hermes3:8b"; fi
fi
if command -v ollama >/dev/null 2>&1 && ! ollama list 2>/dev/null | grep -q 'hermes3'; then
  if ask "Download the hermes3:8b model now (about 5 GB)?" n; then ollama pull hermes3:8b || warn "pull failed - run 'ollama pull hermes3:8b' later"; fi
  echo "  To use it as Hermes' fallback model, run:  hermes fallback"
fi

# ---------- 5. Hermes API ----------
step "5/7  Turning on Hermes' local API for MODI (key is not printed)"
if [ "$HERMES_OK" = 1 ]; then
  have_key=0
  if [ -f "$HERMES_HOME_DIR/.env" ]; then
    k="$(grep -E '^API_SERVER_KEY=' "$HERMES_HOME_DIR/.env" | tail -1 | cut -d= -f2- | tr -d "\"' \r")"
    [ "${#k}" -ge 16 ] && have_key=1; k=""
  fi
  if [ "$have_key" = 1 ]; then ok "API key already present (kept)"
  else
    newkey="modi-$(openssl rand -hex 24)"
    if hermes config set API_SERVER_KEY "$newkey" >/dev/null 2>&1; then ok "fresh random API key written to Hermes config"; else err "could not write API key"; fi
    newkey=""
  fi
  hermes config set API_SERVER_ENABLED true >/dev/null 2>&1 && ok "API server enabled"
  # Gateway: restart if it exists, otherwise install it as a launchd service
  if hermes gateway restart >/dev/null 2>&1; then ok "gateway restarted"
  else
    warn "gateway was not running - installing it"
    hermes gateway install >/dev/null 2>&1 || true
    hermes gateway start >/dev/null 2>&1 || hermes gateway restart >/dev/null 2>&1 || warn "could not start the gateway; run 'hermes gateway run' in a Terminal"
  fi
  printf '  waiting for the API'
  up=0; for i in $(seq 1 30); do if curl -fsS --max-time 2 http://127.0.0.1:8642/health >/dev/null 2>&1; then up=1; break; fi; printf '.'; sleep 1; done; echo
  if [ "$up" = 1 ]; then ok "Hermes API is answering on 127.0.0.1:8642"; else warn "Hermes API not answering yet (check: hermes gateway status). MODI will use 'hermes -z' as a fallback."; fi
else warn "skipped (no Hermes)"; fi

fi   # end of non-update steps 2-5

# ---------- 6. Install MODI ----------
step "6/7  Installing MODI to $APPDIR"
port="$(get_port)"
if curl -fsS --max-time 2 "http://127.0.0.1:$port/api/health" >/dev/null 2>&1; then
  curl -fsS --max-time 2 -X POST "http://127.0.0.1:$port/api/quit" >/dev/null 2>&1
  for i in $(seq 1 40); do curl -fsS --max-time 1 "http://127.0.0.1:$port/api/health" >/dev/null 2>&1 || break; sleep 0.5; done
fi
mkdir -p "$APPDIR" "$DATA/logs"
stage="$(mktemp -d "${TMPDIR:-/tmp}/modi-payload.XXXXXX")"
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
  warn "full install failed - retrying without Piper (MODI will speak with the built-in macOS voice)"
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
"$VPY" "$APPDIR/bridge/bridge.py" --install-deps || warn "some optional feature packages could not be installed (see Settings in MODI)"
if [ "$UPDATE" != 1 ] && [ "$YES" != 1 ] && ! grep -q '"profile"' "$DATA/config.json" 2>/dev/null; then
  echo "  A few personal touches (all optional - press Return to skip; you can change them later in MODI > Settings):"
  printf "%s" "  Name your assistant (Return keeps MODI; 1-20 characters): "; read -r p_aname 2>/dev/null </dev/tty || p_aname=""
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
  if [ "$#" -gt 1 ]; then "$VPY" "$APPDIR/bridge/bridge.py" "$@" || warn "could not save your profile - set it in MODI > Settings"; fi
fi
if [ "$UPDATE" != 1 ] && ask "Download the speech models now (Whisper 'base' ~150 MB + Piper voice ~60 MB)? Otherwise they download on first use." y; then
  "$VPY" "$APPDIR/bridge/bridge.py" --prefetch || warn "model download had problems - MODI will retry on first use"
fi

# ---------- standalone app bundle ----------
mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources"
cp "$APPDIR/assets/modi.icns" "$BUNDLE/Contents/Resources/modi.icns"
cat > "$BUNDLE/Contents/Info.plist" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>MODI</string>
  <key>CFBundleDisplayName</key><string>MODI</string>
  <key>CFBundleIdentifier</key><string>com.modi.app</string>
  <key>CFBundleExecutable</key><string>MODI</string>
  <key>CFBundleIconFile</key><string>modi</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>MODI listens for your voice commands.</string>
  <key>NSCameraUsageDescription</key><string>MODI uses the camera when you ask it to look.</string>
</dict></plist>
PLISTEOF
cat > "$BUNDLE/Contents/MacOS/MODI" <<LAUNCHEOF
#!/bin/bash
exec /bin/bash "$APPDIR/mac/modi-launch.sh" launch
LAUNCHEOF
chmod +x "$BUNDLE/Contents/MacOS/MODI"
[ "$IS_MAC" = 1 ] && xattr -dr com.apple.quarantine "$BUNDLE" >/dev/null 2>&1
[ "$IS_MAC" = 1 ] && touch "$BUNDLE"
ok "app created: $BUNDLE"

# ---------- 7. login item ----------
step "7/7  Start at login (optional)"
LOGIN=0
if [ "$UPDATE" = 1 ]; then [ -f "$PLIST" ] && LOGIN=1
elif ask "Start MODI's bridge automatically when you log in (the window still opens only when you launch MODI)?" n; then LOGIN=1; fi
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
  bash "$APPDIR/mac/modi-launch.sh" bridge
  ok "MODI updated - the open MODI window reloads by itself"
  pause_exit 0
fi
echo
printf '%s MODI is installed.%s\n' "$G" "$N"
echo "  Open it from:  $BUNDLE   (Launchpad/Spotlight: \"MODI\"; drag it to the Dock if you like)"
echo "  First launch: macOS will ask for Microphone / Camera access - click Allow (for the browser window MODI opens)."
echo "  If macOS says MODI.app 'can't be opened': right-click it > Open, or  xattr -dr com.apple.quarantine \"$BUNDLE\""
echo "  Uninstall:    run this installer again and choose 2, or  bash MODI-Setup.command --uninstall"
if [ "$IS_MAC" = 1 ] && ask "Launch MODI now?" y; then open "$BUNDLE"; fi
pause_exit 0
exit 0
__MODI_PAYLOAD__
