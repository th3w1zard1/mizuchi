#!/usr/bin/env bash
# MCP tool: run_objdiff
# Validates byte-identical object match via objdiff.
# Returns JSON with status (matched/mismatched), difference count, and message.
#
# Usage: run-objdiff.sh <target.o> <candidate.o>
# Output: JSON object with keys: status, differences, message
# Exit: 0 on success (tool ran, regardless of match result); 1 on error (invalid args, file not found, objdiff unavailable)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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
set +e
report="$("$ROOT/scripts/lib/verify-objdiff.sh" "$target" "$candidate" 2>&1)"
rc=$?
set -e

if [[ "$rc" -ne 0 ]]; then
  printf '%s\n' "$report"
  exit 1
fi

jq '{status, differences, message}' <<<"$report"
exit 0
