#!/usr/bin/env bash
# Run m2c on assembly from settings.yaml; writes candidate C for programmatic phase.
#
# Requires ReconstructKit vendor/m2c or env M2C_DIR + M2C_PYTHON.
#
# Usage:
#   run-m2c.sh --prompt prompts/<name>/ [--output prompts/<name>/build/m2c.c]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib/prompt-settings.sh
. "$ROOT/scripts/lib/prompt-settings.sh"
# shellcheck source=scripts/lib/reconkit-config.sh
. "$ROOT/scripts/lib/reconkit-config.sh"

prompt_dir=""
output=""
context_file=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) prompt_dir="$2"; shift 2 ;;
    --output) output="$2"; shift 2 ;;
    --context) context_file="$2"; shift 2 ;;
    -h|--help)
      grep '^#' "$0" | head -n 10
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$prompt_dir" ]]; then
  echo "run-m2c: --prompt required" >&2
  exit 2
fi

prompt_settings_require_dir "$prompt_dir" || exit $?

enable="$(recovery_config_get plugins.m2c.enable optional)"
if [[ "$enable" == "false" ]]; then
  echo "run-m2c: plugins.m2c.enable is false — skip" >&2
  exit 3
fi

functionName="$(prompt_settings_get "$prompt_dir" functionName)"
asm="$(prompt_settings_get "$prompt_dir" asm)"

if [[ -z "$output" ]]; then
  output="$prompt_dir/build/m2c.c"
fi
mkdir -p "$(dirname "$output")"

if [[ -z "$context_file" ]]; then
  if [[ -f "$ROOT/context/ctx.h" ]]; then
    context_file="$ROOT/context/ctx.h"
  fi
fi

platform="$(recovery_config_get global.target optional)"
m2c_target="${M2C_TARGET:-}"
if [[ -z "$m2c_target" ]]; then
  m2c_target="$(recovery_m2c_target_for_platform "$platform")"
fi

if [[ -z "$m2c_target" ]]; then
  echo "run-m2c: no m2c target for platform '$platform' (set M2C_TARGET or use MIPS/PPC/ARM project)" >&2
  echo "[OPEN] x86/win32 matching decompilation typically skips m2c; use AI phase or hand-written C." >&2
  exit 4
fi

M2C_DIR="${M2C_DIR:-}"
if [[ -z "$M2C_DIR" && -d "$ROOT/vendor/m2c" ]]; then
  M2C_DIR="$ROOT/vendor/m2c"
fi
if [[ -z "$M2C_DIR" ]]; then
  root_hint="$(reconkit_root_dir)"
  if [[ -n "$root_hint" && -d "$root_hint/vendor/m2c" ]]; then
    M2C_DIR="$root_hint/vendor/m2c"
  fi
fi

if [[ -z "$M2C_DIR" || ! -f "$M2C_DIR/m2c.py" ]]; then
  echo "run-m2c: m2c not found. Set M2C_DIR to ReconstructKit vendor/m2c or clone github.com/macabeus/reconkit" >&2
  exit 1
fi

M2C_PYTHON="${M2C_PYTHON:-$M2C_DIR/.venv/bin/python}"
if [[ ! -x "$M2C_PYTHON" ]]; then
  M2C_PYTHON="${M2C_PYTHON_FALLBACK:-python3}"
fi

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
asm_file="$tmpdir/${functionName}.s"
printf '%s\n' "$asm" >"$asm_file"

args=("$M2C_DIR/m2c.py" "$asm_file" --target "$m2c_target" --function "$functionName" --globals none)
if [[ -n "$context_file" && -f "$context_file" ]]; then
  args+=(--context "$context_file")
fi

log="$prompt_dir/build/m2c.log"
echo "run-m2c: $M2C_PYTHON ${args[*]}" >"$log"
if ! "$M2C_PYTHON" "${args[@]}" >"$output" 2>>"$log"; then
  echo "run-m2c: m2c failed (see $log)" >&2
  tail -n 40 "$log" >&2 || true
  exit 1
fi

if [[ ! -s "$output" ]]; then
  echo "run-m2c: m2c produced empty output" >&2
  exit 1
fi

echo "run-m2c: wrote $(wc -l <"$output" | tr -d ' ') lines to $output"
