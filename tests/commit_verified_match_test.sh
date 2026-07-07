#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/commit-verified-match.sh"
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
cat >"$PROMPT/notes.md" <<'MD'
verified fixture
MD

out="$("$SCRIPT" --prompt "$PROMPT" --path prompt:/notes.md --dry-run)"
printf '%s\n' "$out" | jq -e '
  .schema == "mizuchi.commit-receipt.v1" and
  .status == "verified" and
  .dryRun == true and
  (.paths | map(endswith("candidate.c")) | any) and
  (.paths | map(endswith("build-and-verify.json")) | any) and
  (.paths | map(endswith("notes.md")) | any)
' >/dev/null
jq -e '
  .schema == "mizuchi.build-and-verify.v1" and
  .status == "matched" and
  .byte_identical == true
' "$PROMPT/build/build-and-verify.json" >/dev/null

cat >"$PROMPT/candidate.c" <<'C'
int roundtrip_identity(int value) {
  return value * 3 + 8;
}
C
set +e
"$SCRIPT" --prompt "$PROMPT" --dry-run >"$TMP_DIR/mismatch.out" 2>"$TMP_DIR/mismatch.err"
rc=$?
set -e
[[ "$rc" -eq 1 ]]
grep -q "verification did not match; refusing commit" "$TMP_DIR/mismatch.err"

BLOCKED="$TMP_DIR/prompts/blocked"
mkdir -p "$BLOCKED"
cp "$PROMPT/settings.yaml" "$BLOCKED/settings.yaml"
cp "$PROMPT/prompt.md" "$BLOCKED/prompt.md"
cp "$PROMPT/target.c" "$BLOCKED/target.c"
cp "$PROMPT/candidate.c" "$BLOCKED/candidate.c"
cat >"$BLOCKED/case.yaml" <<'YAML'
status: blocked
blockedReason: fixture blocked
candidateSourcePath: prompt:/candidate.c
YAML
set +e
"$SCRIPT" --prompt "$BLOCKED" --dry-run >"$TMP_DIR/blocked.out" 2>"$TMP_DIR/blocked.err"
blocked_rc=$?
set -e
[[ "$blocked_rc" -eq 3 ]]
grep -q "commit-verified-match: prompt is blocked: fixture blocked" "$TMP_DIR/blocked.err"

echo "ok"
