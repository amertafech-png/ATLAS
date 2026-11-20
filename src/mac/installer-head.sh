#!/bin/bash
# =====================================================================================
#  ATLAS-Setup.command  -  one-file installer for ATLAS on macOS (Intel + Apple Silicon)
#
#  Double-click in Finder.  First time only, macOS may say it can't verify the file
#  (it is not signed/notarized):  right-click > Open > Open,   or in Terminal run
#      xattr -d com.apple.quarantine ~/Downloads/ATLAS-Setup.command
#
#  It opens an "ATLAS Setup" window (no terminal questions at all):
#    Options page : checkboxes with a one-line explanation and download size for every optional extra
#                   (Hermes, Ollama, speech models, wake-word model, start at login)
#    Install page : step list with check marks - 1 Copy files, 2 Python, 3 Install components,
#                   4 Hermes (find/install, connect key, start), [Ollama], 5 ATLAS app + login item, 6 Done
#    Finish       : opens ATLAS straight into its AI sign-in (ChatGPT / Claude / Grok / OpenRouter / Local),
#                   then name / city / sports.
#  Options:  --uninstall   --yes (no window: saved or default choices)   --extract-to DIR (just unpack)
#            --update (silent in-place update: reuses your earlier choices, keeps your data/Hermes setup, restarts ATLAS)
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
UI_DIR=""
ui_log() { [ -n "$UI_DIR" ] || return 0; [ -n "${1:-}" ] && printf '%s\n' "$1" >> "$UI_DIR/log.txt"; }
if [ -t 1 ]; then B=$'\033[1m'; C=$'\033[1;36m'; G=$'\033[1;32m'; Y=$'\033[1;33m'; R=$'\033[1;31m'; N=$'\033[0m'; else B=""; C=""; G=""; Y=""; R=""; N=""; fi
step() { printf '\n%s==> %s%s\n' "$C" "$*" "$N"; ui_log "== $*"; }
ok()   { printf '  %s[ok]%s %s\n' "$G" "$N" "$*"; ui_log "[ok] $*"; }
warn() { printf '  %s[!]%s  %s\n' "$Y" "$N" "$*"; ui_log "[!] $*"; }
err()  { printf '  %s[x]%s  %s\n' "$R" "$N" "$*"; ui_log "[x] $*"; }
pause_exit() { exit "${1:-0}"; }   # kept for the migration code; there are no 'press Return' prompts any more

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

