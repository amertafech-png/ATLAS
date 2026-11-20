#!/usr/bin/env bash
# Lists every place ATLAS's own code can fetch or install something (URLs, pip, winget, brew, model pulls).
# docs/INSTALL-NOTES.md ("What ATLAS downloads") is written from this output; re-run it after changing any download and update the doc.
cd "$(dirname "$0")/.." || exit 2
echo "== URLs (outside this computer) =="
grep -rnoE 'https?://[A-Za-z0-9._/~%?=&:-]+' bridge/*.py bridge/modules/*.py src index.html 2>/dev/null \
  | grep -vE '127\.0\.0\.1|localhost|www\.w3\.org|apple\.com/DTDs|example' | sed 's/^/  /' | sort -u
echo "== pip =="
grep -rnE 'pip".*install|-m pip|"requirements"' bridge/*.py bridge/modules/*.py src 2>/dev/null | sed 's/^/  /'
echo "  bridge/requirements.txt:"; sed 's/^/    /' bridge/requirements.txt
echo "== system package managers / model pulls =="
grep -rnE 'winget install|brew install|piper\.download_voices|WhisperModel\(|/api/pull' bridge/*.py bridge/modules/*.py src 2>/dev/null | sed 's/^/  /'
