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

Env:
  MATCHER_COMMAND  Optional command used for real one-shot invocation.
                   The prompt is piped to stdin and stdout must contain a ```c block.
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
tmp_err="$(mktemp)"
trap 'rm -f "$tmp_prompt" "$tmp_out" "$tmp_err"' EXIT
matcher_build_prompt "$prompt_name" >"$tmp_prompt"

if [[ -n "$response_file" ]]; then
  cp "$response_file" "$tmp_out"
elif [[ -n "${MATCHER_MOCK_RESPONSE:-}" && -f "${MATCHER_MOCK_RESPONSE:-}" ]]; then
  cp "$MATCHER_MOCK_RESPONSE" "$tmp_out"
else
  matcher_cmd="${MATCHER_COMMAND:-}"
  if [[ -z "$matcher_cmd" ]]; then
    if command -v claude >/dev/null 2>&1; then
      matcher_cmd="claude --print"
    elif command -v codex >/dev/null 2>&1; then
      matcher_cmd="codex exec -"
    fi
  fi

  if [[ -z "$matcher_cmd" ]]; then
    echo "Matcher invocation not configured. Set MATCHER_COMMAND or provide --response-file/MATCHER_MOCK_RESPONSE." >&2
    exit 3
  fi

  set +e
  sh -lc "$matcher_cmd" <"$tmp_prompt" >"$tmp_out" 2>"$tmp_err"
  invoke_rc=$?
  set -e
  if [[ "$invoke_rc" -ne 0 ]]; then
    first_err="$(head -n 1 "$tmp_err" 2>/dev/null || true)"
    echo "Matcher invocation failed (rc=$invoke_rc): ${first_err:-unknown error}" >&2
    exit 3
  fi
fi

code="$(matcher_extract_c_block "$tmp_out")"
if [[ -z "${code//[[:space:]]/}" ]]; then
  echo "Failed to parse C code block from matcher response" >&2
  exit 4
fi

printf '%s\n' "$code" >"$trial_file"
echo "{\"status\":\"success\",\"prompt\":\"$prompt_name\",\"trial\":\"$trial_file\"}"
