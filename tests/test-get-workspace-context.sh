#!/usr/bin/env bash

# Test suite for get-workspace-context.sh

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script_path="$root_dir/scripts/get-workspace-context.sh"

tests_run=0
tests_passed=0
tests_failed=0

run_test() {
  local test_name="$1"
  local test_fn="$2"
  
  ((tests_run++))
  printf "Test: %-50s " "$test_name"
  
  if $test_fn > /dev/null 2>&1; then
    echo "PASS"
    ((tests_passed++))
  else
    echo "FAIL"
    ((tests_failed++))
  fi
}

test_script_exists() {
  [[ -f "$script_path" && -x "$script_path" ]]
}

test_returns_valid_json() {
  local output
  output=$("$script_path" 2>&1)
  echo "$output" | jq . > /dev/null 2>&1
}

test_has_required_fields() {
  local output
  output=$("$script_path" 2>&1)
  
  for field in prompt_queue build_artifacts active_branches workspace_metrics readiness_metrics; do
    if ! echo "$output" | jq ".${field}" > /dev/null 2>&1; then
      return 1
    fi
  done
  return 0
}

test_readiness_metrics_structure() {
  local output
  output=$("$script_path" 2>&1)
  local metrics=$(echo "$output" | jq '.readiness_metrics')

  for field in status total ready notReady blockersTotal warningsTotal blockerSummary; do
    if ! echo "$metrics" | jq ".${field}" > /dev/null 2>&1; then
      return 1
    fi
  done
  return 0
}

test_prompt_queue_is_array() {
  local output
  output=$("$script_path" 2>&1)
  [[ "$(echo "$output" | jq -r '.prompt_queue | type')" == "array" ]]
}

test_workspace_metrics_structure() {
  local output
  output=$("$script_path" 2>&1)
  local metrics=$(echo "$output" | jq '.workspace_metrics')
  
  for field in total_prompts matched integrated blocked match_rate_percent integration_rate_percent; do
    if ! echo "$metrics" | jq ".${field}" > /dev/null 2>&1; then
      return 1
    fi
  done
  return 0
}

test_active_branches_structure() {
  local output
  output=$("$script_path" 2>&1)
  local branches=$(echo "$output" | jq '.active_branches')
  
  for field in current_branch remote_count unpushed_commits; do
    if ! echo "$branches" | jq ".${field}" > /dev/null 2>&1; then
      return 1
    fi
  done
  return 0
}

