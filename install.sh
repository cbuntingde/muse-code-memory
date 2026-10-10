#!/usr/bin/env bash
# Install muse-memory as a Muse Code plugin (user scope) and seed stores.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "== muse-memory install =="
command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }

echo "[1/4] Running tests..."
python3 -m unittest discover -s "$ROOT/tests"

echo "[2/4] Validating plugin..."
muse skills validate "$ROOT/skills/memory" --json >/dev/null
muse plugins validate "$ROOT" --json >/dev/null

if [ "${1:-}" != "--cli-only" ]; then
  echo "[3/4] Installing plugin (user scope)..."
  muse plugins install "$ROOT" --scope user
  muse plugins approve muse-memory
  muse plugins enable muse-memory
else
  echo "[3/4] Skipped plugin install (--cli-only)."
fi

echo "[4/4] Seeding memory stores..."
PYTHONPATH="$ROOT" python3 -m muse_memory init
PYTHONPATH="$ROOT" python3 -m muse_memory stats

echo "Done. Restart muse to load hooks. Try /recall, /remember, /consolidate."
