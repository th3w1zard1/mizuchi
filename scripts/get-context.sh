#!/usr/bin/env bash
# Run global.getContextScript from mizuchi.yaml (m2ctx / type headers).
#
# Usage:
#   get-context.sh --prompt prompts/<name>/ [--output context/ctx.h]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/lib/prompt-settings.sh
. "$ROOT/scripts/lib/prompt-settings.sh"
# shellcheck source=scripts/lib/mizuchi-config.sh
. "$ROOT/scripts/lib/mizuchi-config.sh"

prompt_dir=""
output=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) prompt_dir="$2"; shift 2 ;;
    --output) output="$2"; shift 2 ;;
    -h|--help)
      grep '^#' "$0" | head -n 8
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$prompt_dir" ]]; then
  echo "get-context: --prompt required" >&2
  exit 2
fi

prompt_settings_require_dir "$prompt_dir" || exit $?

functionName="$(prompt_settings_get "$prompt_dir" functionName)"
targetObjectPath="$(prompt_settings_get "$prompt_dir" targetObjectPath)"
targetObjectPath="${targetObjectPath//\{\{functionName\}\}/$functionName}"

if [[ -z "$output" ]]; then
  output="$ROOT/context/ctx.h"
fi

script="$(mizuchi_config_get global.getContextScript)" || {
  echo "get-context: global.getContextScript not set in mizuchi config" >&2
  exit 1
}

mkdir -p "$(dirname "$output")"
log="$prompt_dir/build/get-context.log"
mkdir -p "$prompt_dir/build"

echo "get-context: running getContextScript -> $output"
if ! bash -c "$(printf '%s' "$script" | mizuchi_expand_templates)" >"$output" 2>"$log"; then
  echo "get-context: script failed (see $log)" >&2
  tail -n 30 "$log" >&2 || true
  exit 1
fi

if [[ ! -s "$output" ]]; then
  echo "get-context: warning — output file is empty: $output" >&2
fi

echo "get-context: wrote $(wc -l <"$output" | tr -d ' ') lines to $output"
