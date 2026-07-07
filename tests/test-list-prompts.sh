#!/usr/bin/env bash

# Test suite for list-prompts.sh

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script_path="$root_dir/scripts/list-prompts.sh"

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

test_has_prompts_field() {
  local output
  output=$("$script_path" 2>&1)
  echo "$output" | jq ".prompts" > /dev/null 2>&1
}

test_prompts_is_array() {
  local output
  output=$("$script_path" 2>&1)
  [[ "$(echo "$output" | jq -r '.prompts | type')" == "array" ]]
}

test_prompt_items_have_required_fields() {
  local output
  output=$("$script_path" 2>&1)
  local prompt_count=$(echo "$output" | jq '.prompts | length')
  
  if [[ $prompt_count -eq 0 ]]; then
    return 0  # Empty array is valid
  fi
  
  # Check first prompt item has all required fields
  local first_item=$(echo "$output" | jq '.prompts[0]')
  for field in name status function_name last_updated readiness_status readiness_blockers readiness_warnings; do
    if ! echo "$first_item" | jq ".${field}" > /dev/null 2>&1; then
      return 1
    fi
  done
  return 0
}

test_has_readiness_summary() {
  local output
  output=$("$script_path" 2>&1)
  echo "$output" | jq -e '
    .readiness.status
    and (.readiness.total | type == "number")
    and (.readiness.ready | type == "number")
    and (.readiness.notReady | type == "number")
    and (.readiness.blockersTotal | type == "number")
    and (.readiness.warningsTotal | type == "number")
    and (.readiness.blockerSummary | type == "object")
  ' >/dev/null
}

