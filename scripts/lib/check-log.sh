#!/usr/bin/env bash
# Shared verbose logging for validation/audit scripts.
# Verbose by default; pass --quiet to suppress trace (keep failures + summary counts).

: "${CHECK_LOG_QUIET:=0}"
: "${CHECK_LOG_SCRIPT:=check}"
: "${CHECK_LOG_PASSED:=0}"
: "${CHECK_LOG_FAILED:=0}"
declare -a CHECK_LOG_FAILURES=()
declare -a CHECK_LOG_CHANGES=()

_check_log_rel() {
  local root="$1"
  local abs_path="$2"
  if [[ -n "$root" && "$abs_path" == "$root/"* ]]; then
    printf '%s\n' "${abs_path#"$root/"}"
  else
    printf '%s\n' "$abs_path"
  fi
}

check_log_init() {
  CHECK_LOG_SCRIPT="${1:-check}"
  CHECK_LOG_PASSED=0
  CHECK_LOG_FAILED=0
  CHECK_LOG_FAILURES=()
  CHECK_LOG_CHANGES=()
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
  local detail="${3:-}"
  if [[ -n "$detail" ]]; then
    check_log_trace "io    ${op} ${rel_path} (${detail})"
    CHECK_LOG_CHANGES+=("${op} ${rel_path} (${detail})")
    check_log_pass "io ${op} ${rel_path} (${detail})"
  else
    check_log_trace "io    ${op} ${rel_path}"
    CHECK_LOG_CHANGES+=("${op} ${rel_path}")
    check_log_pass "io ${op} ${rel_path}"
  fi
}

# Record write after the fact; existed=1 means overwrite, 0 means new file.
check_log_file_written() {
  local abs_path="$1"
  local root="$2"
  local existed="$3"
  local rel_path
  rel_path="$(_check_log_rel "$root" "$abs_path")"
  local detail=""
  if [[ -f "$abs_path" ]]; then
    detail="$(wc -c <"$abs_path" | tr -d '[:space:]') bytes"
  fi
  if [[ "$existed" -eq 1 ]]; then
    check_log_file_op "$rel_path" "wrote" "$detail"
  else
    check_log_file_op "$rel_path" "created" "$detail"
  fi
}

check_log_file_appended() {
  local abs_path="$1"
  local root="$2"
  local detail="${3:-}"
  check_log_file_op "$(_check_log_rel "$root" "$abs_path")" "appended" "$detail"
}

check_log_file_removed() {
  local abs_path="$1"
  local root="$2"
  local detail="${3:-}"
  check_log_file_op "$(_check_log_rel "$root" "$abs_path")" "removed" "$detail"
}

check_log_run_cmd() {
  local label="$1"
  shift
  check_log_trace "run   ${label}: $*"
  check_log_pass "run ${label}"
}

check_log_run_step() {
  local step="$1"
  check_log_trace "step  ${step}"
  check_log_pass "step ${step}"
}

check_log_summary() {
  local status="${1:-done}"
  printf '\n--- %s summary (%s) ---\n' "$CHECK_LOG_SCRIPT" "$status" >&2
  printf 'passed=%d failed=%d\n' "$CHECK_LOG_PASSED" "$CHECK_LOG_FAILED" >&2
  if [[ ${#CHECK_LOG_CHANGES[@]} -gt 0 ]]; then
    printf 'changes:\n' >&2
    local change
    for change in "${CHECK_LOG_CHANGES[@]}"; do
      printf '  - %s\n' "$change" >&2
    done
  fi
  if [[ ${#CHECK_LOG_FAILURES[@]} -gt 0 ]]; then
    printf 'failures:\n' >&2
    local item
    for item in "${CHECK_LOG_FAILURES[@]}"; do
      printf '  - %s\n' "$item" >&2
    done
  fi
}