# ---------- installer window (no terminal prompts): a tiny local page served by perl, shown in a Chrome/Edge/Brave app window ----------
# There are NO prompts in this script. Questions are asked on the Options page of the installer window; --yes / --update never ask
# (they use the choices saved by the last install, or the defaults).
UI_DIR=""; UI_PHASE="starting"; UI_MODE="install"; UI_STATUS=""; UI_SUB=0; UI_PROG=0; UI_RESULT="null"; UI_OPTS_JSON="[]"
UI_INSTALLED=false; UI_INSTVER=""; UI_CANBACK=false; UI_BLURB=""; UI_VERSION=""
STEP_ID=(); STEP_LABEL=(); STEP_STATE=(); STEP_W=()
json_str() { local s="$1"; s=${s//\\/\\\\}; s=${s//\"/\\\"}; s=${s//$'\n'/\\n}; s=${s//$'\r'/}; s=${s//$'\t'/ }; s="$(printf '%s' "$s" | LC_ALL=C tr -d '\000-\010\013\014\016-\037')"; printf '"%s"' "$s"; }
ui_log() { [ -n "$UI_DIR" ] || return 0; [ -n "${1:-}" ] && printf '%s\n' "$1" >> "$UI_DIR/log.txt"; }
ui_progress() {   # overall % from the step weights + how far the current step is (UI_SUB 0-100)
  local i tot=0 done_=0 cur=0
  for i in "${!STEP_ID[@]}"; do
    tot=$((tot + STEP_W[i]))
    case "${STEP_STATE[i]}" in ok|skip) done_=$((done_ + STEP_W[i]));; run) cur=${STEP_W[i]};; esac
  done
  [ "$tot" -gt 0 ] && UI_PROG=$(( (done_ * 100 + cur * UI_SUB) / tot ))
}
ui_flush() {
  [ -n "$UI_DIR" ] || return 0
  local i j="" first=1 lj="" l
  for i in "${!STEP_ID[@]}"; do [ $first = 1 ] || j="$j,"; first=0; j="$j{\"id\":$(json_str "${STEP_ID[i]}"),\"label\":$(json_str "${STEP_LABEL[i]}"),\"state\":\"${STEP_STATE[i]}\"}"; done
  first=1
  if [ -f "$UI_DIR/log.txt" ]; then while IFS= read -r l; do [ $first = 1 ] || lj="$lj,"; first=0; lj="$lj$(json_str "$l")"; done < <(tail -n 60 "$UI_DIR/log.txt"); fi
  printf '{"phase":"%s","platform":"mac","version":%s,"mode":"%s","installed":%s,"installed_version":%s,"can_back":%s,"blurb":%s,"options":%s,"steps":[%s],"status":%s,"progress":%s,"log":[%s],"result":%s}' \
    "$UI_PHASE" "$(json_str "$UI_VERSION")" "$UI_MODE" "$UI_INSTALLED" "$(json_str "$UI_INSTVER")" "$UI_CANBACK" "$(json_str "$UI_BLURB")" "$UI_OPTS_JSON" "$j" "$(json_str "$UI_STATUS")" "$UI_PROG" "$lj" "$UI_RESULT" > "$UI_DIR/state.tmp" && mv -f "$UI_DIR/state.tmp" "$UI_DIR/state.json"
}
ui_plan() { STEP_ID=(); STEP_LABEL=(); STEP_STATE=(); STEP_W=(); local e; for e in "$@"; do IFS='|' read -r a b c <<< "$e"; STEP_ID+=("$a"); STEP_LABEL+=("$b"); STEP_W+=("$c"); STEP_STATE+=(pending); done; UI_SUB=0; ui_progress; }
ui_step() {   # ui_step id ["status text"]
  local i lab=""
  for i in "${!STEP_ID[@]}"; do
    [ "${STEP_STATE[i]}" = run ] && STEP_STATE[i]=ok
    [ "${STEP_ID[i]}" = "$1" ] && { STEP_STATE[i]=run; lab="${STEP_LABEL[i]}"; }
  done
  printf '\n%s==> %s%s\n' "$C" "$lab" "$N"; ui_log "== $lab"
  UI_SUB=0; UI_STATUS="${2:-$lab}"; ui_progress; ui_flush
}
ui_skip() { local i; for i in "${!STEP_ID[@]}"; do [ "${STEP_ID[i]}" = "$1" ] && STEP_STATE[i]=skip; done; ui_progress; ui_flush; }
ui_status() { printf '  %s\n' "$1"; ui_log "$1"; UI_STATUS="$1"; [ -n "${2:-}" ] && UI_SUB="$2"; ui_progress; ui_flush; }
ui_fail() { local i; for i in "${!STEP_ID[@]}"; do [ "${STEP_STATE[i]}" = run ] && STEP_STATE[i]=fail; done; ui_flush; }
ui_done() {   # ui_done ok|fail "title" "message"
  local i
  if [ "$1" = ok ]; then for i in "${!STEP_ID[@]}"; do case "${STEP_STATE[i]}" in run|pending) STEP_STATE[i]=ok;; esac; done; UI_PROG=100; UI_RESULT="{\"ok\":true,\"title\":$(json_str "$2"),\"message\":$(json_str "$3")}"
  else UI_RESULT="{\"ok\":false,\"title\":$(json_str "$2"),\"message\":$(json_str "$3")}"; fi
  UI_PHASE=done; ui_flush
}
run_logged() {   # run a command, stream its output into the Details log, keep its exit code
  local rcf; rcf="$(mktemp "${TMPDIR:-/tmp}/atlas-rc.XXXXXX")"; local last=""
  { "$@" 2>&1; echo $? > "$rcf"; } | while IFS= read -r line; do
    [ -n "$line" ] || continue
    printf '    %s\n' "$line"; ui_log "$line"
    if [ -n "$UI_DIR" ] && [ "$SECONDS" != "$last" ]; then last="$SECONDS"; ui_flush; fi
  done
  local rc; rc="$(cat "$rcf" 2>/dev/null || echo 1)"; rm -f "$rcf"; return "${rc:-1}"
}
FAILED_MSG=""
fail_install() { err "$2"; ui_fail; FAILED_MSG="$2"; return 1; }
ans() {   # ans FIELD  -> value from the last answer (perl ships with macOS)
  perl -MJSON::PP -e 'local $/; open my $f,"<",$ARGV[0] or exit; my $j=decode_json(<$f>); my $v=$j->{$ARGV[1]}; print defined $v ? $v : ""' "$UI_DIR/answer.cur" "$1" 2>/dev/null
}
ans_options() {   # prints  id=1|0  lines
  perl -MJSON::PP -e 'local $/; open my $f,"<",$ARGV[0] or exit; my $j=decode_json(<$f>); my $o=$j->{options}||{}; for my $k (sort keys %$o){ next unless $k=~/^[a-z_]+$/; print "$k=".($o->{$k}?1:0)."\n" }' "$UI_DIR/answer.cur" 2>/dev/null
}
ui_wait_answer() {   # returns 0 and fills answer.cur when the user clicked; 1 when the window was closed
  local t0=$SECONDS now seen
  while [ $((SECONDS - t0)) -lt "${1:-1800}" ]; do
    if [ -f "$UI_DIR/answer.json" ]; then mv -f "$UI_DIR/answer.json" "$UI_DIR/answer.cur"; return 0; fi
    if [ -f "$UI_DIR/seen" ]; then
      seen="$(cat "$UI_DIR/seen" 2>/dev/null)"; now="$(date +%s)"
      case "$seen" in ''|*[!0-9]*) ;; *) [ $((now - seen)) -gt 25 ] && return 1;; esac
    elif [ $((SECONDS - t0)) -gt 120 ]; then return 1; fi
    sleep 0.25
  done
  return 1
}
ui_start() {   # opens the installer window; returns 1 if it cannot (then the choices are not asked)
  command -v perl >/dev/null 2>&1 || return 1
  local tok; tok="$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 24)"
  cp "$STAGE/installer/ui.html" "$UI_DIR/ui.html" || return 1
  perl "$STAGE/mac/ui-server.pl" "$UI_DIR" "$tok" >/dev/null 2>&1 &
  UI_SRV=$!
  local i; for i in $(seq 1 50); do [ -s "$UI_DIR/port" ] && break; sleep 0.1; done
  [ -s "$UI_DIR/port" ] || return 1
  local url="http://127.0.0.1:$(cat "$UI_DIR/port")/?t=$tok"
  echo "$url" > "$UI_DIR/url"; echo "ATLAS_UI_URL=$url"
  UI_PHASE=starting; ui_flush
  if [ -n "${ATLAS_UI_NO_OPEN:-}" ]; then return 0; fi
  if [ "$IS_MAC" = 1 ]; then
    local b opened=0
    for b in "Google Chrome" "Microsoft Edge" "Brave Browser" "Chromium"; do
      if [ -d "/Applications/$b.app" ] || [ -d "$HOME/Applications/$b.app" ]; then
        open -na "$b" --args "--app=$url" "--user-data-dir=$UI_DIR/profile" --window-size=880,760 --no-first-run --no-default-browser-check >/dev/null 2>&1 && { opened=1; break; }
      fi
    done
    [ $opened = 1 ] || open "$url" >/dev/null 2>&1
  else
    xdg-open "$url" >/dev/null 2>&1 || true
  fi
  return 0
}
ui_stop() { [ -n "$UI_DIR" ] && touch "$UI_DIR/stop" 2>/dev/null; [ -n "${UI_SRV:-}" ] && kill "$UI_SRV" >/dev/null 2>&1; }

