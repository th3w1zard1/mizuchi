#!/usr/bin/env bash
# Placeholder compiler for Mizuchi — replace with your decomp project's compile script.
# Mizuchi invokes: compilerScript <source.c> <output.o>
# Exit 0 on success; non-zero on compile failure.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/check-log.sh
source "$ROOT/scripts/lib/check-log.sh"
# shellcheck source=scripts/lib/guide-manifest.sh
source "$ROOT/scripts/lib/guide-manifest.sh"

quiet=0

usage() {
  cat <<EOF
Usage: compile-placeholder.sh <source.c> <output.o> [--quiet]

Default Mizuchi compiler stub — exits non-zero until mizuchi.yaml global.compilerScript
is wired to a real compiler.

Options:
  --quiet    Suppress verbose trace (keep failures + summary)

Examples:
  ./scripts/compile-placeholder.sh prompts/foo/candidate.c prompts/foo/build/candidate.o
  ./scripts/compile-placeholder.sh candidate.c candidate.o --quiet
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet) quiet=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) break ;;
  esac
done

CHECK_LOG_QUIET=$quiet
check_log_init "compile-placeholder"
guide_manifest_load "$ROOT"
guide_manifest_trace_defaults "$ROOT"

if [[ $# -lt 2 ]]; then
  check_log_fail "missing arguments (need source.c and output.o)"
  check_log_summary "COMPILE_PLACEHOLDER_FAIL"
  usage >&2
  exit 2
fi

cfile="$1"
ofile="$2"
check_log_read_file "$cfile" "$(guide_manifest_rel "$ROOT" "$cfile")" "source.c" || {
  check_log_summary "COMPILE_PLACEHOLDER_FAIL"
  exit 2
}
check_log_trace "target $(guide_manifest_rel "$ROOT" "$ofile")"

msg="compile-placeholder: wire a real compiler (MSVC/clang) in mizuchi.yaml global.compilerScript"
check_log_fail "$msg"
printf '%s\n' "$msg" >&2
check_log_summary "COMPILE_PLACEHOLDER_FAIL"
exit 1
