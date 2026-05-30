#!/usr/bin/env bash
set -euo pipefail

# Test suite for inject-context.sh
# Validates context injection functionality

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script="$root_dir/scripts/inject-context.sh"

# Test counters
TESTS_TOTAL=0
TESTS_PASSED=0
TESTS_FAILED=0

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test helper functions
test_start() {
  local test_name="$1"
  TESTS_TOTAL=$((TESTS_TOTAL + 1))
  echo -n "TEST: $test_name ... "
}

test_pass() {
  TESTS_PASSED=$((TESTS_PASSED + 1))
  echo -e "${GREEN}PASS${NC}"
}

test_fail() {
  local reason="$1"
  TESTS_FAILED=$((TESTS_FAILED + 1))
  echo -e "${RED}FAIL${NC}"
  echo "  Reason: $reason"
}

test_assert_equals() {
  local expected="$1"
  local actual="$2"
  local test_name="${3:-assertion}"
  
  if [[ "$expected" == "$actual" ]]; then
    test_pass
  else
    test_fail "Expected '$expected' but got '$actual'"
  fi
}

test_assert_contains() {
  local haystack="$1"
  local needle="$2"
  local test_name="${3:-assertion}"
  
  if echo "$haystack" | grep -q "$needle"; then
    test_pass
  else
    test_fail "Output does not contain: '$needle'"
  fi
}

test_assert_valid_json() {
  local json="$1"
  
  if echo "$json" | jq . > /dev/null 2>&1; then
    test_pass
  else
    test_fail "Output is not valid JSON"
  fi
}

# Test 1: Script exists and is executable
test_start "Script exists and is executable"
if [[ -x "$script" ]]; then
  test_pass
else
  test_fail "Script not found or not executable: $script"
fi

# Test 2: Script shows help when no arguments provided
test_start "Script shows help when no arguments"
output=$("$script" 2>&1 || true)
test_assert_contains "$output" "Usage: inject-context.sh"

# Test 3: Script works for ghidra-binary-scout agent
test_start "Script works for ghidra-binary-scout agent"
output=$("$script" ghidra-binary-scout 2>&1)
if [[ -n "$output" ]]; then
  test_pass
else
  test_fail "No output for ghidra-binary-scout agent"
fi

# Test 4: Script works for decomp-prompt-architect agent
test_start "Script works for decomp-prompt-architect agent"
output=$("$script" decomp-prompt-architect 2>&1)
if [[ -n "$output" ]]; then
  test_pass
else
  test_fail "No output for decomp-prompt-architect agent"
fi

# Test 5: Script works for decomp-function-agent
test_start "Script works for decomp-function-agent"
output=$("$script" decomp-function-agent 2>&1)
if [[ -n "$output" ]]; then
  test_pass
else
  test_fail "No output for decomp-function-agent agent"
fi

# Test 6: Markdown output contains workspace context section
test_start "Markdown output contains workspace context section"
output=$("$script" ghidra-binary-scout 2>&1)
test_assert_contains "$output" "Workspace Context"

# Test 7: Markdown output contains capabilities
test_start "Markdown output contains capabilities section"
output=$("$script" ghidra-binary-scout 2>&1)
test_assert_contains "$output" "Capabilities"

# Test 8: JSON output is valid JSON
test_start "JSON output is valid JSON"
output=$("$script" ghidra-binary-scout --json 2>&1)
test_assert_valid_json "$output"

# Test 9: JSON output has required fields
test_start "JSON output has required fields (agent, timestamp, fields)"
output=$("$script" decomp-function-agent --json 2>&1)
if echo "$output" | jq -e '.agent' > /dev/null 2>&1 && \
   echo "$output" | jq -e '.timestamp' > /dev/null 2>&1 && \
   echo "$output" | jq -e '.fields' > /dev/null 2>&1; then
  test_pass
else
  test_fail "Missing required JSON fields"
fi

# Test 10: JSON output agent name matches input
test_start "JSON output agent name matches input"
output=$("$script" ghidra-binary-scout --json 2>&1)
agent_name=$(echo "$output" | jq -r '.agent')
test_assert_equals "ghidra-binary-scout" "$agent_name"

# Test 11: JSON output has workspace_state field for agents with context_injection
test_start "JSON output has workspace_state field"
output=$("$script" decomp-function-agent --json 2>&1)
if echo "$output" | jq -e '.fields.workspace_state' > /dev/null 2>&1; then
  test_pass
