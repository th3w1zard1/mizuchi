#!/usr/bin/env bash
set -euo pipefail

root_dir="${MIZUCHI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# shellcheck source=scripts/lib/matcher-prompt.sh
source "$root_dir/scripts/lib/matcher-prompt.sh"
# shellcheck source=scripts/lib/matcher-parse.sh
source "$root_dir/scripts/lib/matcher-parse.sh"

usage() {
  cat <<EOF
Usage: $0 --prompt <prompt-name> [--response-file <path>]
EOF
}

prompt_name=""
response_file=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) prompt_name="${2:-}"; shift 2 ;;
    --response-file) response_file="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$prompt_name" ]]; then
  usage
  exit 2
fi

prompt_dir="$root_dir/prompts/$prompt_name"
mkdir -p "$prompt_dir"
trial_file="$prompt_dir/trial.c"

tmp_prompt="$(mktemp)"
tmp_out="$(mktemp)"
trap 'rm -f "$tmp_prompt" "$tmp_out"' EXIT
matcher_build_prompt "$prompt_name" >"$tmp_prompt"

if [[ -n "$response_file" ]]; then
  cp "$response_file" "$tmp_out"
elif [[ -n "${MATCHER_MOCK_RESPONSE:-}" && -f "${MATCHER_MOCK_RESPONSE:-}" ]]; then
  cp "$MATCHER_MOCK_RESPONSE" "$tmp_out"
else
  echo "Matcher invocation not configured. Set --response-file or MATCHER_MOCK_RESPONSE." >&2
  exit 3
fi

code="$(matcher_extract_c_block "$tmp_out")"
if [[ -z "${code//[[:space:]]/}" ]]; then
  echo "Failed to parse C code block from matcher response" >&2
  exit 4
fi

printf '%s\n' "$code" >"$trial_file"
echo "{\"status\":\"success\",\"prompt\":\"$prompt_name\",\"trial\":\"$trial_file\"}"
