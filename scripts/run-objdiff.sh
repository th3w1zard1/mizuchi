#!/usr/bin/env bash
# MCP tool: run_objdiff
# Validates byte-identical object match via objdiff.
# Returns JSON on stdout; verbose trace on stderr (use --quiet to suppress trace).
#
# Usage: run-objdiff.sh [--quiet] <target.o> <candidate.o>
# Output: JSON object with keys: status, differences, message
# Exit: 0 on success (tool ran, regardless of match result); 1 on error

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib/check-log.sh
source "$ROOT/scripts/lib/check-log.sh"
# shellcheck source=scripts/lib/guide-manifest.sh
source "$ROOT/scripts/lib/guide-manifest.sh"

target="" candidate=""

usage() {
  cat <<EOF
Usage: run-objdiff.sh [--quiet] <target.o> <candidate.o>

Runs objdiff and returns JSON on stdout (logs on stderr).

Options:
  --quiet   Suppress verbose trace (keep summary + JSON)
  -h, --help  Show help

Examples:
  ./scripts/run-objdiff.sh prompts/foo/target.o prompts/foo/build/candidate.o
  ./scripts/run-objdiff.sh --quiet target.o candidate.o | jq .status

Exit codes:
  0  objdiff ran (matched or mismatched)
  1  invalid args, missing files, or objdiff error
  2  usage
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --quiet)
      check_log_configure --quiet
      shift
      ;;
    *)
      if [[ -z "$target" ]]; then target="$1"
      elif [[ -z "$candidate" ]]; then candidate="$1"
      else
        echo "Error: unexpected argument: $1" >&2
        echo "  ./scripts/run-objdiff.sh <target.o> <candidate.o>" >&2
        exit 2
      fi
      shift
      ;;
  esac
done

check_log_init "run-objdiff"
guide_manifest_load "$ROOT"
guide_manifest_trace_defaults "$ROOT"

emit_json() {
  jq -n "$@"
}

if [[ -z "$target" || -z "$candidate" ]]; then
  check_log_fail "missing target and candidate paths"
  check_log_summary "RUN_OBJDIFF_FAIL"
  emit_json --arg message "Invalid arguments: target and candidate paths required" \
    '{status: "error", differences: -1, message: $message}'
  exit 1
fi

if [[ -f "$target" ]]; then
  check_log_read_file "$target" "$(guide_manifest_rel "$ROOT" "$target")" "target object"
else
  check_log_trace "read  $(guide_manifest_rel "$ROOT" "$target") (missing target object)"
  check_log_fail "missing target: $(guide_manifest_rel "$ROOT" "$target")"
  check_log_summary "RUN_OBJDIFF_FAIL"
  emit_json --arg target "$target" --arg message "Target file not found: $target" \
    '{status: "error", differences: -1, message: $message, file: $target}'
  exit 1
fi

if [[ -f "$candidate" ]]; then
  check_log_read_file "$candidate" "$(guide_manifest_rel "$ROOT" "$candidate")" "candidate object"
else
  check_log_trace "read  $(guide_manifest_rel "$ROOT" "$candidate") (missing candidate object)"
  check_log_fail "missing candidate: $(guide_manifest_rel "$ROOT" "$candidate")"
  check_log_summary "RUN_OBJDIFF_FAIL"
  emit_json --arg candidate "$candidate" --arg message "Candidate file not found: $candidate" \
    '{status: "error", differences: -1, message: $message, file: $candidate}'
  exit 1
fi

if ! command -v objdiff >/dev/null 2>&1; then
  check_log_fail "objdiff not on PATH"
  check_log_summary "RUN_OBJDIFF_FAIL"
  emit_json --arg message "objdiff not found on PATH (install from https://github.com/encounter/objdiff)" \
    '{status: "error", differences: -1, message: $message}'
  exit 1
fi

out="$(mktemp)"
out_existed=0
[[ -f "$out" ]] && out_existed=1
trap 'rm -f "$out"' EXIT

check_log_trace "run   objdiff diff $(guide_manifest_rel "$ROOT" "$target") $(guide_manifest_rel "$ROOT" "$candidate")"

set +e
objdiff diff "$target" "$candidate" >"$out" 2>&1
objdiff_exit=$?
set -e

check_log_file_written "$out" "$ROOT" "$out_existed"
check_log_pass "objdiff capture"

body="$(cat "$out")"
differences=-1

if grep -qiE '(^|[^0-9])(0[[:space:]]*(diff|differences)|no diff|identical|perfect match)' <<<"$body"; then
  differences=0
elif grep -qE '[1-9][0-9]* difference' <<<"$body"; then
  count_match=$(grep -oE '[1-9][0-9]* difference' <<<"$body" | head -1 | grep -oE '^[0-9]+')
  if [[ -n "$count_match" ]]; then
    differences=$count_match
  else
    differences=1
  fi
elif [[ -z "${body//[[:space:]]/}" && "$objdiff_exit" -eq 0 ]]; then
  differences=0
fi

if [[ $objdiff_exit -ne 0 ]]; then
  check_log_fail "objdiff exited $objdiff_exit"
  check_log_summary "RUN_OBJDIFF_FAIL"
  emit_json --argjson differences "$differences" --arg output "$body" \
    '{status: "error", differences: $differences, message: "objdiff exited with error", output: $output}'
  exit 1
elif [[ $differences -eq 0 ]]; then
  check_log_summary "RUN_OBJDIFF_OK"
  emit_json \
    '{status: "matched", differences: 0, message: "Object files match (0 differences)"}'
  printf 'RUN_OBJDIFF_OK status=matched differences=0\n' >&2
else
  check_log_summary "RUN_OBJDIFF_MISMATCH"
  emit_json --argjson differences "$differences" \
    '{status: "mismatched", differences: $differences, message: "Object files do not match"}'
  printf 'RUN_OBJDIFF_OK status=mismatched differences=%s\n' "$differences" >&2
fi

exit 0