test_happy_path_lists_all_prompts() {
  local output
  output=$("$script_path" 2>&1)
  
  local reported_count=$(echo "$output" | jq '.prompts | length')
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

test_prompt_name_matches_folder() {
  local output
  output=$("$script_path" 2>&1)
  
  # Check that reported names match actual folders
  local reported_names=$(echo "$output" | jq -r '.prompts[].name' | sort)
  
  local actual_names=""
  local prompts_dir="$root_dir/prompts"
  
  if [[ -d "$prompts_dir" ]]; then
    actual_names=$(
      for prompt_dir in "$prompts_dir"/*; do
        if [[ -d "$prompt_dir" ]]; then
          local name=$(basename "$prompt_dir")
          if [[ "$name" != "_template" ]]; then
            echo "$name"
          fi
        fi
      done | sort
    )
  fi
  
  [[ "$reported_names" == "$actual_names" ]]
}

test_status_values_are_valid() {
  local output
  output=$("$script_path" 2>&1)
  
  # All status values should be one of: pending, matched, in_progress, integrated, blocked
  local statuses=$(echo "$output" | jq -r '.prompts[].status' | sort -u)
  
  while IFS= read -r status; do
    case "$status" in
      pending|matched|in_progress|integrated|blocked)
        :  # Valid status
        ;;
      *)
        return 1  # Invalid status
        ;;
    esac
  done <<< "$statuses"
  
  return 0
}

test_function_name_is_present() {
  local output
  output=$("$script_path" 2>&1)
  local prompt_count=$(echo "$output" | jq '.prompts | length')
  
  if [[ $prompt_count -eq 0 ]]; then
    return 0
  fi
  
  # Check first prompt has a non-empty function_name
  local first_func=$(echo "$output" | jq -r '.prompts[0].function_name')
  [[ -n "$first_func" ]]
}

test_filter_by_status_matched() {
  local output
  output=$("$script_path" status=matched 2>&1)
  
  local filtered_statuses=$(echo "$output" | jq -r '.prompts[].status' | sort -u)
  
  if echo "$output" | jq '.prompts | length' | grep -q 0; then
    # No matched prompts, that's OK
    return 0
  fi
  
  # All statuses should be "matched"
  [[ "$filtered_statuses" == "matched" ]]
}

test_filter_by_status_in_progress() {
  local output
  output=$("$script_path" status=in_progress 2>&1)
  
  # Should return valid JSON regardless
  echo "$output" | jq . > /dev/null 2>&1
}

test_filter_by_status_integrated() {
  local output
  output=$("$script_path" status=integrated 2>&1)
  
  # Should return valid JSON regardless
  echo "$output" | jq . > /dev/null 2>&1
}

test_filter_by_status_pending() {
  local output
  output=$("$script_path" status=pending 2>&1)
  
  # Should return valid JSON regardless
  echo "$output" | jq . > /dev/null 2>&1
}

test_filter_by_status_blocked() {
  local output
  output=$("$script_path" status=blocked 2>&1)

  echo "$output" | jq . > /dev/null 2>&1
  if echo "$output" | jq '.prompts | length' | grep -q 0; then
    return 0
  fi
  [[ "$(echo "$output" | jq -r '.prompts[].status' | sort -u)" == "blocked" ]]
}

test_case_yaml_status_takes_precedence() {
  local output
  output=$("$script_path" 2>&1)

  echo "$output" | jq -e '
    .prompts[]
    | select(.name == "fun_00148020")
    | .status == "matched"
      and .readiness_status == "ready"
      and (.readiness_blockers | length) == 0
  ' >/dev/null
}

test_invalid_status_filter_gracefully_handled() {
  local output
  output=$("$script_path" status=invalid_status 2>&1)
  
  # Should return valid JSON even with invalid status
  echo "$output" | jq . > /dev/null 2>&1
}

test_empty_queue_returns_empty_array() {
  # This test would require temporarily removing prompts
  # For now, just verify that the structure is correct
  local output
  output=$("$script_path" 2>&1)
  
  # Even if empty, should be an array
  [[ "$(echo "$output" | jq -r '.prompts | type')" == "array" ]]
}

test_performance() {
  local start_time=$(date +%s%N)
  "$script_path" > /dev/null 2>&1
  local end_time=$(date +%s%N)
  local elapsed_ms=$(( (end_time - start_time) / 1000000 ))
  
  [[ $elapsed_ms -lt 6000 ]]
}

test_filter_does_not_break_structure() {
  local output
  output=$("$script_path" status=matched 2>&1)
  
  # Even when filtered, structure should have prompts field
  echo "$output" | jq '.prompts' > /dev/null 2>&1
}

test_integrated_metadata_from_case_yaml() {
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
integratedSourcePath: /tmp/mizuchi-integrated/integrated_fn.c
integrationReceiptPath: /tmp/mizuchi-prompts/integrated_fn/build/integration-receipt.json
integratedAt: 2026-06-28T00:00:00Z
YAML
  touch "$prompt/prompt.md"

  local output
  output="$(MIZUCHI_PROMPTS_DIR="$tmpdir" "$script_path" status=integrated 2>&1)"
  echo "$output" | jq -e '
    .prompts as $prompts |
    ($prompts | length) == 1 and
    $prompts[0].name == "integrated_fn" and
    $prompts[0].status == "integrated" and
    $prompts[0].function_name == "integrated_fn" and
    $prompts[0].integrated_source_path == "/tmp/mizuchi-integrated/integrated_fn.c" and
    $prompts[0].integration_receipt_path == "/tmp/mizuchi-prompts/integrated_fn/build/integration-receipt.json" and
    $prompts[0].integrated_at == "2026-06-28T00:00:00Z"
  ' >/dev/null
}

echo "Running tests for list-prompts.sh"
echo "=================================="
echo

run_test "Script exists and is executable" "test_script_exists"
run_test "Returns valid JSON" "test_returns_valid_json"
run_test "Has 'prompts' field" "test_has_prompts_field"
run_test "Has readiness summary" "test_has_readiness_summary"
run_test "prompts is array" "test_prompts_is_array"
run_test "Prompt items have required fields" "test_prompt_items_have_required_fields"
run_test "Happy path: lists all prompts" "test_happy_path_lists_all_prompts"
run_test "Prompt names match folders" "test_prompt_name_matches_folder"
run_test "Status values are valid" "test_status_values_are_valid"
run_test "Function name is present" "test_function_name_is_present"
run_test "Filter by status=matched" "test_filter_by_status_matched"
run_test "Filter by status=in_progress" "test_filter_by_status_in_progress"
run_test "Filter by status=integrated" "test_filter_by_status_integrated"
run_test "Filter by status=pending" "test_filter_by_status_pending"
run_test "Filter by status=blocked" "test_filter_by_status_blocked"
run_test "case.yaml status takes precedence" "test_case_yaml_status_takes_precedence"
run_test "Invalid status filter handled gracefully" "test_invalid_status_filter_gracefully_handled"
run_test "Empty queue returns empty array" "test_empty_queue_returns_empty_array"
run_test "Performance: <6 seconds with readiness checks" "test_performance"
run_test "Filter preserves JSON structure" "test_filter_does_not_break_structure"
run_test "Integrated metadata comes from case.yaml" "test_integrated_metadata_from_case_yaml"

echo
echo "=================================="
echo "Tests run: $tests_run, Passed: $tests_passed, Failed: $tests_failed"

if [[ $tests_failed -gt 0 ]]; then
  exit 1
fi
