#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/validate-case-manifests.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

write_prompt() {
  local prompt="$1" status="$2"
  mkdir -p "$prompt"
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
proof: objdiff-0
status: $status
YAML
}

write_matched_verifier_report() {
  local prompt="$1"
  mkdir -p "$prompt/build"
  printf 'matched-object\n' >"$prompt/build/target.o"
  cp "$prompt/build/target.o" "$prompt/build/candidate.o"
  local sha size
  sha="$(sha256sum "$prompt/build/target.o" | awk '{print $1}')"
  size="$(stat -c %s "$prompt/build/target.o")"
  jq -n \
    --arg prompt_name "$(basename "$prompt")" \
    --arg function_name "sample_fn" \
    --arg target_object "$prompt/build/target.o" \
    --arg candidate_object "$prompt/build/candidate.o" \
    --arg sha "$sha" \
    --argjson size "$size" \
    '{
      schema: "mizuchi.build-and-verify.v1",
      status: "matched",
      method: "cmp",
      prompt: $prompt_name,
      function_name: $function_name,
      candidate_source: null,
      target_object: $target_object,
      candidate_object: $candidate_object,
      target_sha256: $sha,
      candidate_sha256: $sha,
      target_size: $size,
      candidate_size: $size,
      compile_log: null,
      verify_log: null,
      byte_identical: true
    }' >"$prompt/build/build-and-verify.json"
}

expect_valid() {
  local prompt_root="$1"
  "$SCRIPT" "$prompt_root" >/dev/null
}

expect_invalid() {
  local prompt_root="$1" expected="$2"
  local err="$TMP_DIR/$(basename "$prompt_root").err"
  set +e
  "$SCRIPT" "$prompt_root" >"$TMP_DIR/invalid.out" 2>"$err"
  local rc=$?
  set -e
  [[ "$rc" -eq 1 ]] || {
    echo "expected validator to exit 1, got $rc" >&2
    cat "$err" >&2 || true
    exit 1
  }
  grep -q "$expected" "$err"
}

valid_root="$TMP_DIR/valid"
write_prompt "$valid_root/sample_fn" pending
expect_valid "$valid_root"
expect_valid "$valid_root/sample_fn"

blocked_root="$TMP_DIR/blocked"
write_prompt "$blocked_root/sample_fn" blocked
expect_invalid "$blocked_root" "missing blockedReason"

invalid_status_root="$TMP_DIR/invalid_status"
write_prompt "$invalid_status_root/sample_fn" made_up
expect_invalid "$invalid_status_root" "unknown status: made_up"
expect_invalid "$invalid_status_root/sample_fn" "unknown status: made_up"

standalone_verifier_root="$TMP_DIR/standalone_verifier"
standalone_verifier_prompt="$standalone_verifier_root/sample_fn"
write_prompt "$standalone_verifier_prompt" pending
write_matched_verifier_report "$standalone_verifier_prompt"
expect_valid "$standalone_verifier_root"

standalone_stale_root="$TMP_DIR/standalone_stale"
standalone_stale_prompt="$standalone_stale_root/sample_fn"
write_prompt "$standalone_stale_prompt" pending
write_matched_verifier_report "$standalone_stale_prompt"
printf 'mutated-object\n' >"$standalone_stale_prompt/build/candidate.o"
expect_invalid "$standalone_stale_root" "verifier report candidate hash mismatch"