# ---------- Python / Hermes discovery ----------
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
# Hermes is only *found* here, never configured: ATLAS connects to it itself (key, API server, start, AI sign-in).
# The Hermes installer may put `hermes` in ~/.local/bin, which a shortcut/LaunchAgent does not have on PATH.
find_hermes() {
  local d
  for d in "$HERMES_HOME_DIR/bin" "$HERMES_HOME_DIR/hermes-agent/.hermes/bin" "$HOME/.local/bin" /opt/homebrew/bin /usr/local/bin; do
    [ -d "$d" ] && case ":$PATH:" in *":$d:"*) ;; *) PATH="$PATH:$d";; esac
  done
  export PATH; hash -r 2>/dev/null
  command -v hermes >/dev/null 2>&1
}
have_ollama() { command -v ollama >/dev/null 2>&1 || [ -d "$HOME/Applications/Ollama.app" ] || [ -d /Applications/Ollama.app ]; }

# ---------- options (every question the old terminal installer asked is one row here) ----------
OPT_ID=(); OPT_GROUP=(); OPT_LABEL=(); OPT_DESC=(); OPT_SIZE=(); OPT_VAL=(); OPT_DIS=()
OPT_FILE="$DATA/install-options.json"
opt_add() { OPT_ID+=("$1"); OPT_GROUP+=("$2"); OPT_LABEL+=("$3"); OPT_DESC+=("$4"); OPT_SIZE+=("$5"); OPT_VAL+=("$6"); OPT_DIS+=("${7:-0}"); }
saved_opt() {   # saved_opt id  -> 1/0, or empty if never saved
  [ -f "$OPT_FILE" ] || return 0
  perl -MJSON::PP -e 'local $/; open my $f,"<",$ARGV[0] or exit; my $j=eval{decode_json(<$f>)}||{}; my $v=$j->{$ARGV[1]}; print defined $v ? ($v?1:0) : ""' "$OPT_FILE" "$1" 2>/dev/null
}
build_options() {   # build_options install|update|uninstall
  OPT_ID=(); OPT_GROUP=(); OPT_LABEL=(); OPT_DESC=(); OPT_SIZE=(); OPT_VAL=(); OPT_DIS=()
  if [ "$1" = uninstall ]; then
    opt_add delete_data Uninstall "Also delete ATLAS's saved data" "Notes, settings, voice and speech models, window profile and logs. Off = keep them in case you reinstall." "" 0
    find_hermes && opt_add hermes_api_off Uninstall "Turn Hermes' local API server off again" "Hermes itself is never removed. Leave this off if other tools use its API." "" 0
    return
  fi
  if [ -z "$(find_python)" ] && [ ! -x "$APPDIR/.venv/bin/python" ]; then
    if command -v brew >/dev/null 2>&1; then opt_add python Needed "Python 3.12 (required)" "ATLAS runs on Python and it is not on this Mac, so setup adds it with Homebrew (python.org build)." "~30 MB" 1 1
    else opt_add python Needed "Python 3.12 (required)" "ATLAS needs Python 3.11+. Homebrew is not installed, so setup will open python.org: install Python 3.12 there, then run Setup again." "~30 MB" 1 1; fi
  fi
  find_hermes || opt_add hermes AI "Install Hermes Agent" "The AI engine ATLAS talks to (official installer from hermes-agent.nousresearch.com). Skip it if you will install Hermes yourself." "~1 GB" 1
  have_ollama || opt_add ollama AI "Install Ollama for local models" "Only if you want an AI that runs on this Mac instead of ChatGPT/Claude/Grok (official download from ollama.com; models are downloaded later, only when you pick one)." "~230 MB" 0
  opt_add speech Voice "Download the speech models now" "Whisper (hearing you) and a Piper voice (speaking). Off = they download the first time you talk to ATLAS." "~210 MB" 1
  opt_add wake Voice 'Download the "Hey ATLAS" wake-word model now' "Optional hands-free wake word. This only downloads it; you switch it on later in Settings > Features." "~55 MB" 0
  opt_add login Startup "Start ATLAS when I log in" "Starts the background part only, so ATLAS is ready when you open it. No window appears by itself." "" 1
  local i s
  for i in "${!OPT_ID[@]}"; do s="$(saved_opt "${OPT_ID[i]}")"; [ -n "$s" ] && [ "${OPT_DIS[i]}" = 0 ] && OPT_VAL[i]="$s"; done
}
options_json() {
  local i j="" first=1
  for i in "${!OPT_ID[@]}"; do
    [ $first = 1 ] || j="$j,"; first=0
    j="$j{\"id\":$(json_str "${OPT_ID[i]}"),\"group\":$(json_str "${OPT_GROUP[i]}"),\"label\":$(json_str "${OPT_LABEL[i]}"),\"desc\":$(json_str "${OPT_DESC[i]}"),\"size\":$(json_str "${OPT_SIZE[i]}"),\"value\":$([ "${OPT_VAL[i]}" = 1 ] && echo true || echo false),\"disabled\":$([ "${OPT_DIS[i]}" = 1 ] && echo true || echo false)}"
  done
  printf '[%s]' "$j"
}
O_python=0; O_hermes=0; O_ollama=0; O_speech=0; O_wake=0; O_login=0; O_delete_data=0; O_hermes_api_off=0
apply_defaults() { local i; for i in "${!OPT_ID[@]}"; do eval "O_${OPT_ID[i]}=${OPT_VAL[i]}"; done; }
apply_answer() { local k v; while IFS='=' read -r k v; do [ -n "$k" ] && case "$k" in python) ;; *) eval "O_$k=$v";; esac; done < <(ans_options); }

