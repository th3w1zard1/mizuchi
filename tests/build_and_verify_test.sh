#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/build-and-verify.sh"
PROMPT="$ROOT/prompts/roundtrip_identity"
TMP_DIR="$(mktemp -d)"
TEMP_MIZUCHI_CONFIG="$ROOT/mizuchi.yaml"
if [[ -e "$TEMP_MIZUCHI_CONFIG" ]]; then
  echo "tests/build_and_verify_test.sh refuses to overwrite existing mizuchi.yaml" >&2
  exit 1
fi
trap 'rm -rf "$TMP_DIR"; rm -f "$TEMP_MIZUCHI_CONFIG"' EXIT
BLOCKED_ROOT="$TMP_DIR/prompts"
BLOCKED_PROMPT="$BLOCKED_ROOT/blocked_fn"

mkdir -p "$BLOCKED_PROMPT"
cat >"$BLOCKED_PROMPT/prompt.md" <<'MD'
# blocked_fn
MD
cat >"$BLOCKED_PROMPT/settings.yaml" <<'YAML'
functionName: blocked_fn
targetObjectPath: prompt:/build/target.o
asm: |
  blocked_fn:
      ret
YAML
cat >"$BLOCKED_PROMPT/case.yaml" <<'YAML'
caseId: blocked_fn
functionName: blocked_fn
targetObjectPath: prompt:/build/target.o
targetFamily: elf-x86_64
proof: objdiff-0
status: blocked
blockedReason: fixture is intentionally blocked
YAML

expect_blocked() {
  local label="$1" expected="$2" out_file="$3" err_file="$4"
  shift 4
  set +e
  "$@" >"$out_file" 2>"$err_file"
  local rc=$?
  set -e
  if [[ "$rc" -eq 0 ]]; then
    echo "expected $label to reject blocked prompt" >&2
    exit 1
  fi
  if [[ "$rc" -ne 3 ]]; then
    echo "expected $label to exit 3 for blocked prompt, got $rc" >&2
    cat "$err_file" >&2 || true
    exit 1
  fi
  grep -q "$expected" "$err_file"
}

rm -f "$PROMPT/build/target.o" "$PROMPT/build/candidate.o" "$PROMPT/build/build-and-verify.json"

out="$("$SCRIPT" --prompt "$PROMPT" --refresh-target)"

printf '%s\n' "$out" | jq -e '.status == "matched"' >/dev/null
printf '%s\n' "$out" | jq -e '(.method == "objdiff") or (.method == "cmp")' >/dev/null
printf '%s\n' "$out" | jq -e '
  .schema == "mizuchi.build-and-verify.v1" and
  .byte_identical == true and
  .target_sha256 == .candidate_sha256 and
  .target_size == .candidate_size and
  (.target_size > 0) and
  (.candidate_source | endswith("/candidate.c")) and
  (.compile_log | endswith("/build-and-verify.compile.log")) and
  (.compile_summary | endswith("/build-and-verify.compile.summary.txt")) and
  (.verify_log | endswith("/build-and-verify.verify.log"))
' >/dev/null
[[ -f "$PROMPT/build/target.o" ]]
[[ -f "$PROMPT/build/candidate.o" ]]
grep -q "BUILD SUCCEEDED" "$PROMPT/build/build-and-verify.compile.summary.txt"
cmp -s "$PROMPT/build/target.o" "$PROMPT/build/candidate.o"

GLOBAL_PROMPT="$TMP_DIR/global_config"
mkdir -p "$GLOBAL_PROMPT/build"
cat >"$TEMP_MIZUCHI_CONFIG" <<'YAML'
global:
  compilerScript: bash ./scripts/compile-local-fixture.sh "{{cFilePath}}" "{{objFilePath}}"
YAML
cat >"$GLOBAL_PROMPT/settings.yaml" <<'YAML'
functionName: global_config_fn
targetObjectPath: prompt:/build/target.o
asm: |
  global_config_fn:
      ret
YAML
cat >"$GLOBAL_PROMPT/case.yaml" <<'YAML'
caseId: global_config_fn
functionName: global_config_fn
targetObjectPath: prompt:/build/target.o
targetSourcePath: prompt:/target.c
candidateSourcePath: prompt:/candidate.c
targetFamily: elf-x86_64
proof: byte-identical-object
status: pending
YAML
cat >"$GLOBAL_PROMPT/prompt.md" <<'MD'
# global_config_fn
MD
cat >"$GLOBAL_PROMPT/target.c" <<'C'
int global_config_fn(void) { return 7; }
C
cp "$GLOBAL_PROMPT/target.c" "$GLOBAL_PROMPT/candidate.c"
global_out="$("$SCRIPT" --prompt "$GLOBAL_PROMPT" --refresh-target)"
printf '%s\n' "$global_out" | jq -e '
  .schema == "mizuchi.build-and-verify.v1" and
  .status == "matched" and
  .byte_identical == true
' >/dev/null
grep -q "BUILD SUCCEEDED" "$GLOBAL_PROMPT/build/build-and-verify.compile.summary.txt"

CUSTOM_PROMPT="$TMP_DIR/custom_task"
TASK_DIR="$CUSTOM_PROMPT/task"
mkdir -p "$TASK_DIR"
printf '\x55\xc3' >"$TASK_DIR/target.bin"
cat >"$TASK_DIR/VERIFY_CANDIDATE.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
cp target.bin candidate.bin
echo FUNCTION_RECONSTRUCTION_CANDIDATE_OK
SH
chmod +x "$TASK_DIR/VERIFY_CANDIDATE.sh"
cat >"$CUSTOM_PROMPT/settings.yaml" <<'YAML'
functionName: custom_task
targetObjectPath: prompt:/task/target.bin
asm: |
  custom_task:
      push %rbp
      ret
