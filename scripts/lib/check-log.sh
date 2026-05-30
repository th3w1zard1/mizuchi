#!/usr/bin/env bash
# Shared verbose logging for validation/audit scripts.
# Verbose by default; pass --quiet to suppress trace (keep failures + summary counts).

: "${CHECK_LOG_QUIET:=0}"
: "${CHECK_LOG_SCRIPT:=check}"
: "${CHECK_LOG_PASSED:=0}"
: "${CHECK_LOG_FAILED:=0}"
declare -a CHECK_LOG_FAILURES=()

check_log_init() {
  CHECK_LOG_SCRIPT="${1:-check}"
  CHECK_LOG_PASSED=0
  CHECK_LOG_FAILED=0
  CHECK_LOG_FAILURES=()
}

check_log_configure() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --quiet) CHECK_LOG_QUIET=1; shift ;;
      *) shift ;;
    esac
  done
}

check_log_trace() {
  [[ "$CHECK_LOG_QUIET" -eq 0 ]] || return 0
  printf '%s: %s\n' "$CHECK_LOG_SCRIPT" "$*" >&2
}

check_log_pass() {
  local label="$1"
  CHECK_LOG_PASSED=$((CHECK_LOG_PASSED + 1))
  check_log_trace "ok    $label"
}

check_log_fail() {
  local label="$1"
  CHECK_LOG_FAILED=$((CHECK_LOG_FAILED + 1))
  CHECK_LOG_FAILURES+=("$label")
  printf '%s: fail  %s\n' "$CHECK_LOG_SCRIPT" "$label" >&2
}

check_log_read_file() {
  local abs_path="$1"
  local rel_path="${2:-$abs_path}"
  local label="${3:-exists}"
  if [[ -f "$abs_path" ]]; then
    check_log_trace "read  $rel_path ($label)"
    check_log_pass "file $rel_path"
    return 0
  fi
  check_log_fail "missing file: $rel_path ($label)"
  return 1
}

check_log_read_dir() {
  local abs_path="$1"
  local rel_path="${2:-$abs_path}"
  local label="${3:-directory}"
  if [[ -d "$abs_path" ]]; then
    check_log_trace "read  $rel_path/ ($label)"
    check_log_pass "dir $rel_path"
    return 0
  fi
  check_log_fail "missing directory: $rel_path ($label)"
  return 1
}

check_log_grep_file() {
  local file="$1"
  local pattern="$2"
  local label="$3"
  check_log_trace "grep  ${file} pattern=${pattern} ($label)"
  if grep -q "$pattern" "$file"; then
    check_log_pass "grep $label in ${file}"
    return 0
  fi
  check_log_fail "grep miss $label in ${file} (pattern=$pattern)"
  return 1
}

check_log_mcp_server() {
  local mcp_file="$1"
  local server="$2"
  check_log_trace "mcp   server=${server} file=${mcp_file}"
  if grep -q "\"${server}\"" "$mcp_file"; then
    check_log_pass "mcp server ${server}"
    return 0
  fi
  check_log_fail "mcp server missing: ${server} (file=${mcp_file})"
  return 1
}

check_log_file_op() {
  local rel_path="$1"
  local op="$2"
  check_log_trace "io    ${op} ${rel_path}"
  check_log_pass "io ${op} ${rel_path}"
}

check_log_summary() {
  local status="${1:-done}"
  printf '\n--- %s summary (%s) ---\n' "$CHECK_LOG_SCRIPT" "$status" >&2
  printf 'passed=%d failed=%d\n' "$CHECK_LOG_PASSED" "$CHECK_LOG_FAILED" >&2
  if [[ ${#CHECK_LOG_FAILURES[@]} -gt 0 ]]; then
    printf 'failures:\n' >&2
    local item
    for item in "${CHECK_LOG_FAILURES[@]}"; do
      printf '  - %s\n' "$item" >&2
    done
  fi
}
