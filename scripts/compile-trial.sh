#!/usr/bin/env bash
# Compile a candidate C file for a Mizuchi prompt folder and optionally run objdiff gate.
#
# Usage:
#   compile-trial.sh <prompts/<name>/> [candidate.c]
#
# If candidate.c is omitted, uses $dir/candidate.c
# Writes $dir/build/candidate.o (and logs under $dir/build/)
#
# Compiler: mizuchi.yaml global.compilerScript if present, else scripts/compile-placeholder.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib/prompt-settings.sh
. "$ROOT/scripts/lib/prompt-settings.sh"
# shellcheck source=scripts/lib/mizuchi-config.sh
. "$ROOT/scripts/lib/mizuchi-config.sh"

dir="${1:-}"
candidate="${2:-}"

if [[ -z "$dir" ]]; then
  echo "usage: $0 <prompts/<name>/> [candidate.c]" >&2
  exit 2
fi

prompt_settings_require_dir "$dir" || exit $?

function_name="$(prompt_settings_get "$dir" functionName)"
target_object="$(prompt_settings_get "$dir" targetObjectPath)"
target_object="${target_object//\{\{functionName\}\}/$function_name}"

if [[ -z "$candidate" ]]; then
  candidate="$dir/candidate.c"
fi
if [[ ! -f "$candidate" ]]; then
  echo "compile-trial: candidate C not found: $candidate" >&2
  exit 1
fi

build_dir="$dir/build"
mkdir -p "$build_dir"
obj_out="$build_dir/candidate.o"
log="$build_dir/compile.log"

compiler_script=""
mizuchi_cfg="$(mizuchi_config_resolve "$ROOT")"
compiler_script="$(mizuchi_config_get "$mizuchi_cfg" "global.compilerScript" || true)"

run_compiler() {
  local cfile="$1" ofile="$2"
  if [[ -n "$compiler_script" ]]; then
  expanded="$compiler_script"
  expanded="${expanded//\{\{cFilePath\}\}/$cfile}"
  expanded="${expanded//\{\{objFilePath\}\}/$ofile}"
  expanded="${expanded//\{\{functionName\}\}/$function_name}"
    bash -c "$expanded" >"$log" 2>&1
    return $?
  fi
  bash "$ROOT/scripts/compile-placeholder.sh" "$cfile" "$ofile" >"$log" 2>&1
}

echo "compile-trial: compiling $candidate -> $obj_out"
if ! run_compiler "$candidate" "$obj_out"; then
  echo "compile-trial: compile failed (see $log)" >&2
  tail -n 40 "$log" >&2 || true
  exit 1
fi

echo "compile-trial: OK $obj_out"

target_path="$target_object"
if [[ "$target_path" != /* ]]; then
  target_path="$ROOT/$target_path"
fi

if [[ ! -f "$target_path" ]]; then
  echo "compile-trial: target object missing — skip objdiff: $target_path" >&2
  echo "[OPEN] Wire golden .o at targetObjectPath to enable verification." >&2
  exit 0
fi

echo "compile-trial: objdiff $target_path vs $obj_out"
if "$ROOT/scripts/objdiff-gate.sh" "$target_path" "$obj_out"; then
  echo "compile-trial: objdiff reports 0 differences — match eligible"
  exit 0
fi

echo "compile-trial: objdiff reports differences — not matched" >&2
exit 1
