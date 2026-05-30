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
  
  for field in prompt_queue ghidra_status build_artifacts active_branches workspace_metrics; do
    if ! echo "$output" | jq ".${field}" > /dev/null 2>&1; then
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
  
  for field in total_prompts matched integrated match_rate_percent integration_rate_percent; do
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

test_ghidra_status_structure() {
  local output
  output=$("$script_path" 2>&1)
  local status=$(echo "$output" | jq '.ghidra_status')
  
  for field in connected_servers loaded_programs analysis_state; do
    if ! echo "$status" | jq ".${field}" > /dev/null 2>&1; then
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
          actual_count=$((actual_count + 1))
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
  
  [[ $elapsed_ms -lt 2000 ]]
}

test_git_info_populated() {
  local output
  output=$("$script_path" 2>&1)
  local branch=$(echo "$output" | jq -r '.active_branches.current_branch')
  
  [[ -n "$branch" ]]
}

echo "Running tests for get-workspace-context.sh"
echo "==========================================="
echo

run_test "Script exists and is executable" "test_script_exists"
run_test "Returns valid JSON" "test_returns_valid_json"
run_test "Has all required top-level fields" "test_has_required_fields"
run_test "prompt_queue is array" "test_prompt_queue_is_array"
run_test "workspace_metrics structure" "test_workspace_metrics_structure"
run_test "active_branches structure" "test_active_branches_structure"
run_test "ghidra_status structure" "test_ghidra_status_structure"
run_test "Prompt count matches filesystem" "test_prompt_count_accuracy"
run_test "Performance: <2 seconds" "test_performance"
run_test "Git info is populated" "test_git_info_populated"

echo
echo "==========================================="
echo "Tests run: $tests_run, Passed: $tests_passed, Failed: $tests_failed"

if [[ $tests_failed -gt 0 ]]; then
  exit 1
fi
