#!/usr/bin/env bash
# Verify byte-identical object match via objdiff.
# Usage: objdiff-gate.sh <target.o> <candidate.o> [--quiet]
# Exit 0 = 0 differences (match); 1 = non-zero diff or tool error; 2 = usage.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/check-log.sh
source "$ROOT/scripts/lib/check-log.sh"
# shellcheck source=scripts/lib/guide-manifest.sh
source "$ROOT/scripts/lib/guide-manifest.sh"

quiet=0
target="" candidate=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet) quiet=1; shift ;;
    -h|--help)
      cat <<EOF
usage: objdiff-gate.sh <target.o> <candidate.o> [--quiet]

Compares target and candidate object files with objdiff.
Verbose logging is the default; use --quiet for machine-only output.

Examples:
  ./scripts/objdiff-gate.sh target.o prompts/foo/build/candidate.o
  ./scripts/objdiff-gate.sh target.o candidate.o --quiet
EOF
      exit 0
      ;;
    *)
      if [[ -z "$target" ]]; then target="$1"
      elif [[ -z "$candidate" ]]; then candidate="$1"
      else
        echo "unexpected argument: $1" >&2
        echo "  ./scripts/objdiff-gate.sh target.o prompts/foo/build/candidate.o" >&2
        exit 2
      fi
      shift
      ;;
  esac
done

CHECK_LOG_QUIET=$quiet
check_log_init "objdiff-gate"
guide_manifest_load "$ROOT"

if [[ -z "$target" || -z "$candidate" ]]; then
  check_log_fail "missing target.o or candidate.o"
  check_log_summary "OBJDIFF_GATE_FAIL"
  echo "Error: missing target.o or candidate.o" >&2
  echo "  ./scripts/objdiff-gate.sh target.o prompts/foo/build/candidate.o" >&2
  exit 2
fi

for f in "$target" "$candidate"; do
  if [[ ! -f "$f" ]]; then
    check_log_fail "file not found: $(guide_manifest_rel "$ROOT" "$f")"
    check_log_summary "OBJDIFF_GATE_FAIL"
    exit 1
  fi
done

check_log_read_file "$target" "$(guide_manifest_rel "$ROOT" "$target")" "target.o" || true
check_log_read_file "$candidate" "$(guide_manifest_rel "$ROOT" "$candidate")" "candidate.o" || true

if ! command -v objdiff >/dev/null 2>&1; then
  check_log_fail "objdiff not found on PATH"
  check_log_summary "OBJDIFF_GATE_FAIL"
  echo "objdiff-gate: objdiff not found on PATH" >&2
  echo "Install: https://github.com/encounter/objdiff" >&2
  exit 1
fi

out="$(mktemp)"
trap 'rm -f "$out"' EXIT

check_log_trace "run   objdiff diff $(guide_manifest_rel "$ROOT" "$target") $(guide_manifest_rel "$ROOT" "$candidate")"
set +e
objdiff diff "$target" "$candidate" >"$out" 2>&1
status=$?
set -e

if [[ $status -ne 0 ]]; then
  [[ "$quiet" -eq 0 ]] && cat "$out" >&2
  check_log_fail "objdiff exited $status"
  check_log_summary "OBJDIFF_GATE_FAIL"
  exit 1
fi

body="$(cat "$out")"
[[ "$quiet" -eq 0 ]] && printf '%s\n' "$body" >&2

if grep -qiE '0 diff|no diff|identical|perfect match|0 differences' <<<"$body"; then
  check_log_summary "OBJDIFF_GATE_OK"
  echo "OBJDIFF_GATE_OK target=$(guide_manifest_rel "$ROOT" "$target") candidate=$(guide_manifest_rel "$ROOT" "$candidate")"
  exit 0
fi

if grep -qiE '[1-9][0-9]* diff|[1-9][0-9]* difference' <<<"$body"; then
  check_log_fail "non-zero difference count reported"
  check_log_summary "OBJDIFF_GATE_FAIL"
  exit 1
fi

if [[ -z "${body//[[:space:]]/}" ]]; then
  check_log_summary "OBJDIFF_GATE_OK"
  echo "OBJDIFF_GATE_OK target=$(guide_manifest_rel "$ROOT" "$target") candidate=$(guide_manifest_rel "$ROOT" "$candidate")"
  exit 0
fi

check_log_fail "could not confirm 0 differences from objdiff output"
check_log_summary "OBJDIFF_GATE_FAIL"
exit 1
