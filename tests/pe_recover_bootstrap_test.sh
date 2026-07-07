#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${MIZUCHI_KOTOR_BINK_DLL:-/run/media/brunner56/MyBook/SteamLibrary/steamapps/common/swkotor/binkw32.dll}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

if [[ ! -f "$TARGET" ]]; then
  echo "skip: KOTOR binkw32.dll not found at $TARGET"
  exit 0
fi

"$ROOT/scripts/decomp-cli.sh" recover "$TARGET" \
  --work-dir "$TMP_DIR/recover" \
  --source-task-limit 4 \
  --source-synthesis clang \
  --source-synthesis-limit 4 \
  --source-synthesis-max-variants 1 \
  --stop-after plan-strategy \
  --context-max-files 20 \
  --context-max-depth 1 \
  --context-strings-limit 20 \
  --function-analysis none >/dev/null

jq -e '.summary.exports >= 1' "$TMP_DIR/recover/binary-inventory.json" >/dev/null
jq -e '.summary.bySource["pe-export"] >= 1 and .summary.byConfidence.high >= 1' "$TMP_DIR/recover/function-candidates.json" >/dev/null
jq -e '.generatedSourceCandidates == 4 and .semanticSourceCandidates >= 1 and .nonSemanticBootstrapCandidates <= 3 and .targetSlices == 4' "$TMP_DIR/recover/source-generation/summary.json" >/dev/null
jq -e '.generatedCandidates == 4 and .semanticGeneratedCandidates >= 1 and .nonSemanticBootstrapCandidates <= 3 and .codeSliceMatchedCandidates >= 3 and .nonSemanticCodeSliceMatchedCandidates >= 3 and .semanticMismatchedCandidates >= 1 and .acceptedCandidates == 0' "$TMP_DIR/recover/source-synthesis/summary.json" >/dev/null
jq -e '.lanes[] | select(.name == "matching-decompilation" and .status == "nonsemantic-code-slice-evidence")' "$TMP_DIR/recover/strategy.json" >/dev/null
jq -e 'select(.status == "code-slice-matched" and .rule == "target-slice-asm-bootstrap" and .semanticSource == false and .differences == 0)' "$TMP_DIR/recover/source-synthesis/attempts.jsonl" >/dev/null
jq -e 'select(.rule == "stdcall-store-two-stack-args-to-globals" and .semanticSource == true and .status == "mismatched" and .differences == 1)' "$TMP_DIR/recover/source-synthesis/attempts.jsonl" >/dev/null

echo "ok"