objdiff_report_root="$TMP_DIR/objdiff_report"
objdiff_report_prompt="$objdiff_report_root/sample_fn"
write_prompt "$objdiff_report_prompt" pending
mkdir -p "$objdiff_report_prompt/build"
printf 'target-container\n' >"$objdiff_report_prompt/build/target.o"
printf 'candidate-container-with-different-metadata\n' >"$objdiff_report_prompt/build/candidate.o"
objdiff_target_sha="$(sha256sum "$objdiff_report_prompt/build/target.o" | awk '{print $1}')"
objdiff_candidate_sha="$(sha256sum "$objdiff_report_prompt/build/candidate.o" | awk '{print $1}')"
objdiff_target_size="$(stat -c %s "$objdiff_report_prompt/build/target.o")"
objdiff_candidate_size="$(stat -c %s "$objdiff_report_prompt/build/candidate.o")"
jq -n \
  --arg prompt_name "sample_fn" \
  --arg function_name "sample_fn" \
  --arg target_object "$objdiff_report_prompt/build/target.o" \
  --arg candidate_object "$objdiff_report_prompt/build/candidate.o" \
  --arg target_sha "$objdiff_target_sha" \
  --arg candidate_sha "$objdiff_candidate_sha" \
  --argjson target_size "$objdiff_target_size" \
  --argjson candidate_size "$objdiff_candidate_size" \
  '{
    schema: "mizuchi.build-and-verify.v1",
    status: "matched",
    method: "objdiff",
    prompt: $prompt_name,
    function_name: $function_name,
    candidate_source: null,
    target_object: $target_object,
    candidate_object: $candidate_object,
    target_sha256: $target_sha,
    candidate_sha256: $candidate_sha,
    target_size: $target_size,
    candidate_size: $candidate_size,
    compile_log: null,
    verify_log: null,
    byte_identical: false
  }' >"$objdiff_report_prompt/build/build-and-verify.json"
expect_valid "$objdiff_report_root"

phase_root="$TMP_DIR/phase_reports"
phase_prompt="$phase_root/sample_fn"
write_prompt "$phase_prompt" pending
mkdir -p "$phase_prompt/build"
jq -n \
  --arg prompt_dir "$phase_prompt" \
  '{
    schema: "mizuchi.programmatic-phase.v1",
    status: "blocked",
    prompt: "sample_fn",
    promptDir: $prompt_dir,
    exitCode: 3,
    stages: ["blocked"],
    matchedStage: null,
    reason: "fixture blocked",
    verifierReport: null
  }' >"$phase_prompt/build/programmatic-phase.json"
jq -n \
  --arg prompt_dir "$phase_prompt" \
  '{
    schema: "mizuchi.ai-phase.v1",
    status: "blocked",
    prompt: "sample_fn",
    promptDir: $prompt_dir,
    runner: null,
    reason: "fixture blocked",
    config: "mizuchi.yaml",
    image: "docker.io/bolabaden/mizuchi:latest",
    anthropicApiKeyPresent: false,
    exitCode: 3
  }' >"$phase_prompt/build/ai-phase.json"
expect_valid "$phase_root"

ai_matched_root="$TMP_DIR/ai_matched"
ai_matched_prompt="$ai_matched_root/sample_fn"
write_prompt "$ai_matched_prompt" pending
mkdir -p "$ai_matched_prompt/build"
jq -n \
  --arg prompt_dir "$ai_matched_prompt" \
  '{
    schema: "mizuchi.ai-phase.v1",
    status: "matched",
    prompt: "sample_fn",
    promptDir: $prompt_dir,
    runner: "native-mizuchi",
    reason: "mizuchi run completed with objdiff 0",
    config: "mizuchi.yaml",
    image: "docker.io/bolabaden/mizuchi:latest",
    anthropicApiKeyPresent: false,
    exitCode: 0
  }' >"$ai_matched_prompt/build/ai-phase.json"
expect_valid "$ai_matched_root"

bad_ai_root="$TMP_DIR/bad_ai"
bad_ai_prompt="$bad_ai_root/sample_fn"
write_prompt "$bad_ai_prompt" pending
mkdir -p "$bad_ai_prompt/build"
jq -n \
  --arg prompt_dir "$bad_ai_prompt" \
  '{
    schema: "mizuchi.ai-phase.v1",
    status: "failed",
    prompt: "sample_fn",
    promptDir: $prompt_dir,
    runner: "native-mizuchi",
    reason: "mizuchi run failed",
    config: "mizuchi.yaml",
    image: "docker.io/bolabaden/mizuchi:latest",
    anthropicApiKeyPresent: false,
    exitCode: 0
  }' >"$bad_ai_prompt/build/ai-phase.json"