# ---------- uninstall ----------
run_uninstall() {
  ui_plan "stop|Stop ATLAS|10" "files|Remove ATLAS files and login item|60" "extra|Optional clean-up|20" "done|Done|0"
  ui_step stop "Stopping ATLAS"
  local port; port=$(get_port)
  curl -fsS --max-time 3 -X POST "http://127.0.0.1:$port/api/quit" >/dev/null 2>&1 && ok "bridge stopped"
  ui_step files "Removing files and the login item"
  if [ -f "$PLIST" ]; then
    [ "$IS_MAC" = 1 ] && launchctl bootout "gui/$(id -u)/$PLIST_LABEL" >/dev/null 2>&1
    rm -f "$PLIST"; ok "login item removed"
  fi
  [ -d "$BUNDLE" ] && rm -rf "$BUNDLE" && ok "removed $BUNDLE"
  [ -d "$APPDIR" ] && rm -rf "$APPDIR" && ok "removed $APPDIR"
  ui_step extra "Optional clean-up"
  if [ "$O_delete_data" = 1 ] && [ -d "$DATA" ]; then rm -rf "$DATA"; ok "removed $DATA (your saved data)"; elif [ -d "$DATA" ]; then ui_status "your saved data stays in $DATA"; fi
  ui_log "Hermes Agent itself was NOT touched. The API key ATLAS added (API_SERVER_KEY in $HERMES_HOME_DIR/.env) stays, so other tools keep working."
  if [ "$O_hermes_api_off" = 1 ] && command -v hermes >/dev/null 2>&1; then
    hermes config set API_SERVER_ENABLED false >/dev/null 2>&1 && hermes gateway restart --detach --yes >/dev/null 2>&1; ok "Hermes API server disabled"
  fi
  ui_done ok "ATLAS was uninstalled" "Hermes Agent was left alone. You can delete this installer file."
  return 0
}

