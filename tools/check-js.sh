#!/usr/bin/env bash
# Lints the HUD's inline <script> and the module scripts for bugs that only show up at run time in a browser
# (v1.2.0 shipped with a `const` that was assigned later -> "Assignment to constant variable" after "Hey ATLAS").
# Needs Node (npx downloads eslint on first use). Set ESLINT_BIN to use a local copy.
cd "$(dirname "$0")/.." || exit 2
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
python3 - "$tmp" <<'PY' || exit 2
import re, sys, glob, shutil, os
out = sys.argv[1]
s = open('index.html', encoding='utf-8').read()
open(os.path.join(out, 'hud.js'), 'w', encoding='utf-8').write('\n'.join(m for m in re.findall(r'<script>([\s\S]*?)</script>', s)))
for f in glob.glob('bridge/modules/*.js') + glob.glob('src/**/*.js', recursive=True): shutil.copy(f, os.path.join(out, os.path.basename(f)))
open(os.path.join(out, 'eslint.config.js'), 'w').write('module.exports=[{files:["**/*.js"],languageOptions:{ecmaVersion:2022,sourceType:"script"},rules:{"no-const-assign":"error","no-func-assign":"error","no-class-assign":"error","no-dupe-keys":"error","no-redeclare":"error"}}];')
PY
cd "$tmp" || exit 2
if [ -n "${ESLINT_BIN:-}" ]; then node "$ESLINT_BIN" -c eslint.config.js . ; else npx --yes eslint@9 -c eslint.config.js . ; fi
rc=$?; [ $rc = 0 ] && echo "ok: no const reassignment / duplicate declarations in the HUD scripts"; exit $rc