expect_invalid "$bad_ai_root" "failed ai phase has inconsistent outcome"

bad_phase_root="$TMP_DIR/bad_phase"
bad_phase_prompt="$bad_phase_root/sample_fn"
write_prompt "$bad_phase_prompt" pending
mkdir -p "$bad_phase_prompt/build"
jq -n \
  --arg prompt_dir "$bad_phase_prompt" \
  '{
    schema: "mizuchi.programmatic-phase.v1",
    status: "no-match",
    prompt: "sample_fn",
    promptDir: $prompt_dir,
    exitCode: 1,
    stages: ["candidate:mismatched"],
    matchedStage: "candidate",
    reason: "fixture mismatch",
    verifierReport: null
  }' >"$bad_phase_prompt/build/programmatic-phase.json"
expect_invalid "$bad_phase_root" "no-match programmatic phase has inconsistent outcome"

integrated_root="$TMP_DIR/integrated"
integrated_prompt="$integrated_root/sample_fn"
write_prompt "$integrated_prompt" integrated
mkdir -p "$integrated_prompt/build" "$TMP_DIR/src"
source_out="$TMP_DIR/src/sample_fn.c"
receipt="$integrated_prompt/build/integration-receipt.json"
cat >"$source_out" <<'C'
int sample_fn(void) {
  return 0;
}
C
write_matched_verifier_report "$integrated_prompt"
candidate_source="$integrated_prompt/candidate.c"
cat >"$candidate_source" <<'C'
int sample_fn(void) {
  return 0;
}
C
candidate_sha="$(sha256sum "$candidate_source" | awk '{print $1}')"
source_sha="$(sha256sum "$source_out" | awk '{print $1}')"
jq -n \
  --arg candidate_source "$candidate_source" \
  --arg source_out "$source_out" \
  --arg candidate_sha "$candidate_sha" \
  --arg source_sha "$source_sha" \
  --arg verifier_report "$integrated_prompt/build/build-and-verify.json" \
  '{
    schema: "mizuchi.integration-receipt.v1",
    status: "integrated",
    candidateSource: $candidate_source,
    sourceOut: $source_out,
    candidateSourceSha256: $candidate_sha,
    sourceOutSha256: $source_sha,
    verifierReport: $verifier_report
  }' >"$receipt"
cat >>"$integrated_prompt/case.yaml" <<YAML
integratedSourcePath: $source_out
integrationReceiptPath: $receipt
integratedAt: 2026-06-28T00:00:00Z
YAML
expect_valid "$integrated_root"

stale_integrated_root="$TMP_DIR/stale_integrated"
stale_integrated_prompt="$stale_integrated_root/sample_fn"
write_prompt "$stale_integrated_prompt" integrated
mkdir -p "$stale_integrated_prompt/build" "$TMP_DIR/stale-src"
stale_source_out="$TMP_DIR/stale-src/sample_fn.c"
stale_receipt="$stale_integrated_prompt/build/integration-receipt.json"
cat >"$stale_source_out" <<'C'
int sample_fn(void) {
  return 0;
}
C
write_matched_verifier_report "$stale_integrated_prompt"
stale_candidate_source="$stale_integrated_prompt/candidate.c"
cp "$stale_source_out" "$stale_candidate_source"
stale_candidate_sha="$(sha256sum "$stale_candidate_source" | awk '{print $1}')"
stale_source_sha="$(sha256sum "$stale_source_out" | awk '{print $1}')"
jq -n \
  --arg candidate_source "$stale_candidate_source" \
  --arg source_out "$stale_source_out" \
  --arg candidate_sha "$stale_candidate_sha" \
  --arg source_sha "$stale_source_sha" \
  --arg verifier_report "$stale_integrated_prompt/build/build-and-verify.json" \
  '{
    schema: "mizuchi.integration-receipt.v1",
    status: "integrated",
    candidateSource: $candidate_source,
    sourceOut: $source_out,
    candidateSourceSha256: $candidate_sha,
    sourceOutSha256: $source_sha,
    verifierReport: $verifier_report
  }' >"$stale_receipt"
