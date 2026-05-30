#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck source=scripts/lib/check-log.sh
source "$ROOT/scripts/lib/check-log.sh"

quiet=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet) quiet=1; shift ;;
    -h|--help)
      cat <<EOF
usage: run-test-suite.sh [--quiet]

Runs every tests/*.sh script in sorted order.
EOF
      exit 0
      ;;
    *) echo "unexpected argument: $1" >&2; exit 2 ;;
  esac
done

CHECK_LOG_QUIET=$quiet
check_log_init "run-test-suite"

shopt -s nullglob
tests=(tests/*.sh)
shopt -u nullglob

if [[ ${#tests[@]} -eq 0 ]]; then
  check_log_fail "no tests found under tests/"
  check_log_summary "RUN_TEST_SUITE_FAIL"
  exit 1
fi

IFS=$'\n' sorted_tests=($(printf '%s\n' "${tests[@]}" | sort))
unset IFS

passed=0
failed=0
failed_names=()

for test_script in "${sorted_tests[@]}"; do
  name="$(basename "$test_script")"
  check_log_trace "run   ${name}"
  if bash "$test_script"; then
    passed=$((passed + 1))
    check_log_pass "test ${name}"
  else
    failed=$((failed + 1))
    failed_names+=("$name")
    check_log_fail "test ${name}"
  fi
done

check_log_summary "run-test-suite"
printf '\nrun-test-suite: passed=%d failed=%d total=%d\n' "$passed" "$failed" "${#sorted_tests[@]}"

if [[ "$failed" -ne 0 ]]; then
  printf 'failed tests:\n' >&2
  for name in "${failed_names[@]}"; do
    printf '  - %s\n' "$name" >&2
  done
  exit 1
fi

printf 'RUN_TEST_SUITE_OK\n'
