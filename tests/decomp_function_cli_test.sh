#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/decomp-cli.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

write_prompt() {
  local prompt="$1" candidate_expr="$2" status="${3:-pending}" blocked_reason="${4:-}"
  mkdir -p "$prompt"
  cat >"$prompt/settings.yaml" <<'YAML'
functionName: roundtrip_identity
targetObjectPath: prompt:/build/target.o
asm: |
  roundtrip_identity:
      leal    7(%rdi,%rdi,2), %eax
      ret
YAML
  cat >"$prompt/case.yaml" <<YAML
caseId: roundtrip_identity
functionName: roundtrip_identity
targetObjectPath: prompt:/build/target.o
targetSourcePath: prompt:/target.c
candidateSourcePath: prompt:/candidate.c
targetFamily: elf-x86_64
compilerCommand: bash ./scripts/compile-local-fixture.sh "{{cFilePath}}" "{{objFilePath}}"
proof: byte-identical-object
status: $status
YAML
  if [[ -n "$blocked_reason" ]]; then
    printf 'blockedReason: %s\n' "$blocked_reason" >>"$prompt/case.yaml"
  fi
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

prompts_dir="$TMP_DIR/prompts"
mkdir -p "$prompts_dir"

matched="$prompts_dir/matched"
write_prompt "$matched" "value * 3 + 7"
mkdir -p "$matched/build"
cat >"$matched/build/ai-phase.json" <<'JSON'
{
  "schema": "mizuchi.ai-phase.v1",
  "status": "manual-required",
  "runner": "cursor-native"
}
JSON
MIZUCHI_PROMPTS_DIR="$prompts_dir" "$SCRIPT" decomp-function matched >"$TMP_DIR/matched.out" 2>"$TMP_DIR/matched.err"
jq -e '
  .schema == "mizuchi.decomp-function.v1" and
  .status == "matched" and
  .exitCode == 0 and
  .terminalPhase == "programmatic" and
  .programmaticStatus == "matched" and
  .matchedStage == "candidate" and
  .aiStatus == null
' "$matched/build/decomp-function.json" >/dev/null

mismatch="$prompts_dir/mismatch"
write_prompt "$mismatch" "value * 3 + 8"
set +e
PATH="/usr/bin:/bin" MIZUCHI_PROMPTS_DIR="$prompts_dir" MIZUCHI_IMAGE="localhost/mizuchi-missing:never" "$SCRIPT" decomp-function mismatch >"$TMP_DIR/mismatch.out" 2>"$TMP_DIR/mismatch.err"
mismatch_rc=$?
set -e
[[ "$mismatch_rc" -eq 3 ]] || {
  echo "expected manual fallback exit 3, got $mismatch_rc" >&2
  cat "$TMP_DIR/mismatch.err" >&2 || true
  exit 1
}
grep -q "entering AI phase" "$TMP_DIR/mismatch.err"
jq -e '
  .schema == "mizuchi.decomp-function.v1" and
  .status == "manual-required" and
  .exitCode == 3 and
  .terminalPhase == "ai" and
  .programmaticStatus == "no-match" and
  .aiStatus == "manual-required" and
  .aiRunner == "cursor-native"
' "$mismatch/build/decomp-function.json" >/dev/null

ai_success="$prompts_dir/ai_success"
write_prompt "$ai_success" "value * 3 + 8"
mkdir -p "$TMP_DIR/bin"
cat >"$TMP_DIR/bin/mizuchi" <<'SH'
#!/usr/bin/env bash
[[ "$1" == "run" && "$2" == "--config" ]] || exit 2
exit 0
SH
chmod +x "$TMP_DIR/bin/mizuchi"
PATH="$TMP_DIR/bin:/usr/bin:/bin" MIZUCHI_PROMPTS_DIR="$prompts_dir" "$SCRIPT" decomp-function ai_success >"$TMP_DIR/ai_success.out" 2>"$TMP_DIR/ai_success.err"
grep -q "AI phase via native mizuchi run" "$TMP_DIR/ai_success.err"
jq -e '
  .schema == "mizuchi.ai-phase.v1" and
  .status == "matched" and
  .runner == "native-mizuchi" and
  .exitCode == 0
' "$ai_success/build/ai-phase.json" >/dev/null
jq -e '
  .schema == "mizuchi.decomp-function.v1" and
  .status == "matched" and
  .exitCode == 0 and
  .terminalPhase == "ai" and
  .programmaticStatus == "no-match" and
  .aiStatus == "matched" and
  .aiRunner == "native-mizuchi"
' "$ai_success/build/decomp-function.json" >/dev/null

ai_failure="$prompts_dir/ai_failure"
write_prompt "$ai_failure" "value * 3 + 8"
cat >"$TMP_DIR/bin/mizuchi" <<'SH'
#!/usr/bin/env bash
[[ "$1" == "run" && "$2" == "--config" ]] || exit 2
exit 7
SH
chmod +x "$TMP_DIR/bin/mizuchi"
set +e
PATH="$TMP_DIR/bin:/usr/bin:/bin" MIZUCHI_PROMPTS_DIR="$prompts_dir" "$SCRIPT" decomp-function ai_failure >"$TMP_DIR/ai_failure.out" 2>"$TMP_DIR/ai_failure.err"
ai_failure_rc=$?
set -e
[[ "$ai_failure_rc" -eq 7 ]] || {
  echo "expected AI failure exit 7, got $ai_failure_rc" >&2
  cat "$TMP_DIR/ai_failure.err" >&2 || true
  exit 1
}
jq -e '
  .schema == "mizuchi.ai-phase.v1" and
  .status == "failed" and
  .runner == "native-mizuchi" and
  .exitCode == 7
' "$ai_failure/build/ai-phase.json" >/dev/null
jq -e '
  .schema == "mizuchi.decomp-function.v1" and
  .status == "failed" and
  .exitCode == 7 and
  .terminalPhase == "ai" and
  .programmaticStatus == "no-match" and
  .aiStatus == "failed" and
  .aiRunner == "native-mizuchi"
' "$ai_failure/build/decomp-function.json" >/dev/null

blocked="$prompts_dir/blocked"
write_prompt "$blocked" "value * 3 + 7" "blocked" "fixture blocked"
mkdir -p "$blocked/build"
cat >"$blocked/build/ai-phase.json" <<'JSON'
{
  "schema": "mizuchi.ai-phase.v1",
  "status": "blocked",
  "runner": null
}
JSON
set +e
MIZUCHI_PROMPTS_DIR="$prompts_dir" "$SCRIPT" decomp-function blocked >"$TMP_DIR/blocked.out" 2>"$TMP_DIR/blocked.err"
blocked_rc=$?
set -e
[[ "$blocked_rc" -eq 3 ]] || {
  echo "expected blocked exit 3, got $blocked_rc" >&2
  cat "$TMP_DIR/blocked.err" >&2 || true
  exit 1
}
if grep -q "entering AI phase" "$TMP_DIR/blocked.err"; then
  echo "blocked decomp-function fell through to AI phase" >&2
  exit 1
fi
jq -e '
  .schema == "mizuchi.decomp-function.v1" and
  .status == "blocked" and
  .exitCode == 3 and
  .terminalPhase == "programmatic" and
  .reason == "fixture blocked" and
  .programmaticStatus == "blocked" and
  .aiStatus == null
' "$blocked/build/decomp-function.json" >/dev/null

context="$(MIZUCHI_PROMPTS_DIR="$prompts_dir" "$ROOT/scripts/get-workspace-context.sh")"
printf '%s\n' "$context" | jq -e '
  .build_artifacts[]
  | select(.prompt == "matched")
  | .decomp_function_status == "matched" and .terminal_phase == "programmatic"
' >/dev/null
printf '%s\n' "$context" | jq -e '
  .build_artifacts[]
  | select(.prompt == "mismatch")
  | .decomp_function_status == "manual-required" and .terminal_phase == "ai"
' >/dev/null
printf '%s\n' "$context" | jq -e '
  .build_artifacts[]
  | select(.prompt == "ai_success")
  | .decomp_function_status == "matched" and .terminal_phase == "ai" and .ai_status == "matched"
' >/dev/null
printf '%s\n' "$context" | jq -e '
  .build_artifacts[]
  | select(.prompt == "ai_failure")
  | .decomp_function_status == "failed" and .terminal_phase == "ai" and .ai_status == "failed"
' >/dev/null

echo "ok"
