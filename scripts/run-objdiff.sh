#!/usr/bin/env bash
# MCP tool: run_objdiff
# Validates byte-identical object match via objdiff.
# Returns JSON with status (matched/mismatched), difference count, and message.
#
# Usage: run-objdiff.sh <target.o> <candidate.o>
# Output: JSON object with keys: status, differences, message
# Exit: 0 on success (tool ran, regardless of match result); 1 on error (invalid args, file not found, objdiff unavailable)

set -euo pipefail

target="" candidate=""

# Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      echo "usage: $0 <target.o> <candidate.o>" >&2
      echo "Returns JSON with objdiff verification result" >&2
      exit 2
      ;;
    *)
      if [[ -z "$target" ]]; then target="$1"
      elif [[ -z "$candidate" ]]; then candidate="$1"
      else
        echo "unexpected argument: $1" >&2
        exit 2
      fi
      shift
      ;;
  esac
done

# Validate arguments
if [[ -z "$target" || -z "$candidate" ]]; then
  jq -n --arg message "Invalid arguments: target and candidate paths required" \
    '{status: "error", differences: -1, message: $message}'
  exit 1
fi

# Check file existence (before checking objdiff so we report file errors first)
if [[ ! -f "$target" ]]; then
  jq -n --arg target "$target" --arg message "Target file not found: $target" \
    '{status: "error", differences: -1, message: $message, file: $target}'
  exit 1
fi

if [[ ! -f "$candidate" ]]; then
  jq -n --arg candidate "$candidate" --arg message "Candidate file not found: $candidate" \
    '{status: "error", differences: -1, message: $message, file: $candidate}'
  exit 1
fi

# Check objdiff availability
if ! command -v objdiff >/dev/null 2>&1; then
  jq -n --arg message "objdiff not found on PATH (install from https://github.com/encounter/objdiff)" \
    '{status: "error", differences: -1, message: $message}'
  exit 1
fi

# Run objdiff
out="$(mktemp)"
trap 'rm -f "$out"' EXIT

set +e
objdiff diff "$target" "$candidate" >"$out" 2>&1
objdiff_exit=$?
set -e

# Parse objdiff output to determine difference count
body="$(cat "$out")"
differences=-1

# Heuristics: look for explicit zero-diff indicators (match)
if grep -qiE '(^|[^0-9])(0[[:space:]]*(diff|differences)|no diff|identical|perfect match)' <<<"$body"; then
  differences=0
elif grep -qE '[1-9][0-9]* difference' <<<"$body"; then
  # Extract the difference count if present
  count_match=$(grep -oE '[1-9][0-9]* difference' <<<"$body" | head -1 | grep -oE '^[0-9]+')
  if [[ -n "$count_match" ]]; then
    differences=$count_match
  else
    differences=1  # Conservative: at least 1 if we see "difference" keyword
  fi
elif [[ -z "${body//[[:space:]]/}" && "$objdiff_exit" -eq 0 ]]; then
  # Empty output with exit 0 — assume match (some objdiff versions)
  differences=0
fi

# Generate JSON output
if [[ $objdiff_exit -ne 0 ]]; then
  # objdiff tool error
  jq -n --argjson differences "$differences" --arg output "$body" \
    '{status: "error", differences: $differences, message: "objdiff exited with error", output: $output}'
  exit 1
elif [[ $differences -eq 0 ]]; then
  jq -n \
    '{status: "matched", differences: 0, message: "WORKSPACE_SURFACE_OK"}'
else
  jq -n --argjson differences "$differences" \
    '{status: "mismatched", differences: $differences, message: "Object files do not match"}'
fi

exit 0
