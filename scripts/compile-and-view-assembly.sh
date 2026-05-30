#!/usr/bin/env bash
# Cursor-native analogue of Mizuchi compile_and_view_assembly MCP tool.
# Assembly + verdict on stdout; verbose trace on stderr (--quiet to suppress trace).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib/prompt-settings.sh
. "$ROOT/scripts/lib/prompt-settings.sh"
# shellcheck source=scripts/lib/check-log.sh
source "$ROOT/scripts/lib/check-log.sh"
# shellcheck source=scripts/lib/guide-manifest.sh
source "$ROOT/scripts/lib/guide-manifest.sh"
# shellcheck source=scripts/lib/cli-agent.sh
source "$ROOT/scripts/lib/cli-agent.sh"

prompt_dir=""
code_file=""
code_stdin=0
context_file=""
max_insns=120
quiet=0

usage() {
  cat <<EOF
Usage: compile-and-view-assembly.sh --prompt <prompts/<name>/> (--code-file <path.c> | --code-stdin)

Compiles candidate C with context, prints disassembly and objdiff verdict on stdout.

Options:
  --prompt <dir>     Prompt folder (required)
  --code-file <path> Candidate C source
  --code-stdin       Read candidate C from stdin
  --context <file>   Context header (default: context/ctx.h)
  --max-insns N      Limit objdump lines (default: 120)
  --quiet            Suppress verbose trace on stderr
  -h, --help         Show help

Examples:
  ./scripts/compile-and-view-assembly.sh --prompt prompts/fun_00148020/ --code-file trial.c
  echo 'int x() { return 0; }' | ./scripts/compile-and-view-assembly.sh --prompt prompts/fun_00148020/ --code-stdin
  ./scripts/compile-and-view-assembly.sh --prompt prompts/fun_00148020/ --code-file trial.c --quiet
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) prompt_dir="$2"; shift 2 ;;
    --code-file) code_file="$2"; shift 2 ;;
    --code-stdin) code_stdin=1; shift ;;
    --context) context_file="$2"; shift 2 ;;
    --max-insns) max_insns="$2"; shift 2 ;;
    --quiet) quiet=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Error: unknown argument: $1" >&2; echo "  ./scripts/compile-and-view-assembly.sh --help" >&2; exit 2 ;;
  esac
done

[[ "$quiet" -eq 1 ]] && check_log_configure --quiet
check_log_init "compile-and-view-assembly"
guide_manifest_load "$ROOT"
guide_manifest_trace_defaults "$ROOT"

if [[ -z "$prompt_dir" ]]; then
  cli_agent_missing_arg "compile-and-view-assembly.sh" "missing --prompt" \
    "./scripts/compile-and-view-assembly.sh --prompt prompts/fun_00148020/ --code-file trial.c"
fi
prompt_settings_require_dir "$prompt_dir" || exit $?

function_name="$(prompt_settings_get "$prompt_dir" functionName)"
target_object="$(prompt_settings_get "$prompt_dir" targetObjectPath)"
target_object="${target_object//\{\{functionName\}\}/$function_name}"

check_log_trace "read  settings $(guide_manifest_rel "$ROOT" "$prompt_dir/settings.yaml") function=${function_name}"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

if [[ "$code_stdin" -eq 1 ]]; then
  code_file="$tmpdir/submitted.c"
  cat >"$code_file"
  check_log_file_written "$code_file" "$ROOT" 0
  check_log_trace "read  stdin -> $(guide_manifest_rel "$ROOT" "$code_file")"
elif [[ -z "$code_file" || ! -f "$code_file" ]]; then
  cli_agent_missing_arg "compile-and-view-assembly.sh" "provide --code-file or --code-stdin" \
    "./scripts/compile-and-view-assembly.sh --prompt $prompt_dir --code-file trial.c"
else
  check_log_read_file "$code_file" "$(guide_manifest_rel "$ROOT" "$code_file")" "candidate source"
fi

if [[ -n "$context_file" ]]; then
  ctx="$context_file"
elif ctx_path="$(guide_default_context_path "$ROOT")" && [[ -f "$ctx_path" ]]; then
  ctx="$ctx_path"
else
  ctx="$tmpdir/empty.h"
  echo "/* empty context */" >"$ctx"
  check_log_file_written "$ctx" "$ROOT" 0
fi
check_log_read_file "$ctx" "$(guide_manifest_rel "$ROOT" "$ctx")" "context header"

combined="$tmpdir/combined.c"
{
  echo "/* context + candidate for $function_name */"
  cat "$ctx"
  echo
  cat "$code_file"
} >"$combined"
check_log_file_written "$combined" "$ROOT" 0

compile_args=("$prompt_dir" "$combined")
[[ "$quiet" -eq 1 ]] && compile_args=(--quiet "${compile_args[@]}")
if ! "$ROOT/scripts/compile-trial.sh" "${compile_args[@]}"; then
  check_log_fail "compile-trial failed"
  check_log_summary "COMPILE_AND_VIEW_ASSEMBLY_FAIL"
  echo "compile-and-view-assembly: compile failed" >&2
  exit 1
fi

obj_out="$(guide_prompt_build_path "$prompt_dir" "candidate.o")"
check_log_read_file "$obj_out" "$(guide_manifest_rel "$ROOT" "$obj_out")" "candidate object"

echo "=== disassembly: $function_name (candidate) ==="
if command -v objdump >/dev/null 2>&1; then
  check_log_trace "run   objdump -d $(guide_manifest_rel "$ROOT" "$obj_out")"
  objdump -d -M intel "$obj_out" | head -n "$max_insns"
else
  check_log_trace "run   objdump (not installed)"
  echo "(objdump not found — install binutils)" >&2
fi

target_path="$target_object"
[[ "$target_path" != /* ]] && target_path="$ROOT/$target_path"

echo
echo "=== objdiff summary ==="
if [[ ! -f "$target_path" ]]; then
  check_log_fail "target object missing: $(guide_manifest_rel "$ROOT" "$target_path")"
  check_log_summary "COMPILE_AND_VIEW_ASSEMBLY_OPEN"
  echo "[OPEN] target object missing: $target_path"
  echo "Cannot compute diff count without golden .o"
  printf 'COMPILE_AND_VIEW_ASSEMBLY_OK verdict=OPEN\n' >&2
  exit 0
fi

check_log_read_file "$target_path" "$(guide_manifest_rel "$ROOT" "$target_path")" "golden target"

if command -v objdiff >/dev/null 2>&1; then
  gate_args=("$target_path" "$obj_out")
  [[ "$quiet" -eq 1 ]] && gate_args=(--quiet "${gate_args[@]}")
  if "$ROOT/scripts/objdiff-gate.sh" "${gate_args[@]}"; then
    check_log_summary "COMPILE_AND_VIEW_ASSEMBLY_OK"
    echo "diff_count: 0"
    echo "verdict: MATCH"
    printf 'COMPILE_AND_VIEW_ASSEMBLY_OK verdict=MATCH diff_count=0\n' >&2
    exit 0
  fi
  check_log_summary "COMPILE_AND_VIEW_ASSEMBLY_MISMATCH"
  echo "diff_count: non-zero"
  echo "verdict: NOT_MATCHED"
  printf 'COMPILE_AND_VIEW_ASSEMBLY_OK verdict=NOT_MATCHED\n' >&2
  exit 0
fi

check_log_summary "COMPILE_AND_VIEW_ASSEMBLY_OPEN"
echo "[OPEN] objdiff not installed — assembly shown only"
printf 'COMPILE_AND_VIEW_ASSEMBLY_OK verdict=OPEN objdiff=missing\n' >&2
