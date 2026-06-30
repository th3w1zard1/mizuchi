#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

mkdir -p "$TMP_DIR/input/Fake DAW/Resources" "$TMP_DIR/input/Fake Game"
printf 'install_mode=silent\ncomponent=aax\n' >"$TMP_DIR/input/Fake DAW/Setup.ini"
printf 'preset_name=Wide Piano\n' >"$TMP_DIR/input/Fake DAW/Resources/preset.txt"
printf 'standalone installer bytes\n' >"$TMP_DIR/input/Standalone.exe"
printf 'game config\n' >"$TMP_DIR/input/Fake Game/config.ini"

PYTHONPATH="$ROOT/src" python -m mizuchi_re.cli export-context-batch \
  "$TMP_DIR/input" \
  --out-dir "$TMP_DIR/out" \
  --item-mode top-level \
  --max-items 3 \
  --max-files-per-item 20 \
  --max-index-text-chars 120 \
  >"$TMP_DIR/report.json"

jq -e '.itemMode == "top-level" and .itemsExported == 3' "$TMP_DIR/out/manifest.json" >/dev/null
jq -e '.items[] | select(.path == "Fake DAW" and (.llmContextJson | endswith("LLM_CONTEXT.json")) and (.llmContextMarkdown | endswith("LLM_CONTEXT.md")))' "$TMP_DIR/out/manifest.json" >/dev/null
jq -e '.entries[] | select(.path == "Setup.ini") | .summary.textPreview | contains("component=aax")' "$TMP_DIR/out/items/Fake_DAW/LLM_CONTEXT.json" >/dev/null
jq -e '.entries[] | select(.path == "Resources/preset.txt") | .summary.textPreview | contains("Wide Piano")' "$TMP_DIR/out/items/Fake_DAW/LLM_CONTEXT.json" >/dev/null
grep -q 'Fake DAW' "$TMP_DIR/out/TREE.md"

echo "ok"
