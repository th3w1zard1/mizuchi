#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

cat >"$TMP_DIR/tiny.c" <<'C'
int return_zero(void) { return 0; }
void _start(void) { (void)return_zero(); for (;;) {} }
C

clang \
  -m32 \
  -nostdlib \
  -Wl,--build-id=none \
  -O2 \
  -ffreestanding \
  -fno-pic \
  -fno-pie \
  -fno-asynchronous-unwind-tables \
  -fno-stack-protector \
  -fno-ident \
  "$TMP_DIR/tiny.c" \
  -o "$TMP_DIR/tiny.elf"

"$ROOT/scripts/decomp-cli.sh" recover "$TMP_DIR/tiny.elf" \
  --work-dir "$TMP_DIR/recover" \
  --source-task-limit 5 \
  --source-synthesis clang \
  --source-synthesis-limit 5 \
  --stop-after plan-strategy \
  --context-max-files 20 \
  --context-max-depth 1 \
  --context-strings-limit 20 >/dev/null

jq -e '.schema == "reconkit.source-generation.v1" and .targetSlices >= 1' "$TMP_DIR/recover/source-generation/summary.json" >/dev/null
jq -e '.schema == "reconkit.source-parity-synthesis-summary.v1" and .compiler == "clang" and .codeSliceMatchedCandidates >= 1 and .semanticCodeSliceMatchedCandidates >= 1 and .acceptedCandidates == 0' "$TMP_DIR/recover/source-synthesis/summary.json" >/dev/null
jq -e '.schema == "reconkit.recovery-strategy.v1" and (.lanes[] | select(.name == "matching-decompilation" and .status == "semantic-code-slice-evidence"))' "$TMP_DIR/recover/strategy.json" >/dev/null
jq -e 'select(.status == "code-slice-matched" and .differences == 0 and .verificationTier == "synthetic-target-object-objdiff")' "$TMP_DIR/recover/source-synthesis/attempts.jsonl" >/dev/null

echo "ok"
