#!/usr/bin/env bash
# Test suite for run-objdiff.sh MCP tool

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_PATH="$ROOT/scripts/run-objdiff.sh"

run_objdiff_stdout() {
  # JSON on stdout; verbose trace on stderr.
  "$SCRIPT_PATH" "$@" 2>/dev/null
}

run_objdiff_stderr() {
  "$SCRIPT_PATH" "$@" 2>/dev/null 1>/dev/null
}

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
  echo "$output" | grep -qi "usage:"
}

# ============================================================================
# Error handling tests
# ============================================================================

test_missing_arguments_returns_error_json() {
  local output
  output=$(run_objdiff_stdout || true)
  echo "$output" | jq . > /dev/null 2>&1 && \
  echo "$output" | jq -e '.status == "error"' > /dev/null 2>&1
}

test_single_argument_returns_error_json() {
  local output
  output=$(run_objdiff_stdout /fake/file.o 2>&1 || true)
  echo "$output" | jq . > /dev/null 2>&1 && \
  echo "$output" | jq -e '.status == "error"' > /dev/null 2>&1
}

test_nonexistent_target_file() {
  local output
  output=$(run_objdiff_stdout /nonexistent/target.o /nonexistent/candidate.o 2>&1 || true)
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
  output=$(run_objdiff_stdout "$target" /nonexistent/candidate.o 2>&1 || true)
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
  output=$(run_objdiff_stdout "$target" "$candidate" 2>&1 || true)
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
  output=$(run_objdiff_stdout "$target" "$candidate" 2>&1 || true)
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
  output=$(run_objdiff_stdout "$target" "$candidate" 2>&1 || true)
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
  output=$(run_objdiff_stdout "$target" "$candidate" || true)
  echo "$output" | jq -e '.message' > /dev/null 2>&1
}

test_help_has_examples() {
  "$SCRIPT_PATH" --help 2>&1 | grep -q "Examples:"
}

test_trace_lists_mcp_servers() {
  local tmpdir target candidate err
  tmpdir=$(mktemp -d)
  target="$tmpdir/target.o"
  candidate="$tmpdir/candidate.o"
  echo "ELF" > "$target"
  echo "ELF" > "$candidate"
  err=$("$SCRIPT_PATH" "$target" "$candidate" 2>&1 1>/dev/null || true)
  echo "$err" | grep -q "mcp   server=agdec-http"
  echo "$err" | grep -q "mcp   server=mizuchi"
  rm -rf "$tmpdir"
}

test_matched_message_not_workspace_surface() {
  local tmpdir target candidate output
  tmpdir=$(mktemp -d)
  target="$tmpdir/target.o"
  candidate="$tmpdir/candidate.o"
  echo "ELF" > "$target"
  echo "ELF" > "$candidate"
  output=$(run_objdiff_stdout "$target" "$candidate" || true)
  if echo "$output" | jq -e '.message | contains("WORKSPACE_SURFACE_OK")' >/dev/null 2>&1; then
    rm -rf "$tmpdir"
    return 1
  fi
  rm -rf "$tmpdir"
  return 0
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
run_test "Help includes Examples" test_help_has_examples
run_test "Verbose trace lists MCP servers" test_trace_lists_mcp_servers
run_test "Missing arguments returns error JSON" test_missing_arguments_returns_error_json
run_test "Single argument returns error JSON" test_single_argument_returns_error_json
run_test "Nonexistent target file returns error" test_nonexistent_target_file
run_test "Nonexistent candidate file returns error" test_nonexistent_candidate_file
run_test "Output is valid JSON" test_output_is_valid_json
run_test "Output has status field" test_output_has_status
run_test "Output has differences field" test_output_has_differences
run_test "Output has message field" test_output_has_message
run_test "Matched message is not WORKSPACE_SURFACE_OK" test_matched_message_not_workspace_surface
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