cat >>"$stale_integrated_prompt/case.yaml" <<YAML
integratedSourcePath: $stale_source_out
integrationReceiptPath: $stale_receipt
integratedAt: 2026-06-28T00:00:00Z
YAML
printf 'mutated integration output\n' >"$stale_source_out"
expect_invalid "$stale_integrated_root" "integration receipt source output hash mismatch"

bad_integrated_root="$TMP_DIR/bad_integrated"
bad_integrated_prompt="$bad_integrated_root/sample_fn"
write_prompt "$bad_integrated_prompt" integrated
cat >>"$bad_integrated_prompt/case.yaml" <<'YAML'
integratedSourcePath: /tmp/mizuchi-missing-source.c
integrationReceiptPath: /tmp/mizuchi-missing-receipt.json
integratedAt: 2026-06-28T00:00:00Z
YAML
expect_invalid "$bad_integrated_root" "integratedSourcePath does not exist"

decomp_root="$TMP_DIR/decomp_receipt"
decomp_prompt="$decomp_root/sample_fn"
write_prompt "$decomp_prompt" pending
mkdir -p "$decomp_prompt/build"
write_matched_verifier_report "$decomp_prompt"
jq -n \
  --arg prompt_dir "$decomp_prompt" \
  --arg verifier_report "$decomp_prompt/build/build-and-verify.json" \
  '{
    schema: "mizuchi.programmatic-phase.v1",
    status: "matched",
    prompt: "sample_fn",
    promptDir: $prompt_dir,
    exitCode: 0,
    stages: ["candidate:matched"],
    matchedStage: "candidate",
    reason: null,
    verifierReport: $verifier_report
  }' >"$decomp_prompt/build/programmatic-phase.json"
jq -n \
  --arg prompt_dir "$decomp_prompt" \
  --arg programmatic_report "$decomp_prompt/build/programmatic-phase.json" \
  '{
    schema: "mizuchi.decomp-function.v1",
    status: "matched",
    prompt: "sample_fn",
    promptDir: $prompt_dir,
    exitCode: 0,
    terminalPhase: "programmatic",
    reason: null,
    programmaticReport: $programmatic_report,
    programmaticStatus: "matched",
    matchedStage: "candidate",
    aiReport: null,
    aiStatus: null,
    aiRunner: null
  }' >"$decomp_prompt/build/decomp-function.json"
expect_valid "$decomp_root"

stale_ai_root="$TMP_DIR/stale_ai_receipt"
stale_ai_prompt="$stale_ai_root/sample_fn"
write_prompt "$stale_ai_prompt" pending
mkdir -p "$stale_ai_prompt/build"
write_matched_verifier_report "$stale_ai_prompt"
jq -n \
  --arg prompt_dir "$stale_ai_prompt" \
  --arg verifier_report "$stale_ai_prompt/build/build-and-verify.json" \
  '{
    schema: "mizuchi.programmatic-phase.v1",
    status: "matched",
    prompt: "sample_fn",
    promptDir: $prompt_dir,
    exitCode: 0,
    stages: ["candidate:matched"],
    matchedStage: "candidate",
    reason: null,
    verifierReport: $verifier_report
  }' >"$stale_ai_prompt/build/programmatic-phase.json"
