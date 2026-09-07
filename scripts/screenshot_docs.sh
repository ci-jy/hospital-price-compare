#!/usr/bin/env bash
# Render docs/index.html with headless Chromium and save docs/screenshot.png.
# Usage: scripts/screenshot_docs.sh [width] [height]
set -euo pipefail
cd "$(dirname "$0")/.."

browser=""
for b in chromium chromium-browser google-chrome google-chrome-stable; do
    if command -v "$b" >/dev/null; then browser="$b"; break; fi
done
[ -n "$browser" ] || { echo "no Chromium or Chrome found" >&2; exit 1; }

profile=$(mktemp -d "${TMPDIR:-/tmp}/hpc-shot.XXXXXX")
trap 'rm -rf "$profile"' EXIT

"$browser" --headless=new --no-sandbox --disable-gpu --hide-scrollbars \
    --user-data-dir="$profile" --window-size="${1:-1400},${2:-1300}" \
    --screenshot="$PWD/docs/screenshot.png" "file://$PWD/docs/index.html" 2>/dev/null
echo "wrote docs/screenshot.png"