YAML
cat >"$CUSTOM_PROMPT/case.yaml" <<'YAML'
caseId: custom_task
functionName: custom_task
targetObjectPath: prompt:/task/target.bin
candidateSourcePath: prompt:/candidate.c
targetFamily: byte-slice
proof: task-byte-identical
status: pending
verifierCommand: bash ./scripts/verify-reconstruction-task.sh --task-dir "{{promptDir}}/task" --candidate "{{candidateSourcePath}}" --candidate-output "{{candidateOutputPath}}"
YAML
cat >"$CUSTOM_PROMPT/prompt.md" <<'MD'
# custom_task
MD
cat >"$CUSTOM_PROMPT/candidate.c" <<'C'
void custom_task(void) {}
C
custom_out="$("$SCRIPT" --prompt "$CUSTOM_PROMPT")"
printf '%s\n' "$custom_out" | jq -e '
  .schema == "mizuchi.build-and-verify.v1" and
  .status == "matched" and
  .method == "custom" and
  .byte_identical == true and
  .target_sha256 == .candidate_sha256 and
  .target_size == .candidate_size
' >/dev/null
[[ -f "$CUSTOM_PROMPT/build/candidate.bin" ]]
cmp -s "$TASK_DIR/target.bin" "$CUSTOM_PROMPT/build/candidate.bin"

NOISY_PROMPT="$TMP_DIR/noisy"
mkdir -p "$NOISY_PROMPT"
cat >"$NOISY_PROMPT/settings.yaml" <<'YAML'
functionName: noisy_failure
targetObjectPath: prompt:/build/target.o
asm: |
  noisy_failure:
      ret
YAML
cat >"$NOISY_PROMPT/case.yaml" <<'YAML'
caseId: noisy_failure
functionName: noisy_failure
targetObjectPath: prompt:/build/target.o
targetSourcePath: prompt:/target.c
candidateSourcePath: prompt:/candidate.c
targetFamily: elf-x86_64
compilerCommand: bash ./scripts/compile-local-fixture.sh "{{cFilePath}}" "{{objFilePath}}"
proof: byte-identical-object
status: pending
YAML
cat >"$NOISY_PROMPT/prompt.md" <<'MD'
# noisy_failure
MD
cat >"$NOISY_PROMPT/target.c" <<'C'
int noisy_failure(void) {
  return 0;
}
C
{
  printf 'int noisy_failure(void) {\n'
  printf '  return missing_symbol + ;\n'
  for i in $(seq 1 1200); do
    printf '  /* noisy compiler context line %04d */\n' "$i"
  done
  printf '}\n'
} >"$NOISY_PROMPT/candidate.c"

set +e
"$SCRIPT" --prompt "$NOISY_PROMPT" --refresh-target >"$TMP_DIR/noisy.out" 2>"$TMP_DIR/noisy.err"
noisy_rc=$?
set -e
[[ "$noisy_rc" -eq 1 ]] || {
  echo "expected noisy compile failure exit 1, got $noisy_rc" >&2
  cat "$TMP_DIR/noisy.err" >&2 || true
  exit 1
}
summary="$NOISY_PROMPT/build/build-and-verify.compile.summary.txt"
[[ -f "$summary" ]]
grep -q "BUILD FAILED" "$summary"
grep -q "first_error:" "$summary"
grep -q "full_log:" "$summary"
summary_size="$(stat -c %s "$summary")"
[[ "$summary_size" -lt 7000 ]] || {
  echo "compile summary too large: $summary_size bytes" >&2
  exit 1
}

expect_blocked \
  "build-and-verify" \
  "build-and-verify: prompt is blocked: fixture is intentionally blocked" \
  "$TMP_DIR/blocked-build.out" \
  "$TMP_DIR/blocked-build.err" \
  "$SCRIPT" --prompt "$BLOCKED_PROMPT"

expect_blocked \
  "run-programmatic-phase" \
  "run-programmatic-phase: prompt is blocked: fixture is intentionally blocked" \
  "$TMP_DIR/blocked-programmatic.out" \
  "$TMP_DIR/blocked-programmatic.err" \
  "$ROOT/scripts/run-programmatic-phase.sh" --prompt "$BLOCKED_PROMPT"

expect_blocked \
  "run-ai-phase" \
  "run-ai-phase: prompt is blocked: fixture is intentionally blocked" \
  "$TMP_DIR/blocked-ai.out" \
  "$TMP_DIR/blocked-ai.err" \
  "$ROOT/scripts/run-ai-phase.sh" --prompt "$BLOCKED_PROMPT"

expect_blocked \
  "decomp-cli" \
  "run-programmatic-phase: prompt is blocked: fixture is intentionally blocked" \
  "$TMP_DIR/blocked-cli.out" \
  "$TMP_DIR/blocked-cli.err" \
  env MIZUCHI_PROMPTS_DIR="$BLOCKED_ROOT" "$ROOT/scripts/decomp-cli.sh" decomp-function blocked_fn
if grep -q "entering AI phase" "$TMP_DIR/blocked-cli.err"; then
  echo "blocked decomp-cli run fell through to AI phase" >&2
  exit 1
fi

echo "ok"