# ---------- install ----------
install_ollama() {
  local url="${ATLAS_OLLAMA_URL:-https://ollama.com/download/Ollama-darwin.zip}" z d
  z="$(mktemp "${TMPDIR:-/tmp}/ollama.XXXXXX")"; d="$(mktemp -d "${TMPDIR:-/tmp}/ollama-app.XXXXXX")"
  ui_status "Downloading Ollama (about 230 MB)..." 20
  if ! curl -fL --silent --show-error -o "$z" "$url"; then warn "Ollama could not be downloaded now (you can get it from ollama.com/download)"; rm -rf "$z" "$d"; return 0; fi
  ui_status "Installing Ollama..." 80
  if [ "$IS_MAC" = 1 ]; then ditto -x -k "$z" "$d"; else unzip -q "$z" -d "$d"; fi
  if [ -d "$d/Ollama.app" ]; then
    mkdir -p "$HOME/Applications"; rm -rf "$HOME/Applications/Ollama.app"; mv "$d/Ollama.app" "$HOME/Applications/Ollama.app"
    [ "$IS_MAC" = 1 ] && xattr -dr com.apple.quarantine "$HOME/Applications/Ollama.app" >/dev/null 2>&1
    ok "Ollama installed in ~/Applications (ATLAS will offer your local models in its AI step)"
  else warn "the Ollama download did not contain Ollama.app - you can get it from ollama.com/download"; fi
  rm -rf "$z" "$d"
}

