#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

mkdir -p "$TMP_DIR/input/nested"
printf 'alpha config\nanswer=42\n' >"$TMP_DIR/input/app.ini"
printf 'embedded note\n' >"$TMP_DIR/input/nested/readme.txt"
printf 'LARGE_PAYLOAD_SENTINEL\n' >"$TMP_DIR/input/large-payload"
dd if=/dev/zero bs=1024 count=2 status=none >>"$TMP_DIR/input/large-payload"
(cd "$TMP_DIR/input" && zip -q "$TMP_DIR/input/bundle.zip" nested/readme.txt)

PYTHONPATH="$ROOT/src" python -m reconkit_re.cli export-context \
  "$TMP_DIR/input" \
  --out-dir "$TMP_DIR/out" \
  --format json \
  --max-files 20 \
  --max-depth 2 \
  --max-binary-analysis-bytes 1024 \
  --max-index-text-chars 80 \
  >"$TMP_DIR/report.json"

jq -e '.llmContext.json == "LLM_CONTEXT.json" and .llmContext.markdown == "LLM_CONTEXT.md"' "$TMP_DIR/out/manifest.json" >/dev/null
jq -e '.schema == "reconkit.llm-context-index.v1"' "$TMP_DIR/out/LLM_CONTEXT.json" >/dev/null
jq -e '.entries[] | select(.path == "app.ini") | .summary.textPreview | contains("answer=42")' "$TMP_DIR/out/LLM_CONTEXT.json" >/dev/null
jq -e '.entries[] | select(.path | contains("bundle.zip::extracted/nested/readme.txt")) | .summary.textPreview | contains("embedded note")' "$TMP_DIR/out/LLM_CONTEXT.json" >/dev/null
jq -e '.entries[] | select(.path == "large-payload") | .summary.analysis.status == "bounded"' "$TMP_DIR/out/LLM_CONTEXT.json" >/dev/null
jq -e '.entries[] | select(.path == "large-payload") | .summary.analysis.headStrings[0] == "LARGE_PAYLOAD_SENTINEL"' "$TMP_DIR/out/LLM_CONTEXT.json" >/dev/null
jq -e '.entries[] | select(.path == "large-payload") | .summary.analysis.headBytesScanned == 1024' "$TMP_DIR/out/LLM_CONTEXT.json" >/dev/null
grep -q 'ReconstructKit LLM Context' "$TMP_DIR/out/LLM_CONTEXT.md"

echo "ok"