test_prompt_count_accuracy() {
  local output
  output=$("$script_path" 2>&1)
  local reported_count=$(echo "$output" | jq '.workspace_metrics.total_prompts')
  
  local actual_count=0
  local prompts_dir="$root_dir/prompts"
  
  if [[ -d "$prompts_dir" ]]; then
    for prompt_dir in "$prompts_dir"/*; do
      if [[ -d "$prompt_dir" ]]; then
        local name=$(basename "$prompt_dir")
        if [[ "$name" != "_template" ]]; then
          ((actual_count++))
        fi
      fi
    done
  fi
  
  [[ "$reported_count" -eq "$actual_count" ]]
}

test_performance() {
  local start_time=$(date +%s%N)
  "$script_path" > /dev/null 2>&1
  local end_time=$(date +%s%N)
  local elapsed_ms=$(( (end_time - start_time) / 1000000 ))
  
  [[ $elapsed_ms -lt 6000 ]]
}

test_git_info_populated() {
  local output
  output=$("$script_path" 2>&1)
  local branch=$(echo "$output" | jq -r '.active_branches.current_branch')
  
  [[ -n "$branch" && "$branch" != "unknown" ]]
}

test_ready_prompt_metadata() {
  local output
  output=$("$script_path" 2>&1)

  echo "$output" | jq -e '
    .readiness_metrics.status == "ready" and
    (.prompt_queue[]
      | select(.name == "fun_00148020")
      | .status == "matched"
        and .readiness_status == "ready"
        and (.readiness_blockers | length) == 0)
  ' >/dev/null
}

test_integrated_prompt_metadata() {
  local tmpdir
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' RETURN

  local prompt="$tmpdir/integrated_fn"
  mkdir -p "$prompt/build"
  cat >"$prompt/settings.yaml" <<'YAML'
functionName: integrated_fn
targetObjectPath: prompt:/build/target.o
asm: |
  integrated_fn:
      ret
YAML
  cat >"$prompt/case.yaml" <<'YAML'
caseId: integrated_fn
functionName: integrated_fn
targetObjectPath: prompt:/build/target.o
status: integrated
integratedSourcePath: /tmp/reconkit-integrated/integrated_fn.c
integrationReceiptPath: /tmp/reconkit-prompts/integrated_fn/build/integration-receipt.json
integratedAt: 2026-06-28T00:00:00Z
YAML
  touch "$prompt/prompt.md"

  local output
  output="$(RECONKIT_PROMPTS_DIR="$tmpdir" "$script_path" 2>&1)"
  echo "$output" | jq -e '
    .workspace_metrics.total_prompts == 1 and
    .workspace_metrics.integrated == 1 and
    .workspace_metrics.integration_rate_percent == 100 and
    (.prompt_queue[]
      | select(.name == "integrated_fn")
      | .status == "integrated"
        and .function_name == "integrated_fn"
        and .integrated_source_path == "/tmp/reconkit-integrated/integrated_fn.c"
        and .integration_receipt_path == "/tmp/reconkit-prompts/integrated_fn/build/integration-receipt.json"
        and .integrated_at == "2026-06-28T00:00:00Z")
  ' >/dev/null
}

test_programmatic_report_build_artifact() {
  local tmpdir
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' RETURN

  local prompt="$tmpdir/report_fn"
  mkdir -p "$prompt/build"
  cat >"$prompt/settings.yaml" <<'YAML'
functionName: report_fn
targetObjectPath: prompt:/build/target.o
asm: |
  report_fn:
      ret
YAML
  cat >"$prompt/case.yaml" <<'YAML'
caseId: report_fn
functionName: report_fn
targetObjectPath: prompt:/build/target.o
status: matched
YAML
  cat >"$prompt/build/programmatic-phase.json" <<'JSON'
{
  "schema": "reconkit.programmatic-phase.v1",
  "status": "matched",
  "matchedStage": "candidate"
}
JSON

  local output
  output="$(RECONKIT_PROMPTS_DIR="$tmpdir" "$script_path" 2>&1)"
  echo "$output" | jq -e '
    .build_artifacts[]
    | select(.prompt == "report_fn")
    | .programmatic_status == "matched" and .matched_stage == "candidate"
  ' >/dev/null
}

test_ai_phase_report_build_artifact() {
  local tmpdir
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' RETURN

  local prompt="$tmpdir/ai_fn"
  mkdir -p "$prompt/build"
  cat >"$prompt/settings.yaml" <<'YAML'
functionName: ai_fn
targetObjectPath: prompt:/build/target.o
asm: |
  ai_fn:
      ret
YAML
  cat >"$prompt/case.yaml" <<'YAML'
caseId: ai_fn
functionName: ai_fn
targetObjectPath: prompt:/build/target.o
status: pending
YAML
  cat >"$prompt/build/ai-phase.json" <<'JSON'
{
  "schema": "reconkit.ai-phase.v1",
  "status": "manual-required",
  "runner": "cursor-native"
}
JSON

  local output
  output="$(RECONKIT_PROMPTS_DIR="$tmpdir" "$script_path" 2>&1)"
  echo "$output" | jq -e '
    .build_artifacts[]
    | select(.prompt == "ai_fn")
    | .ai_status == "manual-required" and .ai_runner == "cursor-native"
  ' >/dev/null
}

echo "Running tests for get-workspace-context.sh"
echo "==========================================="
echo

run_test "Script exists and is executable" "test_script_exists"
run_test "Returns valid JSON" "test_returns_valid_json"
run_test "Has all required top-level fields" "test_has_required_fields"
run_test "prompt_queue is array" "test_prompt_queue_is_array"
run_test "workspace_metrics structure" "test_workspace_metrics_structure"
run_test "readiness_metrics structure" "test_readiness_metrics_structure"
run_test "active_branches structure" "test_active_branches_structure"
run_test "Prompt count matches filesystem" "test_prompt_count_accuracy"
run_test "Performance: <6 seconds with readiness checks" "test_performance"
run_test "Git info is populated" "test_git_info_populated"
run_test "Ready prompt metadata is canonical" "test_ready_prompt_metadata"
run_test "Integrated prompt metadata is surfaced" "test_integrated_prompt_metadata"
run_test "Programmatic report is surfaced in build artifacts" "test_programmatic_report_build_artifact"
run_test "AI phase report is surfaced in build artifacts" "test_ai_phase_report_build_artifact"

echo
echo "==========================================="
echo "Tests run: $tests_run, Passed: $tests_passed, Failed: $tests_failed"

if [[ $tests_failed -gt 0 ]]; then
  exit 1
fi