run_install() {   # run_install install|update
  local mode="$1" PY="" VPY="$APPDIR/.venv/bin/python" PIPER_OK=1 HERMES_OK=0 port rc
  local plan=("copy|Copy files|6" "python|Python 3.11+|8" "components|Install components|42" "hermes|Hermes Agent (find or install, connect key, start)|24")
  [ "$O_ollama" = 1 ] && [ "$UPDATE" != 1 ] && plan+=("ollama|Ollama (local models)|12")
  plan+=("shortcuts|ATLAS app and start-at-login|6" "done|Done|0")
  ui_plan "${plan[@]}"
  # ---- 1. copy files ----
  ui_step copy "Copying ATLAS files"
  migrate_from_old_name
  port="$(get_port)"
  if curl -fsS --max-time 2 "http://127.0.0.1:$port/api/health" >/dev/null 2>&1; then
    curl -fsS --max-time 2 -X POST "http://127.0.0.1:$port/api/quit" >/dev/null 2>&1
    for i in $(seq 1 40); do curl -fsS --max-time 1 "http://127.0.0.1:$port/api/health" >/dev/null 2>&1 || break; sleep 0.5; done
  fi
  mkdir -p "$APPDIR" "$DATA/logs"
  rm -rf "$APPDIR/bridge" "$APPDIR/assets" "$APPDIR/mac" "$APPDIR/win" "$APPDIR/installer" "$APPDIR/index.html"
  cp -R "$STAGE"/. "$APPDIR"/ || { fail_install copy "Could not copy the ATLAS files."; return 1; }
  chmod +x "$APPDIR/mac/"*.sh "$APPDIR/mac/ui-server.pl" 2>/dev/null
  ok "files installed in $APPDIR"
  # ---- 2. Python ----
  ui_step python "Checking for Python 3.11+"
  if [ "$UPDATE" = 1 ] && [ -x "$VPY" ]; then ok "using the existing Python environment"
  else
    PY="$(find_python)" || PY=""
    if [ -z "$PY" ]; then
      warn "No Python 3.11 or newer found."
      if command -v brew >/dev/null 2>&1; then
        ui_status "Installing Python 3.12 with Homebrew (a few minutes)..."
        run_logged brew install python@3.12
        PY="$(find_python)" || PY=""
      else
        [ "$IS_MAC" = 1 ] && open "https://www.python.org/downloads/macos/" >/dev/null 2>&1
        fail_install python "Python 3.11+ is needed. python.org was opened: install Python 3.12 there, then run this installer again."; return 1
      fi
      [ -n "$PY" ] || { fail_install python "Python 3.11+ could not be installed. Install it from python.org and run this installer again."; return 1; }
    fi
    ok "Python: $PY ($("$PY" -c 'import sys;print(".".join(map(str,sys.version_info[:3])))'))"
  fi
  # ---- 3. components ----
  ui_step components "Setting up the Python environment"
  if [ ! -x "$VPY" ]; then "$PY" -m venv "$APPDIR/.venv" || { fail_install components "Could not create the Python environment."; return 1; }; fi
  run_logged "$VPY" -m pip install --quiet --upgrade pip
  ui_status "Installing ATLAS packages (aiohttp, faster-whisper, piper-tts, psutil...) - a few minutes the first time" 10
  if ! run_logged "$VPY" -m pip install --disable-pip-version-check --progress-bar off -r "$APPDIR/bridge/requirements.txt"; then
    warn "full install failed - retrying without Piper (ATLAS will speak with the built-in macOS voice)"
    grep -vi '^piper' "$APPDIR/bridge/requirements.txt" > "$DATA/requirements-nopiper.txt"
    run_logged "$VPY" -m pip install --disable-pip-version-check --progress-bar off -r "$DATA/requirements-nopiper.txt" || { fail_install components "Installing the Python packages failed (see Details). Check your internet connection and run the installer again."; return 1; }
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
  ui_status "Checking optional feature packages" 55
  run_logged "$VPY" "$APPDIR/bridge/bridge.py" --install-deps || warn "some optional feature packages could not be installed (see Settings in ATLAS)"
  if [ "$O_speech" = 1 ] && [ "$UPDATE" != 1 ]; then
    ui_status "Downloading the speech models (about 210 MB)..." 70
    run_logged "$VPY" "$APPDIR/bridge/bridge.py" --prefetch || warn "model download had problems - ATLAS will retry on first use"
  fi
  if [ "$O_wake" = 1 ] && [ "$UPDATE" != 1 ]; then
    ui_status "Downloading the wake-word model (about 55 MB)..." 90
    run_logged "$VPY" "$APPDIR/bridge/bridge.py" --prefetch-wake || warn "the wake-word model could not be downloaded now - Settings > Features will fetch it when you switch the wake word on"
  fi
  # ---- 4. Hermes: find / install, connect the key, start ----
  ui_step hermes "Looking for Hermes Agent"
  find_hermes && HERMES_OK=1 || HERMES_OK=0
  if [ "$HERMES_OK" = 0 ] && [ "$O_hermes" = 1 ] && [ "$UPDATE" != 1 ]; then
    ui_status "Installing Hermes Agent with its official installer (a few minutes, nothing to type)..." 10
    local tmpi; tmpi="$(mktemp "${TMPDIR:-/tmp}/hermes-install.XXXXXX")"
    if curl -fsSL "${ATLAS_HERMES_INSTALL_URL:-https://hermes-agent.nousresearch.com/install.sh}" -o "$tmpi"; then run_logged bash "$tmpi" --non-interactive || warn "the Hermes installer reported a problem"; else err "could not download the Hermes installer"; fi
    rm -f "$tmpi"
    find_hermes && HERMES_OK=1 || HERMES_OK=0
  fi
  if [ "$HERMES_OK" = 1 ]; then
    ok "hermes: $(command -v hermes)"
    ui_status "Connecting ATLAS to Hermes (private key, local API, start)..." 55
    run_logged "$VPY" "$APPDIR/bridge/bridge.py" --connect-hermes || warn "Could not finish connecting to Hermes now - ATLAS's first screen has a 'Fix it' button for that."
  else warn "Hermes is not installed (yet). That's fine - ATLAS's first screen installs it with one click."; fi
  # ---- 5. Ollama (optional) ----
  if [ "$O_ollama" = 1 ] && [ "$UPDATE" != 1 ]; then ui_step ollama "Downloading Ollama (about 230 MB)"; install_ollama; fi
  # ---- 6. app bundle + login item ----
  ui_step shortcuts "Creating the ATLAS app"
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
  if [ "$O_login" = 1 ] || [ "$MIGRATED_LOGIN" = 1 ]; then
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
    ok "login item installed ($PLIST)"; O_login=1
  elif [ -f "$PLIST" ]; then
    [ "$IS_MAC" = 1 ] && launchctl bootout "gui/$(id -u)/$PLIST_LABEL" >/dev/null 2>&1; rm -f "$PLIST"; ok "login item removed"
  fi
  # remember the choices (an --update reuses them); a fresh install makes ATLAS open straight into its AI sign-in + "about you" screens
  printf '{"speech":%s,"wake":%s,"ollama":%s,"login":%s,"hermes":%s}\n' "$([ "$O_speech" = 1 ] && echo true || echo false)" "$([ "$O_wake" = 1 ] && echo true || echo false)" "$([ "$O_ollama" = 1 ] && echo true || echo false)" "$([ "$O_login" = 1 ] && echo true || echo false)" "$([ "$O_hermes" = 1 ] && echo true || echo false)" > "$OPT_FILE"
  [ "$mode" = install ] && echo 1 > "$DATA/first-run"
  ui_step done "All done"
  if [ "$UPDATE" = 1 ]; then
    bash "$APPDIR/mac/atlas-launch.sh" bridge
    ok "ATLAS updated - the open ATLAS window reloads by itself"
    ui_done ok "ATLAS was updated" "Your settings, notes and AI connection were kept."
  else
    ui_done ok "ATLAS is installed" "Press Finish to open it."
  fi
  return 0
}

