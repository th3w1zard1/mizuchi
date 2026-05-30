#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${MIZUCHI_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"

# shellcheck source=scripts/lib/check-log.sh
source "$SCRIPT_DIR/lib/check-log.sh"

quiet=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet) quiet=1; shift ;;
    -h|--help)
      cat <<EOF
usage: validate-capability-parity.sh [--quiet]

Checks decomp-cli.sh usage tokens against CAPABILITY_MATRIX.md contracts.
Verbose logging is the default; use --quiet for machine-only output.
EOF
      exit 0
      ;;
    *) echo "unexpected argument: $1" >&2; exit 2 ;;
  esac
done

CHECK_LOG_QUIET=$quiet
check_log_init "validate-capability-parity"
check_log_trace "root  ${ROOT#${HOME}/}"

cli_file="$ROOT/scripts/decomp-cli.sh"
matrix_file="$ROOT/CAPABILITY_MATRIX.md"

required_cli_commands=(
  help
  ghidra-scout
  decomp-prompt
  decomp-atlas
  decomp-function
  decomp-integrate
  list-prompts
  inject-context
  run-objdiff
  programmatic-phase
  verify-surface
)

failures=0
record_fail() { failures=1; }

check_log_read_file "$cli_file" "${cli_file#$ROOT/}" "CLI entrypoint" || record_fail
check_log_read_file "$matrix_file" "${matrix_file#$ROOT/}" "capability matrix" || record_fail

for cmd in "${required_cli_commands[@]}"; do
  pattern="^[[:space:]]+${cmd}([[:space:]]|$)"
  check_log_trace "grep  ${cli_file#$ROOT/} pattern=${cmd} (CLI usage block)"
  if grep -qE "$pattern" "$cli_file"; then
    check_log_pass "CLI command ${cmd}"
  else
    check_log_fail "missing CLI command in usage: ${cmd}"
    record_fail
  fi
done

check_log_trace "grep  ${cli_file#$ROOT/} pattern=list-prompts status enum"
if grep -q "list-prompts \\[status=<matched|in_progress|integrated|pending|blocked>\\]" "$cli_file"; then
  check_log_pass "list-prompts status enum in decomp-cli"
else
  check_log_fail "missing CLI status enum contract for list-prompts"
  record_fail
fi

declare -A matrix_tokens=(
  ["list_prompts(status=<matched|integrated|in_progress|pending|blocked>)"]="list-prompts [status=<matched|in_progress|integrated|pending|blocked>]"
  ["run_objdiff"]="run-objdiff"
  ["inject-context"]="inject-context"
)

for token in "${!matrix_tokens[@]}"; do
  check_log_grep_file "$matrix_file" "$token" "matrix token ${token}" || record_fail
done

if [[ "$failures" -ne 0 ]]; then
  check_log_summary "CAPABILITY_PARITY_FAIL"
  exit 1
fi

check_log_summary "CAPABILITY_PARITY_OK"
echo "CAPABILITY_PARITY_OK"