jq -n '{schema: "mizuchi.ai-phase.v1", status: "blocked", runner: null}' >"$stale_ai_prompt/build/ai-phase.json"
jq -n \
  --arg prompt_dir "$stale_ai_prompt" \
  --arg programmatic_report "$stale_ai_prompt/build/programmatic-phase.json" \
  --arg ai_report "$stale_ai_prompt/build/ai-phase.json" \
  '{
    schema: "mizuchi.decomp-function.v1",
    status: "matched",
    prompt: "sample_fn",
    promptDir: $prompt_dir,
    exitCode: 0,
    terminalPhase: "programmatic",
    reason: null,
    programmaticReport: $programmatic_report,
    programmaticStatus: "matched",
    matchedStage: "candidate",
    aiReport: $ai_report,
    aiStatus: "blocked",
    aiRunner: null
  }' >"$stale_ai_prompt/build/decomp-function.json"
expect_invalid "$stale_ai_root" "programmatic-terminal decomp-function receipt must not link ai report"

bad_verifier_root="$TMP_DIR/bad_verifier"
bad_verifier_prompt="$bad_verifier_root/sample_fn"
write_prompt "$bad_verifier_prompt" pending
mkdir -p "$bad_verifier_prompt/build"
write_matched_verifier_report "$bad_verifier_prompt"
rm -f "$bad_verifier_prompt/build/target.o"
jq -n \
  --arg prompt_dir "$bad_verifier_prompt" \
  --arg verifier_report "$bad_verifier_prompt/build/build-and-verify.json" \
  '{
    schema: "mizuchi.programmatic-phase.v1",
    status: "matched",
    prompt: "sample_fn",
    promptDir: $prompt_dir,
    exitCode: 0,
    stages: ["candidate:matched"],
    matchedStage: "candidate",
    reason: null,
    verifierReport: $verifier_report
  }' >"$bad_verifier_prompt/build/programmatic-phase.json"
jq -n \
  --arg prompt_dir "$bad_verifier_prompt" \
  --arg programmatic_report "$bad_verifier_prompt/build/programmatic-phase.json" \
  '{
    schema: "mizuchi.decomp-function.v1",
    status: "matched",
    prompt: "sample_fn",
    promptDir: $prompt_dir,
    exitCode: 0,
    terminalPhase: "programmatic",
    reason: null,
    programmaticReport: $programmatic_report,
    programmaticStatus: "matched",
    matchedStage: "candidate",
    aiReport: null,
    aiStatus: null,
    aiRunner: null
  }' >"$bad_verifier_prompt/build/decomp-function.json"
expect_invalid "$bad_verifier_root" "verifier report target object missing"

stale_hash_root="$TMP_DIR/stale_hash"
stale_hash_prompt="$stale_hash_root/sample_fn"
write_prompt "$stale_hash_prompt" pending
mkdir -p "$stale_hash_prompt/build"
write_matched_verifier_report "$stale_hash_prompt"
printf 'mutated-object\n' >"$stale_hash_prompt/build/candidate.o"
jq -n \
  --arg prompt_dir "$stale_hash_prompt" \
  --arg verifier_report "$stale_hash_prompt/build/build-and-verify.json" \
  '{
    schema: "mizuchi.programmatic-phase.v1",
    status: "matched",
    prompt: "sample_fn",
    promptDir: $prompt_dir,
    exitCode: 0,
    stages: ["candidate:matched"],
    matchedStage: "candidate",
    reason: null,
    verifierReport: $verifier_report
  }' >"$stale_hash_prompt/build/programmatic-phase.json"
jq -n \
  --arg prompt_dir "$stale_hash_prompt" \
  --arg programmatic_report "$stale_hash_prompt/build/programmatic-phase.json" \
  '{
    schema: "mizuchi.decomp-function.v1",
    status: "matched",
    prompt: "sample_fn",
    promptDir: $prompt_dir,
    exitCode: 0,
    terminalPhase: "programmatic",
    reason: null,
    programmaticReport: $programmatic_report,
    programmaticStatus: "matched",
    matchedStage: "candidate",
    aiReport: null,
    aiStatus: null,
    aiRunner: null
  }' >"$stale_hash_prompt/build/decomp-function.json"
expect_invalid "$stale_hash_root" "verifier report candidate hash mismatch"

echo "ok"
