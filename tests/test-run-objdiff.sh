#!/usr/bin/env bash
# Test suite for run-objdiff.sh MCP tool

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_PATH="$ROOT/scripts/run-objdiff.sh"

TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

run_test() {
  local test_name="$1"
  local test_fn="$2"
  
  ((TESTS_RUN++))
  
  if $test_fn >/dev/null 2>&1; then
    echo "✓ $test_name"
    ((TESTS_PASSED++))
  else
    echo "✗ $test_name"
    ((TESTS_FAILED++))
  fi
}

# ============================================================================
# Basic infrastructure tests
# ============================================================================

test_script_exists() {
  [[ -f "$SCRIPT_PATH" && -x "$SCRIPT_PATH" ]]
}

test_script_has_shebang() {
  head -1 "$SCRIPT_PATH" | grep -q "^#!/usr/bin/env bash"
}

# ============================================================================
# Help and usage tests
# ============================================================================

test_help_flag() {
  local output
  output=$("$SCRIPT_PATH" --help 2>&1 || true)
  echo "$output" | grep -q "usage:"
}

# ============================================================================
# Error handling tests
# ============================================================================

test_missing_arguments_returns_error_json() {
  local output
  output=$("$SCRIPT_PATH" 2>&1 || true)
  echo "$output" | jq . > /dev/null 2>&1 && \
  echo "$output" | jq -e '.status == "error"' > /dev/null 2>&1
}

test_single_argument_returns_error_json() {
  local output
  output=$("$SCRIPT_PATH" /fake/file.o 2>&1 || true)
  echo "$output" | jq . > /dev/null 2>&1 && \
  echo "$output" | jq -e '.status == "error"' > /dev/null 2>&1
}

test_nonexistent_target_file() {
  local output
  output=$("$SCRIPT_PATH" /nonexistent/target.o /nonexistent/candidate.o 2>&1 || true)
  echo "$output" | jq . > /dev/null 2>&1 && \
  echo "$output" | jq -e '.status == "error"' > /dev/null 2>&1 && \
  echo "$output" | jq -e '.message | contains("Target file not found")' > /dev/null 2>&1
}

test_nonexistent_candidate_file() {
  local tmpdir
  tmpdir=$(mktemp -d)
  trap "rm -rf $tmpdir" RETURN
  
  local target="$tmpdir/target.o"
  echo "dummy" > "$target"
  
  local output
  output=$("$SCRIPT_PATH" "$target" /nonexistent/candidate.o 2>&1 || true)
  echo "$output" | jq . > /dev/null 2>&1 && \
  echo "$output" | jq -e '.status == "error"' > /dev/null 2>&1 && \
  echo "$output" | jq -e '.message | contains("Candidate file not found")' > /dev/null 2>&1
}

# ============================================================================
# JSON output format tests
# ============================================================================

test_output_is_valid_json() {
  local tmpdir
  tmpdir=$(mktemp -d)
  trap "rm -rf $tmpdir" RETURN
  
  local target="$tmpdir/target.o"
  local candidate="$tmpdir/candidate.o"
  echo "ELF" > "$target"
  echo "ELF" > "$candidate"
  
  local output
  output=$("$SCRIPT_PATH" "$target" "$candidate" 2>&1 || true)
  echo "$output" | jq . > /dev/null 2>&1
}

test_output_has_status() {
  local tmpdir
  tmpdir=$(mktemp -d)
  trap "rm -rf $tmpdir" RETURN
  
  local target="$tmpdir/target.o"
  local candidate="$tmpdir/candidate.o"
  echo "ELF" > "$target"
  echo "ELF" > "$candidate"
  
  local output
  output=$("$SCRIPT_PATH" "$target" "$candidate" 2>&1 || true)
  echo "$output" | jq -e '.status' > /dev/null 2>&1
}

test_output_has_differences() {
  local tmpdir
  tmpdir=$(mktemp -d)
  trap "rm -rf $tmpdir" RETURN
  
  local target="$tmpdir/target.o"
  local candidate="$tmpdir/candidate.o"
  echo "ELF" > "$target"
  echo "ELF" > "$candidate"
  
  local output
  output=$("$SCRIPT_PATH" "$target" "$candidate" 2>&1 || true)
  echo "$output" | jq -e '.differences' > /dev/null 2>&1
}

test_output_has_message() {
  local tmpdir
  tmpdir=$(mktemp -d)
  trap "rm -rf $tmpdir" RETURN
  
  local target="$tmpdir/target.o"
  local candidate="$tmpdir/candidate.o"
  echo "ELF" > "$target"
  echo "ELF" > "$candidate"
  
  local output
  output=$("$SCRIPT_PATH" "$target" "$candidate" 2>&1 || true)
  echo "$output" | jq -e '.message' > /dev/null 2>&1
}

# ============================================================================
# Exit code tests
# ============================================================================

test_exit_nonzero_on_missing_files() {
  ! ("$SCRIPT_PATH" /nonexistent/target.o /nonexistent/candidate.o 2>&1 >/dev/null)
}

test_exit_nonzero_on_missing_args() {
  ! ("$SCRIPT_PATH" 2>&1 >/dev/null)
}

# ============================================================================
# Run all tests
# ============================================================================

echo "Running test suite for run-objdiff.sh MCP tool"
echo "================================================"

run_test "Script exists and is executable" test_script_exists
run_test "Script has proper shebang" test_script_has_shebang
run_test "Help flag works" test_help_flag
run_test "Missing arguments returns error JSON" test_missing_arguments_returns_error_json
run_test "Single argument returns error JSON" test_single_argument_returns_error_json
run_test "Nonexistent target file returns error" test_nonexistent_target_file
run_test "Nonexistent candidate file returns error" test_nonexistent_candidate_file
run_test "Output is valid JSON" test_output_is_valid_json
run_test "Output has status field" test_output_has_status
run_test "Output has differences field" test_output_has_differences
run_test "Output has message field" test_output_has_message
run_test "Exit non-zero for missing files" test_exit_nonzero_on_missing_files
run_test "Exit non-zero for missing arguments" test_exit_nonzero_on_missing_args

echo ""
echo "================================================"
echo "Tests run:    $TESTS_RUN"
echo "Tests passed: $TESTS_PASSED"
echo "Tests failed: $TESTS_FAILED"

if [[ $TESTS_FAILED -eq 0 ]]; then
  exit 0
else
  exit 1
fi
