#!/usr/bin/env bash
# sync_to_github.sh — push local B2B changes to
#   github.com/BitCodingSolution/lead_generator  (B2B/ subfolder).
#
# Usage:
#   bash scripts/sync_to_github.sh "my commit message"
#   bash scripts/sync_to_github.sh                       # uses a default msg
#
# The remote tree is `lead_generator/B2B/...`, but our local B2B folder is
# standalone — so we keep a mirror clone at MIRROR and rsync into its B2B/
# subfolder before committing. Runtime output (node_modules, .next, data.db,
# batches, logs, etc.) is excluded so only source code ships.

set -euo pipefail

MIRROR="/c/temp/lead_generator"
REMOTE_URL="https://github.com/BitCodingSolution/lead_generator.git"
LOCAL_B2B="H:\\Lead Generator\\B2B"
PY="C:/Program Files/Python311/python.exe"
MSG="${1:-sync: local changes}"

echo "--- sync_to_github.sh ---"
echo "msg: $MSG"

if [ ! -d "$MIRROR/.git" ]; then
  echo "[mirror] missing — cloning fresh"
  mkdir -p "$(dirname "$MIRROR")"
  git clone "$REMOTE_URL" "$MIRROR"
fi

cd "$MIRROR"
echo "[mirror] pulling latest main"
git checkout main -q
git pull --ff-only

echo "[mirror] replacing B2B/ with local working copy"
rm -rf B2B
"$PY" - <<PYEOF
import shutil
shutil.copytree(
    r'${LOCAL_B2B}',
    'B2B',
    ignore=shutil.ignore_patterns(
        '.git', '.git.*',
        'node_modules', '.next', 'test-results', 'playwright-report',
        '__pycache__', '.claude', 'logs', 'batches', 'raw',
        '*.db', '*.db-journal', '*.db-wal', '*.db-shm',
        'schedules.json', 'schedules.json.corrupt',
        '*.log', '*.xlsx.bak', '.venv', 'venv',
        '.fernet.key', '*.secret', '.env', '.env.*',
        'cvs',
    ),
)
print('[mirror] copy done')
PYEOF

git add B2B

if git diff --cached --quiet; then
  echo "[done] no changes to commit"
  exit 0
fi

echo "[mirror] committing"
git -c user.name="Pradip Kachhadiya" -c user.email="pradip@bitcodingsolutions.com" \
  commit -m "$MSG"

echo "[mirror] pushing"
git push

echo "✓ pushed to main — https://github.com/BitCodingSolution/lead_generator/tree/main/B2B"