# ================================= main =================================
clear_screen_banner() {
  printf '%s\n' "$C"'    _  _____ _        _    ____'"$N"
  printf '%s\n' "$C"'   / \|_   _| |      / \  / ___|'"$N"'   Personal command interface for Hermes Agent'
  printf '%s\n' "$C"'  / _ \ | | | |     / _ \ \___ \'"$N"'   macOS installer'
  printf '%s\n' "$C"' / ___ \| | | |___ / ___ \ ___) |'"$N"
  printf '%s\n' "$C"'/_/   \_\_| |_____/_/   \_\____/'"$N"
}
clear_screen_banner

installed_version() {
  local m v
  for m in "$APPDIR/bridge/atlas_meta.py" "$OLD_DATA/app/bridge/modi_meta.py"; do   # legacy-modi
    [ -f "$m" ] && v="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$m" | head -1)" && [ -n "$v" ] && { echo "$v"; return; }
  done
}
is_installed() { [ -f "$APPDIR/bridge/bridge.py" ] || [ -f "$OLD_DATA/app/bridge/bridge.py" ]; }   # legacy-modi

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/atlas-setup.XXXXXX")"
trap 'ui_stop; rm -rf "$STAGE"' EXIT
extract_payload "$STAGE/payload" || { err "could not unpack the embedded files (is the installer file complete?)"; exit 1; }
STAGE="$STAGE/payload"
UI_VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$STAGE/bridge/atlas_meta.py" | head -1)"
MODE=install; [ "$UPDATE" = 1 ] && MODE=update; [ "$UNINSTALL" = 1 ] && MODE=uninstall
GUI=0; [ "$YES" != 1 ] && GUI=1
if [ "$GUI" = 1 ]; then
  UI_DIR="$(dirname "$STAGE")/ui"; mkdir -p "$UI_DIR"
  if ! ui_start; then echo "  (the installer window could not start; using the saved or default choices)"; GUI=0; UI_DIR=""; fi
