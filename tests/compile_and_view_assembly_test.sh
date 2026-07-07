#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/compile-and-view-assembly.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

PROMPT="$TMP_DIR/prompts/roundtrip_identity"
mkdir -p "$PROMPT"
cat >"$PROMPT/settings.yaml" <<'YAML'
functionName: roundtrip_identity
targetObjectPath: prompt:/build/target.o
asm: |
  roundtrip_identity:
      leal    7(%rdi,%rdi,2), %eax
      ret
YAML
cat >"$PROMPT/case.yaml" <<'YAML'
caseId: roundtrip_identity
functionName: roundtrip_identity
targetObjectPath: prompt:/build/target.o
targetSourcePath: prompt:/target.c
candidateSourcePath: prompt:/candidate.c
targetFamily: elf-x86_64
compilerCommand: bash ./scripts/compile-local-fixture.sh "{{cFilePath}}" "{{objFilePath}}"
proof: byte-identical-object
status: matched
YAML
cat >"$PROMPT/prompt.md" <<'MD'
# roundtrip_identity
MD
cat >"$PROMPT/target.c" <<'C'
int roundtrip_identity(int value) {
  return value * 3 + 7;
}
C
cat >"$PROMPT/candidate.c" <<'C'
int roundtrip_identity(int value) {
  return value * 3 + 7;
}
C

bad_candidate="$TMP_DIR/bad.c"
cat >"$bad_candidate" <<'C'
int roundtrip_identity(int value) {
  return value * 3 + 8;
}
C

out="$("$SCRIPT" --prompt "$PROMPT" --code-file "$bad_candidate" 2>"$TMP_DIR/bad.err")"
printf '%s\n' "$out" | grep -q "=== disassembly: roundtrip_identity (candidate) ==="
printf '%s\n' "$out" | grep -q "=== objdiff summary ==="
printf '%s\n' "$out" | grep -q "diff_count: non-zero"
printf '%s\n' "$out" | grep -q "verdict: NOT_MATCHED"
jq -e '
  .schema == "mizuchi.build-and-verify.v1" and
  .status == "mismatched" and
  .byte_identical == false and
  .target_sha256 != .candidate_sha256 and
  .target_size > 0 and
  .candidate_size > 0
' "$PROMPT/build/build-and-verify.json" >/dev/null

out_match="$(cat "$PROMPT/candidate.c" | "$SCRIPT" --prompt "$PROMPT" --code-stdin 2>"$TMP_DIR/match.err")"
printf '%s\n' "$out_match" | grep -q "diff_count: 0"
printf '%s\n' "$out_match" | grep -q "verdict: MATCH"
jq -e '
  .schema == "mizuchi.build-and-verify.v1" and
  .status == "matched" and
  .byte_identical == true and
  .target_sha256 == .candidate_sha256
' "$PROMPT/build/build-and-verify.json" >/dev/null

invalid_candidate="$TMP_DIR/invalid.c"
cat >"$invalid_candidate" <<'C'
int roundtrip_identity(int value) {
  return value * ;
}
C
set +e
"$SCRIPT" --prompt "$PROMPT" --code-file "$invalid_candidate" >"$TMP_DIR/invalid.out" 2>"$TMP_DIR/invalid.err"
invalid_rc=$?
set -e
[[ "$invalid_rc" -eq 1 ]] || {
  echo "expected invalid candidate to exit 1, got $invalid_rc" >&2
  cat "$TMP_DIR/invalid.err" >&2 || true
  exit 1
}
grep -q "compile-and-view-assembly: compile failed" "$TMP_DIR/invalid.err"
if grep -q "=== disassembly:" "$TMP_DIR/invalid.out"; then
  echo "invalid candidate reused stale disassembly" >&2
  exit 1
fi
[[ ! -f "$PROMPT/build/candidate.o" ]] || {
  echo "invalid candidate left stale candidate.o" >&2
  exit 1
}

BLOCKED_PROMPT="$TMP_DIR/prompts/blocked"
mkdir -p "$BLOCKED_PROMPT"
cp "$PROMPT/settings.yaml" "$BLOCKED_PROMPT/settings.yaml"
cp "$PROMPT/prompt.md" "$BLOCKED_PROMPT/prompt.md"
cp "$PROMPT/target.c" "$BLOCKED_PROMPT/target.c"
cp "$PROMPT/candidate.c" "$BLOCKED_PROMPT/candidate.c"
ruby -ryaml - "$PROMPT/case.yaml" "$BLOCKED_PROMPT/case.yaml" <<'RUBY'
source, dest = ARGV
data = YAML.load_file(source)
data["status"] = "blocked"
data["blockedReason"] = "fixture blocked"
File.write(dest, data.to_yaml)
RUBY

set +e
"$SCRIPT" --prompt "$BLOCKED_PROMPT" --code-file "$PROMPT/candidate.c" >"$TMP_DIR/blocked.out" 2>"$TMP_DIR/blocked.err"
blocked_rc=$?
set -e
[[ "$blocked_rc" -eq 3 ]] || {
  echo "expected blocked prompt to exit 3, got $blocked_rc" >&2
  cat "$TMP_DIR/blocked.err" >&2 || true
  exit 1
}
grep -q "compile-and-view-assembly: prompt is blocked: fixture blocked" "$TMP_DIR/blocked.err"

echo "ok"
