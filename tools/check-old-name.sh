#!/usr/bin/env bash
# The product was renamed MODI -> ATLAS. Fail if the old name shows up anywhere user-visible.
# Allowed: lines tagged "legacy-modi" (the upgrade/migration code and its notes) and this script.
cd "$(dirname "$0")/.." || exit 2
bad=$(grep -rnIiE '(^|[^a-z])modi([^a-z]|$)' . --exclude-dir=.git --exclude-dir=dist --exclude-dir=node_modules --exclude-dir=__pycache__ \
        --exclude=check-old-name.sh | grep -v 'legacy-modi')
if [ -n "$bad" ]; then echo "Old product name MODI found (tag intentional legacy lines with 'legacy-modi'):"; echo "$bad"; exit 1; fi
echo "OK: no user-visible MODI left."
