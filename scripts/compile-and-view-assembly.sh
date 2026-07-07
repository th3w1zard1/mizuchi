#!/usr/bin/env bash
# Cursor-native analogue of Mizuchi compile_and_view_assembly MCP tool.
#
# Compiles candidate C (prepended with context), disassembles the .o, optionally
# runs objdiff against the golden target from settings.yaml.
#
# Usage:
#   compile-and-view-assembly.sh --prompt <prompts/<name>/> --code-file <path.c>
#   echo 'int x() { return 0; }' | compile-and-view-assembly.sh --prompt prompts/foo/ --code-stdin
#
# Options:
#   --context <file>   Override context header (default: context/ctx.h)
#   --max-insns N      Limit objdump lines (default 120)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib/prompt-settings.sh
. "$ROOT/scripts/lib/prompt-settings.sh"
# shellcheck source=scripts/lib/case-metadata.sh
. "$ROOT/scripts/lib/case-metadata.sh"

prompt_dir=""
code_file=""
code_stdin=0
context_file=""
max_insns=120

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) prompt_dir="$2"; shift 2 ;;
    --code-file) code_file="$2"; shift 2 ;;
    --code-stdin) code_stdin=1; shift ;;
    --context) context_file="$2"; shift 2 ;;
    --max-insns) max_insns="$2"; shift 2 ;;
    -h|--help)
      grep '^#' "$0" | head -n 12
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$prompt_dir" ]]; then
  echo "compile-and-view-assembly: --prompt required" >&2
  exit 2
fi
prompt_settings_require_dir "$prompt_dir" || exit $?
prompt_dir="$(cd "$prompt_dir" && pwd)"

function_name="$(prompt_settings_get "$prompt_dir" functionName)"
prompt_name="$(basename "$prompt_dir")"
case_status="$(case_metadata_get_default "$prompt_dir" status "")"
if [[ "$case_status" == "blocked" ]]; then
  blocked_reason="$(case_metadata_get_default "$prompt_dir" blockedReason "case.yaml status is blocked")"
  echo "compile-and-view-assembly: prompt is blocked: $blocked_reason" >&2
  exit 3
fi

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

if [[ "$code_stdin" -eq 1 ]]; then
  code_file="$tmpdir/submitted.c"
  cat >"$code_file"
elif [[ -z "$code_file" || ! -f "$code_file" ]]; then
  echo "compile-and-view-assembly: provide --code-file or --code-stdin" >&2
  exit 2
fi

if [[ -n "$context_file" ]]; then
  ctx="$context_file"
elif [[ -f "$ROOT/context/ctx.h" ]]; then
  ctx="$ROOT/context/ctx.h"
else
  ctx="$tmpdir/empty.h"
  echo "/* empty context */" >"$ctx"
fi

combined="$tmpdir/combined.c"
{
  echo "/* context + candidate for $function_name */"
  cat "$ctx"
  echo
  cat "$code_file"
} >"$combined"

obj_out="$prompt_dir/build/candidate.o"
report_json="$prompt_dir/build/build-and-verify.json"
rm -f "$obj_out" "$report_json"

set +e
"$ROOT/scripts/compile-trial.sh" "$prompt_dir" "$combined"
compile_rc=$?
set -e

if [[ "$compile_rc" -eq 3 ]]; then
  exit 3
fi
if [[ "$compile_rc" -ne 0 && ! -f "$obj_out" ]]; then
  echo "compile-and-view-assembly: compile failed" >&2
  exit 1
fi
if [[ "$compile_rc" -ne 0 && ! -f "$report_json" ]]; then
  echo "compile-and-view-assembly: verifier failed before writing report" >&2
  exit 1
fi

echo "=== disassembly: $function_name (candidate) ==="
if command -v objdump >/dev/null 2>&1; then
  objdump -d -M intel "$obj_out" | head -n "$max_insns"
else
  echo "(objdump not found — install binutils)" >&2
fi

echo
echo "=== objdiff summary ==="
if [[ -f "$report_json" ]]; then
  status="$(jq -r '.status // "unknown"' "$report_json")"
  method="$(jq -r '.method // "unknown"' "$report_json")"
  target_path="$(jq -r '.target_object // ""' "$report_json")"
  echo "method: $method"
  [[ -n "$target_path" ]] && echo "target_object: $target_path"
  echo "candidate_object: $obj_out"
  if [[ "$status" == "matched" ]]; then
    echo "diff_count: 0"
    echo "verdict: MATCH"
  else
    echo "diff_count: non-zero"
    echo "verdict: NOT_MATCHED"
  fi
else
  target_object="$(prompt_settings_get "$prompt_dir" targetObjectPath)"
  target_object="$(case_metadata_expand "$target_object" "$function_name" "$prompt_name")"
  target_path="$(case_metadata_resolve_path "$ROOT" "$prompt_dir" "$target_object")"
  echo "[OPEN] verifier report missing: $report_json"
  echo "target_object: $target_path"
  echo "candidate_object: $obj_out"
fi
