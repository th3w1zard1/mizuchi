#!/usr/bin/env bash
# One-shot matcher: builds prompt, invokes MATCHER_COMMAND, writes trial.c
# JSON on stdout; verbose trace on stderr (--quiet to suppress trace).
set -euo pipefail

root_dir="${MIZUCHI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=scripts/lib/matcher-prompt.sh
source "$root_dir/scripts/lib/matcher-prompt.sh"
# shellcheck source=scripts/lib/matcher-parse.sh
source "$root_dir/scripts/lib/matcher-parse.sh"
# shellcheck source=scripts/lib/check-log.sh
source "$root_dir/scripts/lib/check-log.sh"
# shellcheck source=scripts/lib/guide-manifest.sh
source "$root_dir/scripts/lib/guide-manifest.sh"
# shellcheck source=scripts/lib/cli-agent.sh
source "$root_dir/scripts/lib/cli-agent.sh"

usage() {
  cat <<EOF
Usage: matcher.sh --prompt <prompt-name> [--response-file <path>] [--quiet]

Env:
  MATCHER_COMMAND  Optional command for one-shot invocation (prompt on stdin).
                   stdout must contain a \`\`\`c block.

Options:
  --quiet   Suppress verbose trace (keep summary + JSON)
  -h, --help  Show help

Examples:
  MATCHER_MOCK_RESPONSE=fixtures/response.txt ./scripts/matcher.sh --prompt fun_00148020
  ./scripts/matcher.sh --prompt fun_00148020 --response-file /tmp/matcher-out.txt
EOF
}

prompt_name=""
response_file=""
quiet=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) prompt_name="${2:-}"; shift 2 ;;
    --response-file) response_file="${2:-}"; shift 2 ;;
    --quiet) quiet=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Error: unknown argument: $1" >&2; echo "  ./scripts/matcher.sh --help" >&2; exit 2 ;;
  esac
done

[[ "$quiet" -eq 1 ]] && check_log_configure --quiet
check_log_init "matcher"
guide_manifest_load "$root_dir"
guide_manifest_trace_defaults "$root_dir"

if [[ -z "$prompt_name" ]]; then
  cli_agent_missing_arg "matcher.sh" "missing --prompt" \
    "./scripts/matcher.sh --prompt fun_00148020"
fi

prompt_dir="$GUIDE_PROMPTS_DIR/$prompt_name"
mkdir -p "$prompt_dir"
check_log_file_op "$(guide_manifest_rel "$root_dir" "$prompt_dir")" "ensure-dir"

trial_file="$prompt_dir/trial.c"
trial_existed=0
[[ -f "$trial_file" ]] && trial_existed=1

tmp_prompt="$(mktemp)"
tmp_out="$(mktemp)"
tmp_err="$(mktemp)"
trap 'rm -f "$tmp_prompt" "$tmp_out" "$tmp_err"' EXIT

check_log_run_step "build matcher prompt"
matcher_build_prompt "$prompt_name" >"$tmp_prompt"
check_log_file_written "$tmp_prompt" "$root_dir" 0

if [[ -n "$response_file" ]]; then
  check_log_read_file "$response_file" "$(guide_manifest_rel "$root_dir" "$response_file")" "matcher response file"
  cp "$response_file" "$tmp_out"
  check_log_file_written "$tmp_out" "$root_dir" 0
elif [[ -n "${MATCHER_MOCK_RESPONSE:-}" && -f "${MATCHER_MOCK_RESPONSE:-}" ]]; then
  check_log_read_file "$MATCHER_MOCK_RESPONSE" "$(guide_manifest_rel "$root_dir" "$MATCHER_MOCK_RESPONSE")" "MATCHER_MOCK_RESPONSE"
  cp "$MATCHER_MOCK_RESPONSE" "$tmp_out"
  check_log_file_written "$tmp_out" "$root_dir" 0
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
    check_log_fail "matcher not configured (MATCHER_COMMAND)"
    check_log_summary "MATCHER_FAIL"
    echo "Matcher invocation not configured. Set MATCHER_COMMAND or provide --response-file/MATCHER_MOCK_RESPONSE." >&2
    exit 3
  fi

  check_log_trace "run   matcher: ${matcher_cmd}"
  set +e
  sh -lc "$matcher_cmd" <"$tmp_prompt" >"$tmp_out" 2>"$tmp_err"
  invoke_rc=$?
  set -e
  check_log_file_written "$tmp_out" "$root_dir" 0
  if [[ -s "$tmp_err" ]]; then
    check_log_file_appended "$tmp_err" "$root_dir" "matcher stderr"
  fi
  if [[ "$invoke_rc" -ne 0 ]]; then
    first_err="$(head -n 1 "$tmp_err" 2>/dev/null || true)"
    check_log_fail "matcher exited rc=$invoke_rc"
    check_log_summary "MATCHER_FAIL"
    echo "Matcher invocation failed (rc=$invoke_rc): ${first_err:-unknown error}" >&2
    exit 3
  fi
fi

code="$(matcher_extract_c_block "$tmp_out")"
if [[ -z "${code//[[:space:]]/}" ]]; then
  check_log_fail "no C block in matcher response"
  check_log_summary "MATCHER_FAIL"
  echo "Failed to parse C code block from matcher response" >&2
  exit 4
fi

printf '%s\n' "$code" >"$trial_file"
check_log_file_written "$trial_file" "$root_dir" "$trial_existed"

check_log_summary "MATCHER_OK"
echo "{\"status\":\"success\",\"prompt\":\"$prompt_name\",\"trial\":\"$trial_file\"}"
printf 'MATCHER_OK prompt=%s trial=%s\n' "$prompt_name" "$(guide_manifest_rel "$root_dir" "$trial_file")" >&2
