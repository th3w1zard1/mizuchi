#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

if ! command -v clang >/dev/null 2>&1; then
  echo "skip: clang not installed"
  exit 0
fi
if ! command -v objdump >/dev/null 2>&1; then
  echo "skip: objdump not installed"
  exit 0
fi
if ! command -v objdiff >/dev/null 2>&1; then
  echo "skip: objdiff not installed"
  exit 0
fi

cat >"$TMP_DIR/macho_tiny.c" <<'C'
unsigned int framed(void) {
    return 0x12345678u;
}
C

if ! clang \
  -target x86_64-apple-macosx10.12 \
  -c \
  -O0 \
  -ffreestanding \
  -fno-asynchronous-unwind-tables \
  -fno-stack-protector \
  -fno-ident \
  "$TMP_DIR/macho_tiny.c" \
  -o "$TMP_DIR/macho_tiny.o" 2>"$TMP_DIR/clang.err"; then
  echo "skip: clang cannot emit Mach-O object on this host"
  sed -n '1,20p' "$TMP_DIR/clang.err"
  exit 0
fi

PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m reconkit_re.reconkit_cli "$TMP_DIR/macho_tiny.o" \
  --work-dir "$TMP_DIR/recover" \
  --no-resume \
  --no-byte-authority \
  --source-synthesis clang \
  --source-synthesis-limit 5 \
  --source-synthesis-source-quality high-level-c \
  --stop-after plan-strategy \
  --context-max-files 20 \
  --context-max-depth 1 \
  --context-strings-limit 20 >/dev/null

jq -e '.format == "macho" and .architectureHint == "x86_64"' "$TMP_DIR/recover/target.json" >/dev/null
jq -e '.format == "macho" and .summary.functionSymbols == 1 and .summary.codeRanges == 1' "$TMP_DIR/recover/binary-inventory.json" >/dev/null
jq -e '.summary.candidateCount == 1 and .summary.bySource["macho-symbol"] == 1' "$TMP_DIR/recover/function-candidates.json" >/dev/null
jq -e '.generatedByRule["x86-64-framed-return-immediate-cdecl"] == 1 and .highLevelSourceCandidates == 1' "$TMP_DIR/recover/source-generation/summary.json" >/dev/null
jq -e '.successfulFunctions == 1 and .highLevelSourceMatches == 1 and .matchedBySourceQuality["high-level-c"] == 1' "$TMP_DIR/recover/source-synthesis/summary.json" >/dev/null
jq -e 'select(.rule == "x86-64-framed-return-immediate-cdecl" and .status == "code-slice-matched" and .differences == 0 and .sourceQuality == "high-level-c")' "$TMP_DIR/recover/source-synthesis/plugin-attempts.jsonl" >/dev/null
jq -e 'select(.fallback == "objdump-disassembly-byte-compare" and .status == "matched" and .differences == 0 and .targetCodeSha256 == .candidateCodeSha256)' "$TMP_DIR"/recover/source-synthesis/cases/*/profile_00_row-hint/verify.json >/dev/null

echo "ok"
