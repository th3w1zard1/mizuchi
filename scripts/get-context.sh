#!/usr/bin/env bash
# Run global.getContextScript from mizuchi.yaml (m2ctx / type headers).
#
# Usage:
#   get-context.sh --prompt prompts/<name>/ [--output context/ctx.h] [--quiet]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib/prompt-settings.sh
. "$ROOT/scripts/lib/prompt-settings.sh"
# shellcheck source=scripts/lib/mizuchi-config.sh
. "$ROOT/scripts/lib/mizuchi-config.sh"
# shellcheck source=scripts/lib/check-log.sh
source "$ROOT/scripts/lib/check-log.sh"
# shellcheck source=scripts/lib/guide-manifest.sh
source "$ROOT/scripts/lib/guide-manifest.sh"

prompt_dir=""
output=""
quiet=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) prompt_dir="$2"; shift 2 ;;
    --output) output="$2"; shift 2 ;;
    --quiet) quiet=1; shift ;;
    -h|--help)
      cat <<EOF
usage: get-context.sh --prompt prompts/<name>/ [--output context/ctx.h] [--quiet]

Runs global.getContextScript from mizuchi.yaml to produce context headers.
Verbose logging is the default.

Examples:
  ./scripts/get-context.sh --prompt prompts/fun_00148020/
  ./scripts/get-context.sh --prompt prompts/fun_00148020/ --output context/ctx.h --quiet
EOF
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

CHECK_LOG_QUIET=$quiet
check_log_init "get-context"
guide_manifest_load "$ROOT"
guide_manifest_trace_defaults "$ROOT"

if [[ -z "$prompt_dir" ]]; then
  check_log_fail "missing --prompt"
  check_log_summary "GET_CONTEXT_FAIL"
  echo "Error: --prompt required" >&2
  echo "  ./scripts/get-context.sh --prompt prompts/fun_00148020/" >&2
  exit 2
fi

prompt_settings_require_dir "$prompt_dir" || exit $?

functionName="$(prompt_settings_get "$prompt_dir" functionName)"
targetObjectPath="$(prompt_settings_get "$prompt_dir" targetObjectPath)"
targetObjectPath="${targetObjectPath//\{\{functionName\}\}/$functionName}"

check_log_trace "prompt functionName=${functionName} targetObject=${targetObjectPath}"

if [[ -z "$output" ]]; then
  output="$(guide_default_context_path "$ROOT")"
fi

script="$(mizuchi_config_get global.getContextScript)" || {
  check_log_fail "global.getContextScript not set in mizuchi config"
  check_log_summary "GET_CONTEXT_FAIL"
  exit 1
}

mkdir -p "$(dirname "$output")"
log="$prompt_dir/build/get-context.log"
mkdir -p "$prompt_dir/build"
check_log_file_op "$(guide_manifest_rel "$ROOT" "$prompt_dir/build")" "ensure-dir"

output_existed=0
[[ -f "$output" ]] && output_existed=1
log_existed=0
[[ -f "$log" ]] && log_existed=1

check_log_run_cmd "getContextScript" "-> $(guide_manifest_rel "$ROOT" "$output")"
if ! bash -c "$(printf '%s' "$script" | mizuchi_expand_templates)" >"$output" 2>"$log"; then
  check_log_file_written "$log" "$ROOT" "$log_existed"
  check_log_fail "getContextScript failed (see $(guide_manifest_rel "$ROOT" "$log"))"
  tail -n 30 "$log" >&2 || true
  check_log_summary "GET_CONTEXT_FAIL"
  exit 1
fi

check_log_file_written "$log" "$ROOT" "$log_existed"
line_count="$(wc -l <"$output" | tr -d '[:space:]')"
if [[ ! -s "$output" ]]; then
  check_log_trace "warn  output file is empty: $(guide_manifest_rel "$ROOT" "$output")"
else
  check_log_file_written "$output" "$ROOT" "$output_existed"
fi

check_log_summary "GET_CONTEXT_OK"
echo "GET_CONTEXT_OK output=$(guide_manifest_rel "$ROOT" "$output") lines=${line_count}"
