#!/usr/bin/env bash
# Verify byte-identical object match via objdiff.
# Usage: objdiff-gate.sh <target.o> <candidate.o> [--quiet]
# Exit 0 = 0 differences (match); 1 = non-zero diff or tool error; 2 = usage.
set -euo pipefail

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

if ! command -v objdiff >/dev/null 2>&1; then
  echo "objdiff-gate: objdiff not found on PATH" >&2
  echo "Install: https://github.com/encounter/objdiff" >&2
  exit 1
fi

for f in "$target" "$candidate"; do
  if [[ ! -f "$f" ]]; then
    echo "objdiff-gate: file not found: $f" >&2
    exit 1
  fi
done

out="$(mktemp)"
trap 'rm -f "$out"' EXIT

set +e
objdiff diff "$target" "$candidate" >"$out" 2>&1
status=$?
set -e

if [[ $status -ne 0 ]]; then
  [[ "$quiet" -eq 0 ]] && cat "$out"
  echo "objdiff-gate: objdiff exited $status" >&2
  exit 1
fi

body="$(cat "$out")"
[[ "$quiet" -eq 0 ]] && printf '%s\n' "$body"

# Heuristics: treat explicit zero-diff wording as pass; any "N difference" with N>0 as fail.
if grep -qiE '0 diff|no diff|identical|perfect match|0 differences' <<<"$body"; then
  exit 0
fi

if grep -qiE '[1-9][0-9]* diff|[1-9][0-9]* difference' <<<"$body"; then
  echo "objdiff-gate: non-zero difference count reported" >&2
  exit 1
fi

# Empty output with exit 0 — assume match (some objdiff versions).
if [[ -z "${body//[[:space:]]/}" ]]; then
  exit 0
fi

# Ambiguous — require explicit zero in output for automation safety.
echo "objdiff-gate: could not confirm 0 differences from objdiff output" >&2
exit 1
