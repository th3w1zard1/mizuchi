#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/decomp-readiness.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

write_prompt() {
  local prompt="$1" status="${2:-pending}"
  mkdir -p "$prompt/build"
  cat >"$prompt/prompt.md" <<'MD'
# sample_fn
MD
  cat >"$prompt/settings.yaml" <<'YAML'
functionName: sample_fn
targetObjectPath: prompt:/build/target.o
asm: |
  sample_fn:
      ret
YAML
  cat >"$prompt/case.yaml" <<YAML
caseId: sample_fn
functionName: sample_fn
targetObjectPath: prompt:/build/target.o
targetFamily: elf-x86_64
binaryPath: /tmp/sample-binary
proof: objdiff-0
status: $status
compilerCommand: cp "{{cFilePath}}" "{{objFilePath}}"
YAML
  printf 'target-object\n' >"$prompt/build/target.o"
}

fake_bin="$TMP_DIR/bin"
mkdir -p "$fake_bin"
cat >"$fake_bin/objdiff" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$fake_bin/objdiff"

ready_prompt="$TMP_DIR/ready/sample_fn"
write_prompt "$ready_prompt" pending
ready_json="$(PATH="$fake_bin:$PATH" "$SCRIPT" --prompt "$ready_prompt")"
jq -e '.status == "ready"' <<<"$ready_json" >/dev/null
jq -e '.checks.targetObjectPresent == true and .checks.objdiffPresent == true and .checks.compilerPlaceholder == false' <<<"$ready_json" >/dev/null

missing_prompt="$TMP_DIR/missing_target/sample_fn"
write_prompt "$missing_prompt" pending
rm -f "$missing_prompt/build/target.o"
set +e
missing_json="$(PATH="$fake_bin:$PATH" "$SCRIPT" --prompt "$missing_prompt")"
missing_rc=$?
set -e
[[ "$missing_rc" -eq 1 ]]
jq -e '.status == "not-ready"' <<<"$missing_json" >/dev/null
jq -e '.blockers[] | contains("targetObjectPath does not exist")' <<<"$missing_json" >/dev/null

blocked_prompt="$TMP_DIR/blocked/sample_fn"
write_prompt "$blocked_prompt" blocked
cat >>"$blocked_prompt/case.yaml" <<'YAML'
blockedReason: no matching compiler command
YAML
set +e
blocked_json="$(PATH="$fake_bin:$PATH" "$SCRIPT" --prompt "$blocked_prompt")"
blocked_rc=$?
set -e
[[ "$blocked_rc" -eq 1 ]]
jq -e '.blockers[] | contains("case.yaml status is blocked")' <<<"$blocked_json" >/dev/null

placeholder_prompt="$TMP_DIR/placeholder/sample_fn"
write_prompt "$placeholder_prompt" pending
cat >"$placeholder_prompt/case.yaml" <<'YAML'
caseId: sample_fn
functionName: sample_fn
targetObjectPath: prompt:/build/target.o
targetFamily: elf-x86_64
binaryPath: /tmp/sample-binary
proof: objdiff-0
status: pending
compilerCommand: bash ./scripts/compile-placeholder.sh "{{cFilePath}}" "{{objFilePath}}"
YAML
set +e
placeholder_json="$(PATH="$fake_bin:$PATH" "$SCRIPT" --prompt "$placeholder_prompt")"
placeholder_rc=$?
set -e
[[ "$placeholder_rc" -eq 1 ]]
jq -e '.checks.compilerPlaceholder == true' <<<"$placeholder_json" >/dev/null
jq -e '.blockers[] | contains("compiler command is still the placeholder")' <<<"$placeholder_json" >/dev/null

custom_prompt="$TMP_DIR/custom_verifier/sample_fn"
write_prompt "$custom_prompt" pending
cat >"$custom_prompt/case.yaml" <<'YAML'
caseId: sample_fn
functionName: sample_fn
targetObjectPath: prompt:/build/target.o
candidateSourcePath: prompt:/candidate.c
targetFamily: byte-slice
proof: task-byte-identical
status: pending
verifierCommand: bash ./scripts/verify-reconstruction-task.sh --task-dir "{{promptDir}}/task" --candidate "{{candidateSourcePath}}" --candidate-output "{{candidateOutputPath}}"
YAML
custom_json="$("$SCRIPT" --prompt "$custom_prompt")"
jq -e '
  .status == "ready" and
  .compilerSource == "custom verifier" and
  .checks.customVerifierConfigured == true and
  .checks.compilerConfigured == false and
  .checks.objdiffPresent == false and
  ([.blockers[] | select(. == "compiler command is missing" or . == "objdiff is not on PATH")] | length) == 0
' <<<"$custom_json" >/dev/null

prompts_dir="$TMP_DIR/prompts"
write_prompt "$prompts_dir/ready_fn" pending
write_prompt "$prompts_dir/blocked_fn" blocked
cat >>"$prompts_dir/blocked_fn/case.yaml" <<'YAML'
blockedReason: fixture blocker
YAML
set +e
summary_json="$(PATH="$fake_bin:$PATH" "$SCRIPT" --all --prompts-dir "$prompts_dir")"
summary_rc=$?
set -e
[[ "$summary_rc" -eq 1 ]]
jq -e '.schema == "reconkit.decomp-readiness-summary.v1"' <<<"$summary_json" >/dev/null
jq -e '.status == "not-ready" and .total == 2 and .ready == 1 and .notReady == 1' <<<"$summary_json" >/dev/null
jq -e '.blockerSummary | has("case.yaml status is blocked: fixture blocker")' <<<"$summary_json" >/dev/null

all_ready_dir="$TMP_DIR/all_ready"
write_prompt "$all_ready_dir/ready_one" pending
write_prompt "$all_ready_dir/ready_two" pending
all_ready_json="$(PATH="$fake_bin:$PATH" "$SCRIPT" --all --prompts-dir "$all_ready_dir")"
jq -e '.status == "ready" and .total == 2 and .ready == 2 and .notReady == 0' <<<"$all_ready_json" >/dev/null

echo "ok"