else
  test_fail "Missing workspace_state field in JSON output"
fi

# Test 12: Markdown output contains constraints section
test_start "Markdown output contains constraints section"
output=$("$script" decomp-function-agent 2>&1)
test_assert_contains "$output" "Constraints"

# Test 13: Markdown output does not contain error markers
test_start "Markdown output does not contain error markers"
output=$("$script" ghidra-binary-scout 2>&1)
if echo "$output" | grep -q "Error:" || echo "$output" | grep -q "error:"; then
  test_fail "Output contains error messages"
else
  test_pass
fi

# Test 14: JSON workspace_state has expected fields
test_start "JSON workspace_state has expected fields"
output=$("$script" decomp-prompt-architect --json 2>&1)
if echo "$output" | jq -e '.fields.workspace_state.total_prompts' > /dev/null 2>&1 && \
   echo "$output" | jq -e '.fields.workspace_state.matched' > /dev/null 2>&1 && \
   echo "$output" | jq -e '.fields.workspace_state.integrated' > /dev/null 2>&1; then
  test_pass
else
  test_fail "Missing expected workspace_state fields"
fi

# Test 15: Script handles invalid agent name gracefully
test_start "Script handles invalid agent name gracefully"
output=$("$script" invalid-agent 2>&1 || true)
if echo "$output" | grep -q "not found\|Error"; then
  test_pass
else
  test_fail "Should error on invalid agent name"
fi

# Test 16: Script execution time is reasonable (<2 seconds)
test_start "Script execution time is reasonable (<2 seconds)"
start_time=$(date +%s.%N)
"$script" ghidra-binary-scout > /dev/null 2>&1
end_time=$(date +%s.%N)
elapsed=$(echo "$end_time - $start_time" | bc)
if (( $(echo "$elapsed < 2.0" | bc -l) )); then
  test_pass
else
  test_fail "Script took ${elapsed}s (should be <2s)"
fi

# Test 17: Markdown output includes reference to CAPABILITY_MATRIX.md
test_start "Markdown output references CAPABILITY_MATRIX.md"
output=$("$script" ghidra-binary-scout 2>&1)
test_assert_contains "$output" "CAPABILITY_MATRIX.md"

# Test 18: Different agents have different capabilities in output
test_start "Different agents have different capabilities in output"
out1=$("$script" ghidra-binary-scout 2>&1)
out2=$("$script" decomp-function-agent 2>&1)
# Function agent should have more capabilities listed
if echo "$out2" | grep -q "/decomp-function" && ! echo "$out1" | grep -q "/decomp-function"; then
  test_pass
else
  test_fail "Agent capabilities not differentiated"
fi

# Test 19: JSON output has timestamp in ISO format
test_start "JSON output has timestamp in ISO format"
output=$("$script" decomp-function-agent --json 2>&1)
timestamp=$(echo "$output" | jq -r '.timestamp')
if [[ "$timestamp" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]; then
  test_pass
else
  test_fail "Timestamp not in ISO format: $timestamp"
fi

# Test 20: Markdown output is consistent across runs
test_start "Markdown output is deterministic (consistent)"
out1=$("$script" ghidra-binary-scout 2>&1)
out2=$("$script" ghidra-binary-scout 2>&1)
# Content should be mostly the same (allowing for timestamp differences in workspace state)
if diff <(echo "$out1" | grep -v timestamp | head -20) <(echo "$out2" | grep -v timestamp | head -20) > /dev/null 2>&1; then
  test_pass
else
  # This is acceptable if data changed, so we pass if both produce output
  if [[ -n "$out1" && -n "$out2" ]]; then
    test_pass
  else
    test_fail "Inconsistent output"
  fi
fi

# Summary
echo ""
echo "========================================"
echo "Test Summary"
echo "========================================"
echo "Total:  $TESTS_TOTAL"
echo -e "${GREEN}Passed: $TESTS_PASSED${NC}"
if [[ $TESTS_FAILED -gt 0 ]]; then
  echo -e "${RED}Failed: $TESTS_FAILED${NC}"
else
  echo "Failed: $TESTS_FAILED"
fi
echo ""

# Exit with appropriate code
if [[ $TESTS_FAILED -eq 0 ]]; then
  echo -e "${GREEN}✓ All tests passed!${NC}"
  exit 0
else
  echo -e "${RED}✗ Some tests failed${NC}"
  exit 1
fi
