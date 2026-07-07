#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${RECONKIT_KOTOR_BINK_DLL:-/run/media/brunner56/MyBook/SteamLibrary/steamapps/common/swkotor/binkw32.dll}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

if [[ ! -f "$TARGET" ]]; then
  echo "skip: KOTOR binkw32.dll not found at $TARGET"
  exit 0
fi

"$ROOT/scripts/decomp-cli.sh" recover "$TARGET" \
  --work-dir "$TMP_DIR/recover" \
  --source-task-limit 1 \
  --source-synthesis clang-cl \
  --source-synthesis-limit 1 \
  --source-synthesis-max-variants 2 \
  --stop-after plan-strategy \
  --context-max-files 20 \
  --context-max-depth 1 \
  --context-strings-limit 20 \
  --function-analysis none >/dev/null

jq -e '.generatedSourceCandidates == 1 and .semanticSourceCandidates == 1 and .nonSemanticBootstrapCandidates == 0' "$TMP_DIR/recover/source-generation/summary.json" >/dev/null
jq -e '.compiler == "clang-cl" and .semanticGeneratedCandidates == 1 and .semanticMismatchedCandidates >= 1 and .sourceShapeSearches == 1 and .sourceShapeSearchMatches == 0 and .sliceFailedCandidates == 0 and .acceptedCandidates == 0' "$TMP_DIR/recover/source-synthesis/summary.json" >/dev/null
jq -e 'select(.compiler == "clang-cl" and .verificationTier == "synthetic-target-coff-objdiff" and .rule == "stdcall-store-two-stack-args-to-globals" and .semanticSource == true and .status == "mismatched" and .differences == 1 and .sourceShapeSearchSummary.status == "no-match")' "$TMP_DIR/recover/source-synthesis/attempts.jsonl" >/dev/null
SEARCH_JSON="$(find "$TMP_DIR/recover/source-synthesis" -path '*/source-shape-search/summary.json' | head -n 1)"
[[ -f "$SEARCH_JSON" ]]
jq -e '.schema == "reconkit.source-shape-search.v1" and .status == "no-match" and (.attempts | length) >= 5 and .best.commonPrefixBytes >= 1' "$SEARCH_JSON" >/dev/null
jq -e '.lanes[] | select(.name == "matching-decompilation" and .status == "semantic-source-needs-compiler-profile")' "$TMP_DIR/recover/strategy.json" >/dev/null
jq -e '.sourceSynthesisSummary.sourceShapeSearches == 1 and .sourceSynthesisSummary.sourceShapeSearchMatches == 0' "$TMP_DIR/recover/strategy.json" >/dev/null

echo "ok"
