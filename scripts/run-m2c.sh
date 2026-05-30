#!/usr/bin/env bash
# Run m2c on assembly from settings.yaml; writes candidate C for programmatic phase.
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
# shellcheck source=scripts/lib/cli-agent.sh
source "$ROOT/scripts/lib/cli-agent.sh"

usage() {
  cat <<EOF
Usage: run-m2c.sh --prompt <prompt-dir> [options]

Runs m2c on asm from settings.yaml. Defaults use guide paths (build/m2c.c, context/ctx.h).

Options:
  --output <path>    Output C file (default: <prompt>/build/m2c.c)
  --context <path>   m2c context header (default: context/ctx.h if present)
  --quiet            Suppress verbose trace (keep summary + result token)
  -h, --help         Show help

Examples:
  ./scripts/run-m2c.sh --prompt prompts/fun_00148020/
  ./scripts/run-m2c.sh --prompt prompts/fun_00148020/ --output prompts/fun_00148020/build/m2c.c --quiet

Exit codes:
  0  m2c wrote output
  1  m2c not installed or run failed
  2  usage / missing --prompt
  3  plugins.m2c.enable is false
  4  no m2c target for platform (e.g. x86)
EOF
}

prompt_dir=""
output=""
context_file=""
quiet=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) prompt_dir="$2"; shift 2 ;;
    --output) output="$2"; shift 2 ;;
    --context) context_file="$2"; shift 2 ;;
    --quiet) quiet=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      cli_agent_missing_arg "run-m2c.sh" "unknown argument: $1" "./scripts/run-m2c.sh --prompt prompts/fun_00148020/"
      ;;
  esac
done

CHECK_LOG_QUIET=$quiet
check_log_init "run-m2c"
guide_manifest_load "$ROOT"
guide_manifest_trace_defaults "$ROOT"

if [[ -z "$prompt_dir" ]]; then
  check_log_fail "missing --prompt"
  check_log_summary "RUN_M2C_FAIL"
  cli_agent_missing_arg "run-m2c.sh" "missing --prompt" "./scripts/run-m2c.sh --prompt prompts/fun_00148020/"
fi

prompt_settings_require_dir "$prompt_dir" || exit $?
prompt_dir="$(cd "$prompt_dir" && pwd)"

check_log_trace "prompt $(guide_manifest_rel "$ROOT" "$prompt_dir")"

enable="$(mizuchi_config_get plugins.m2c.enable optional)"
if [[ "$enable" == "false" ]]; then
  check_log_fail "plugins.m2c.enable is false"
  check_log_summary "RUN_M2C_SKIP"
  echo "RUN_M2C_SKIP reason=m2c_disabled" >&2
  exit 3
fi

functionName="$(prompt_settings_get "$prompt_dir" functionName)"
asm="$(prompt_settings_get "$prompt_dir" asm)"
check_log_trace "target functionName=${functionName}"

if [[ -z "$output" ]]; then
  output="$(guide_prompt_build_path "$prompt_dir" "m2c.c")"
fi

build_dir="$(dirname "$output")"
mkdir -p "$build_dir"
check_log_file_op "$(guide_manifest_rel "$ROOT" "$build_dir")" "ensure-dir"

if [[ -z "$context_file" ]]; then
  default_ctx="$(guide_default_context_path "$ROOT")"
  if [[ -f "$default_ctx" ]]; then
    context_file="$default_ctx"
  fi
fi

if [[ -n "$context_file" && -f "$context_file" ]]; then
  check_log_read_file "$context_file" "$(guide_manifest_rel "$ROOT" "$context_file")" "m2c context"
fi

platform="$(mizuchi_config_get global.target optional)"
m2c_target="${M2C_TARGET:-}"
if [[ -z "$m2c_target" ]]; then
  m2c_target="$(mizuchi_m2c_target_for_platform "$platform")"
fi

if [[ -z "$m2c_target" ]]; then
  check_log_fail "no m2c target for platform '${platform}'"
  check_log_summary "RUN_M2C_FAIL"
  echo "RUN_M2C_FAIL reason=no_m2c_target platform=${platform}" >&2
  exit 4
fi
check_log_trace "m2c   target=${m2c_target} platform=${platform}"

M2C_DIR="${M2C_DIR:-}"
if [[ -z "$M2C_DIR" && -d "$ROOT/vendor/m2c" ]]; then
  M2C_DIR="$ROOT/vendor/m2c"
fi
if [[ -z "$M2C_DIR" && -n "${MIZUCHI_ROOT:-}" && -d "$MIZUCHI_ROOT/vendor/m2c" ]]; then
  M2C_DIR="$MIZUCHI_ROOT/vendor/m2c"
fi

if [[ -z "$M2C_DIR" || ! -f "$M2C_DIR/m2c.py" ]]; then
  check_log_fail "m2c not found (set M2C_DIR or vendor/m2c)"
  check_log_summary "RUN_M2C_FAIL"
  exit 1
fi
check_log_read_file "$M2C_DIR/m2c.py" "$(guide_manifest_rel "$ROOT" "$M2C_DIR/m2c.py")" "m2c.py"

M2C_PYTHON="${M2C_PYTHON:-$M2C_DIR/.venv/bin/python}"
if [[ ! -x "$M2C_PYTHON" ]]; then
  M2C_PYTHON="${M2C_PYTHON_FALLBACK:-python3}"
fi
check_log_trace "m2c   python=${M2C_PYTHON} dir=$(guide_manifest_rel "$ROOT" "$M2C_DIR")"

tmpdir="$(mktemp -d)"
trap 'check_log_trace "io    removed temp/ (m2c asm dir)"; rm -rf "$tmpdir"' EXIT
asm_file="$tmpdir/${functionName}.s"
printf '%s\n' "$asm" >"$asm_file"
check_log_trace "io    wrote temp/${functionName}.s (m2c input)"

args=("$M2C_DIR/m2c.py" "$asm_file" --target "$m2c_target" --function "$functionName" --globals none)
if [[ -n "$context_file" && -f "$context_file" ]]; then
  args+=(--context "$context_file")
fi

log="$(guide_prompt_build_path "$prompt_dir" "m2c.log")"
log_existed=0
[[ -f "$log" ]] && log_existed=1
: >"$log"
check_log_file_written "$log" "$ROOT" "$log_existed"

output_existed=0
[[ -f "$output" ]] && output_existed=1

check_log_run_cmd "m2c" "$M2C_PYTHON ${args[*]}"
if ! "$M2C_PYTHON" "${args[@]}" >"$output" 2>>"$log"; then
  check_log_file_appended "$log" "$ROOT" "stderr from m2c"
  check_log_fail "m2c failed (see $(guide_manifest_rel "$ROOT" "$log"))"
  tail -n 40 "$log" >&2 || true
  check_log_summary "RUN_M2C_FAIL"
  exit 1
fi
check_log_file_appended "$log" "$ROOT" "m2c stderr"

if [[ ! -s "$output" ]]; then
  check_log_fail "m2c produced empty output"
  check_log_summary "RUN_M2C_FAIL"
  exit 1
fi

check_log_file_written "$output" "$ROOT" "$output_existed"
line_count="$(wc -l <"$output" | tr -d '[:space:]')"
check_log_summary "RUN_M2C_OK"
echo "RUN_M2C_OK output=$(guide_manifest_rel "$ROOT" "$output") lines=${line_count}"
