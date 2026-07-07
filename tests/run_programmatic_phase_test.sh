#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/run-programmatic-phase.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

write_prompt() {
  local prompt="$1" candidate_expr="$2"
  mkdir -p "$prompt"
  cat >"$prompt/settings.yaml" <<'YAML'
functionName: roundtrip_identity
targetObjectPath: prompt:/build/target.o
asm: |
  roundtrip_identity:
      leal    7(%rdi,%rdi,2), %eax
      ret
YAML
  cat >"$prompt/case.yaml" <<'YAML'
caseId: roundtrip_identity
functionName: roundtrip_identity
targetObjectPath: prompt:/build/target.o
targetSourcePath: prompt:/target.c
candidateSourcePath: prompt:/candidate.c
targetFamily: elf-x86_64
compilerCommand: bash ./scripts/compile-local-fixture.sh "{{cFilePath}}" "{{objFilePath}}"
proof: byte-identical-object
status: pending
YAML
  cat >"$prompt/prompt.md" <<'MD'
# roundtrip_identity
MD
  cat >"$prompt/target.c" <<'C'
int roundtrip_identity(int value) {
  return value * 3 + 7;
}
C
  cat >"$prompt/candidate.c" <<C
int roundtrip_identity(int value) {
  return $candidate_expr;
}
C
}

matched_prompt="$TMP_DIR/matched"
write_prompt "$matched_prompt" "value * 3 + 7"
"$SCRIPT" --prompt "$matched_prompt" --skip-context --skip-m2c --skip-permuter >/dev/null
jq -e '
  .schema == "mizuchi.programmatic-phase.v1" and
  .status == "matched" and
  .exitCode == 0 and
  .matchedStage == "candidate" and
  (.stages | index("context:skipped")) and
  (.stages | index("m2c:skipped")) and
  (.stages | index("candidate:matched"))
' "$matched_prompt/build/programmatic-phase.json" >/dev/null

mismatch_prompt="$TMP_DIR/mismatch"
write_prompt "$mismatch_prompt" "value * 3 + 8"
set +e
"$SCRIPT" --prompt "$mismatch_prompt" --skip-context --skip-m2c --skip-permuter >"$TMP_DIR/mismatch.out" 2>"$TMP_DIR/mismatch.err"
mismatch_rc=$?
set -e
[[ "$mismatch_rc" -eq 1 ]] || {
  echo "expected no-match exit 1, got $mismatch_rc" >&2
  cat "$TMP_DIR/mismatch.err" >&2 || true
  exit 1
}
jq -e '
  .schema == "mizuchi.programmatic-phase.v1" and
  .status == "no-match" and
  .exitCode == 1 and
  .matchedStage == null and
  .reason == "permuter skipped" and
  (.stages | index("candidate:mismatched")) and
  (.stages | index("permuter:skipped"))
' "$mismatch_prompt/build/programmatic-phase.json" >/dev/null

blocked_prompt="$TMP_DIR/blocked"
write_prompt "$blocked_prompt" "value * 3 + 7"
ruby -ryaml - "$blocked_prompt/case.yaml" <<'RUBY'
path = ARGV.fetch(0)
data = YAML.load_file(path)
data["status"] = "blocked"
data["blockedReason"] = "fixture blocked"
File.write(path, data.to_yaml)
RUBY

set +e
"$SCRIPT" --prompt "$blocked_prompt" >"$TMP_DIR/blocked.out" 2>"$TMP_DIR/blocked.err"
blocked_rc=$?
set -e
[[ "$blocked_rc" -eq 3 ]] || {
  echo "expected blocked exit 3, got $blocked_rc" >&2
  cat "$TMP_DIR/blocked.err" >&2 || true
  exit 1
}
jq -e '
  .schema == "mizuchi.programmatic-phase.v1" and
  .status == "blocked" and
  .exitCode == 3 and
  .reason == "fixture blocked" and
  (.stages | index("blocked"))
' "$blocked_prompt/build/programmatic-phase.json" >/dev/null

echo "ok"