fi
if is_installed; then UI_INSTALLED=true; UI_INSTVER="$(installed_version)"; fi
CODE=0
if [ "$GUI" = 1 ]; then
  echo "  The ATLAS Setup window is open. (If you do not see it, open the address printed above in your browser.)"
  PHASE=options; if [ "$UI_INSTALLED" = true ] && [ "$UNINSTALL" != 1 ]; then PHASE=welcome; MODE=update; fi
  while true; do
    if [ "$PHASE" = welcome ]; then
      UI_PHASE=welcome; UI_MODE=$MODE; ui_flush
      ui_wait_answer || { UI_PHASE=bye; UI_STATUS="Setup closed - nothing was changed."; ui_flush; sleep 1; exit 0; }
      case "$(ans action)" in cancel) UI_PHASE=bye; UI_STATUS="Setup closed - nothing was changed."; ui_flush; sleep 1; exit 0;; choose) if [ "$(ans mode)" = uninstall ]; then MODE=uninstall; else MODE=update; fi; PHASE=options;; esac
      continue
    fi
    build_options "$MODE"; UI_MODE=$MODE; UI_OPTS_JSON="$(options_json)"
    UI_CANBACK=false; [ "$UI_INSTALLED" = true ] && [ "$UNINSTALL" != 1 ] && UI_CANBACK=true
    case "$MODE" in uninstall) UI_BLURB="Nothing is deleted until you press Uninstall.";; update) UI_BLURB="Your earlier choices are filled in. Nothing is downloaded that you leave unticked.";; *) UI_BLURB="Everything here is optional except what is marked required. Sizes are approximate downloads.";; esac
    UI_PHASE=options; ui_flush
    ui_wait_answer || { UI_PHASE=bye; UI_STATUS="Setup closed - nothing was changed."; ui_flush; sleep 1; exit 0; }
    case "$(ans action)" in
      cancel) UI_PHASE=bye; UI_STATUS="Setup closed - nothing was changed."; ui_flush; sleep 1; exit 0;;
      back) PHASE=welcome; continue;;
      start) apply_defaults; apply_answer; break;;
    esac
  done
else
  build_options "$MODE"; apply_defaults
  if [ "$UPDATE" = 1 ]; then   # -update keeps what you chose last time; downloads are not repeated
    s="$(saved_opt login)"; if [ -n "$s" ]; then O_login="$s"; else [ -f "$PLIST" ] && O_login=1 || O_login=0; fi
    O_speech=0; O_wake=0; O_ollama=0
  fi
fi
UI_MODE=$MODE; UI_PHASE=running; ui_flush
if [ "$MODE" = uninstall ]; then run_uninstall; else run_install "$MODE" || CODE=1; fi
if [ "$CODE" != 0 ]; then
  [ -n "$FAILED_MSG" ] || FAILED_MSG="Something unexpected went wrong. See the Details."
  if [ "$GUI" = 1 ]; then ui_done fail "Setup could not finish" "$FAILED_MSG"; fi
  if [ "$UPDATE" = 1 ] && [ "$IS_MAC" = 1 ]; then
    osascript -e 'display dialog "The ATLAS update did not finish. Open ATLAS again, or re-run ATLAS-Setup.command. Log: ~/Library/Application Support/ATLAS/logs/update.log" with title "ATLAS" buttons {"OK"} default button 1 with icon caution' >/dev/null 2>&1
  fi
fi
if [ "$GUI" = 1 ]; then
  if ui_wait_answer 900 && [ "$(ans action)" = finish ] && { [ "$(ans open)" = 1 ] || [ "$(ans open)" = true ]; } && [ "$CODE" = 0 ] && [ "$MODE" != uninstall ]; then
    if [ "$IS_MAC" = 1 ]; then open "$BUNDLE"; else bash "$APPDIR/mac/atlas-launch.sh" launch >/dev/null 2>&1 & fi
  fi
  sleep 1
fi
exit "$CODE"
__ATLAS_PAYLOAD__
