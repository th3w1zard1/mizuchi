#!/usr/bin/env bash
# Verify byte-identical object match via objdiff.
# Usage: objdiff-gate.sh <target.o> <candidate.o> [--quiet]
# Exit 0 = 0 differences (match); 1 = non-zero diff or tool error; 2 = usage.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
quiet=0
target="" candidate=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet) quiet=1; shift ;;
    -h|--help)
      echo "usage: $0 <target.o> <candidate.o> [--quiet]" >&2
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

if [[ -z "$target" || -z "$candidate" ]]; then
  echo "usage: $0 <target.o> <candidate.o> [--quiet]" >&2
  exit 2
fi

set +e
report="$("$ROOT/scripts/lib/verify-objdiff.sh" "$target" "$candidate" 2>&1)"
rc=$?
set -e

[[ "$quiet" -eq 0 ]] && printf '%s\n' "$report"
if [[ "$rc" -ne 0 ]]; then
  message="$(jq -r '.message // empty' <<<"$report" 2>/dev/null || true)"
  [[ -n "$message" ]] && echo "objdiff-gate: $message" >&2
  exit 1
fi

status="$(jq -r '.status' <<<"$report")"
if [[ "$status" == "matched" ]]; then
  exit 0
fi

differences="$(jq -r '.differences // -1' <<<"$report" 2>/dev/null || printf -- '-1')"
echo "objdiff-gate: non-zero differences reported: $differences" >&2
exit 1
