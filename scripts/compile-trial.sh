#!/usr/bin/env bash
# Compile a candidate C file for a Mizuchi prompt folder and optionally run objdiff gate.
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
Usage: compile-trial.sh <prompts/<name>/> [candidate.c] [--quiet]

Compiles candidate C to build/candidate.o and runs objdiff-gate when target .o exists.
Defaults: candidate.c in prompt dir; output build/candidate.o; compiler from mizuchi.yaml.

Options:
  --quiet    Suppress verbose trace (keep summary + result token)

Examples:
  ./scripts/compile-trial.sh prompts/fun_00148020/
  ./scripts/compile-trial.sh prompts/fun_00148020/ prompts/fun_00148020/build/m2c.c
  ./scripts/compile-trial.sh prompts/fun_00148020/ --quiet
EOF
}

dir=""
candidate=""
quiet=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet) quiet=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      if [[ -z "$dir" ]]; then
        dir="$1"
      elif [[ -z "$candidate" ]]; then
        candidate="$1"
      else
        cli_agent_missing_arg "compile-trial.sh" "unexpected argument: $1" "./scripts/compile-trial.sh prompts/fun_00148020/"
      fi
      shift
      ;;
  esac
done

CHECK_LOG_QUIET=$quiet
check_log_init "compile-trial"
guide_manifest_load "$ROOT"
guide_manifest_trace_defaults "$ROOT"

if [[ -z "$dir" ]]; then
  check_log_fail "missing prompt directory"
  check_log_summary "COMPILE_TRIAL_FAIL"
  cli_agent_missing_arg "compile-trial.sh" "missing prompt directory" "./scripts/compile-trial.sh prompts/fun_00148020/"
fi

prompt_settings_require_dir "$dir" || exit $?
dir="$(cd "$dir" && pwd)"
check_log_trace "prompt $(guide_manifest_rel "$ROOT" "$dir")"

function_name="$(prompt_settings_get "$dir" functionName)"
target_object="$(prompt_settings_get "$dir" targetObjectPath)"
target_object="${target_object//\{\{functionName\}\}/$function_name}"
check_log_trace "target functionName=${function_name} targetObject=${target_object}"

if [[ -z "$candidate" ]]; then
  candidate="$dir/candidate.c"
fi
check_log_read_file "$candidate" "$(guide_manifest_rel "$ROOT" "$candidate")" "candidate.c" || {
  check_log_summary "COMPILE_TRIAL_FAIL"
  exit 1
}

build_dir="$dir/${GUIDE_BUILD_DIR_NAME}"
mkdir -p "$build_dir"
check_log_file_op "$(guide_manifest_rel "$ROOT" "$build_dir")" "ensure-dir"

obj_out="$(guide_prompt_build_path "$dir" "candidate.o")"
log="$(guide_prompt_build_path "$dir" "compile.log")"
log_existed=0
[[ -f "$log" ]] && log_existed=1

compiler_script=""
mizuchi_cfg="$(mizuchi_config_resolve "$ROOT")"
compiler_script="$(mizuchi_config_get "$mizuchi_cfg" "global.compilerScript" || true)"
if [[ -n "$compiler_script" ]]; then
  check_log_trace "compiler mizuchi.yaml global.compilerScript"
else
  check_log_trace "compiler scripts/compile-placeholder.sh (default)"
fi

run_compiler() {
  local cfile="$1" ofile="$2"
  if [[ -n "$compiler_script" ]]; then
    local expanded="$compiler_script"
    expanded="${expanded//\{\{cFilePath\}\}/$cfile}"
    expanded="${expanded//\{\{objFilePath\}\}/$ofile}"
    expanded="${expanded//\{\{functionName\}\}/$function_name}"
    bash -c "$expanded" >"$log" 2>&1
    return $?
  fi
  bash "$ROOT/scripts/compile-placeholder.sh" "$cfile" "$ofile" >"$log" 2>&1
}

obj_existed=0
[[ -f "$obj_out" ]] && obj_existed=1

check_log_run_cmd "compile" "$(guide_manifest_rel "$ROOT" "$candidate") -> $(guide_manifest_rel "$ROOT" "$obj_out")"
if ! run_compiler "$candidate" "$obj_out"; then
  check_log_file_written "$log" "$ROOT" "$log_existed"
  check_log_fail "compile failed (see $(guide_manifest_rel "$ROOT" "$log"))"
  tail -n 40 "$log" >&2 || true
  check_log_summary "COMPILE_TRIAL_FAIL"
  exit 1
fi
check_log_file_written "$log" "$ROOT" "$log_existed"
check_log_file_written "$obj_out" "$ROOT" "$obj_existed"

target_path="$target_object"
if [[ "$target_path" != /* ]]; then
  target_path="$ROOT/$target_path"
fi

if [[ ! -f "$target_path" ]]; then
  check_log_trace "warn  target object missing — skip objdiff: $(guide_manifest_rel "$ROOT" "$target_path")"
  check_log_summary "COMPILE_TRIAL_OK"
  echo "COMPILE_TRIAL_OK obj=$(guide_manifest_rel "$ROOT" "$obj_out") objdiff=skipped"
  exit 0
fi

check_log_run_step "objdiff-gate"
gate_args=("$ROOT/scripts/objdiff-gate.sh" "$target_path" "$obj_out")
[[ "$quiet" -eq 1 ]] && gate_args+=(--quiet)
if "${gate_args[@]}"; then
  check_log_summary "COMPILE_TRIAL_OK"
  echo "COMPILE_TRIAL_OK obj=$(guide_manifest_rel "$ROOT" "$obj_out") objdiff=0"
  exit 0
fi

check_log_fail "objdiff reports differences"
check_log_summary "COMPILE_TRIAL_FAIL"
echo "COMPILE_TRIAL_FAIL obj=$(guide_manifest_rel "$ROOT" "$obj_out") objdiff=nonzero" >&2
exit 1
