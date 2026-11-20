#!/usr/bin/env bash
# MODI must not depend on Windows Script Host (wscript / cscript / .vbs / .js / .wsf launchers, WScript.Shell COM).
# The only allowed mentions are the lines tagged "#legacy-wsh": the code that migrates shortcuts written by MODI <= 1.2.0.
set -u
cd "$(dirname "$0")/.."
bad=$(grep -rniE 'wscript|cscript|\.vbs|\.wsf|\.vbe|\.jse|WScript\.Shell|Scripting\.FileSystemObject|CreateObject' \
        src tools bridge .github README.md docs RELEASING.md 2>/dev/null \
      | grep -v 'legacy-wsh' | grep -v 'tools/check-no-wsh.sh' | grep -v 'createObjectURL' | grep -v 'URL.createObjectURL')
if ls src/win/*.vbs src/win/*.wsf src/win/*.js >/dev/null 2>&1; then bad="$bad"$'\n'"script-host file present in src/win"; fi
if [ -n "$bad" ]; then echo "Windows Script Host references found:"; echo "$bad"; exit 1; fi
echo "ok: no Windows Script Host dependency (src/win launches via powershell.exe only)"
