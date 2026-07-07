#!/usr/bin/env bash
# One-shot matcher entrypoint: build fixed prompt, run a headless response source, parse trial.c.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/case-metadata.sh
. "$ROOT/scripts/lib/case-metadata.sh"
. "$ROOT/scripts/lib/reconkit-config.sh"

usage() {
  cat >&2 <<'EOF'
usage: matcher.sh --prompt <prompt-dir|name> [--prompts-dir <dir>] [--response-file <file>] [--runner-command <cmd>]

Builds a fixed one-shot prompt and writes <prompt>/trial.c from a fenced C
response. Without --response-file, set --runner-command or RECONKIT_MATCHER_COMMAND.
The command may contain {{promptFile}} and {{responseFile}} placeholders.
EOF
}

prompt_arg=""
prompts_dir="$(reconkit_default_prompts_dir "$ROOT")"
response_file=""
runner_command="$(reconkit_matcher_command)"
prompt_out=""
trial_out=""
result_out=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) prompt_arg="$2"; shift 2 ;;
    --prompts-dir) prompts_dir="$2"; shift 2 ;;
    --response-file) response_file="$2"; shift 2 ;;
    --runner-command) runner_command="$2"; shift 2 ;;
    --prompt-out) prompt_out="$2"; shift 2 ;;
    --trial-out) trial_out="$2"; shift 2 ;;
    --result-out) result_out="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "matcher: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -z "$prompt_arg" ]] && { usage; exit 2; }
if [[ -d "$prompt_arg" ]]; then
  prompt_dir="$(cd "$prompt_arg" && pwd)"
else
  prompt_dir="$(cd "$prompts_dir/$prompt_arg" && pwd)"
fi
prompt_name="$(basename "$prompt_dir")"
build_dir="$prompt_dir/build"
mkdir -p "$build_dir"

prompt_out="${prompt_out:-$build_dir/matcher-prompt.md}"
trial_out="${trial_out:-$prompt_dir/trial.c}"
result_out="${result_out:-$build_dir/matcher.json}"
raw_response="$build_dir/matcher-response.txt"
parse_report="$build_dir/matcher-parse.json"

write_result() {
  local status="$1" exit_code="$2" reason="${3:-}" runner="${4:-}"
  local trial_exists=false
  [[ -f "$trial_out" ]] && trial_exists=true
  jq -n \
    --arg schema "reconkit.matcher.v1" \
    --arg status "$status" \
    --arg prompt "$prompt_name" \
    --arg prompt_dir "$prompt_dir" \
    --arg prompt_file "$prompt_out" \
    --arg raw_response "$raw_response" \
    --arg parse_report "$parse_report" \
    --arg trial "$trial_out" \
    --arg reason "$reason" \
    --arg runner "$runner" \
    --argjson exit_code "$exit_code" \
    --argjson trial_exists "$trial_exists" \
    '{
      schema: $schema,
      status: $status,
      prompt: $prompt,
      promptDir: $prompt_dir,
      promptFile: $prompt_file,
      rawResponse: $raw_response,
      parseReport: $parse_report,
      trialSource: $trial,
      trialSourcePresent: $trial_exists,
      runner: (if $runner == "" then null else $runner end),
      reason: (if $reason == "" then null else $reason end),
      exitCode: $exit_code
    }' >"$result_out"
}

case_status="$(case_metadata_get_default "$prompt_dir" status "")"
if [[ "$case_status" == "blocked" ]]; then
  blocked_reason="$(case_metadata_get_default "$prompt_dir" blockedReason "case.yaml status is blocked")"
  "$ROOT/scripts/lib/matcher-prompt.sh" --prompt "$prompt_dir" --out "$prompt_out" >/dev/null || true
  write_result "blocked" 3 "$blocked_reason" ""
  echo "matcher: prompt is blocked: $blocked_reason" >&2
  exit 3
fi

"$ROOT/scripts/lib/matcher-prompt.sh" --prompt "$prompt_dir" --out "$prompt_out" >/dev/null

runner_label=""
if [[ -n "$response_file" ]]; then
  if [[ ! -f "$response_file" ]]; then
    write_result "empty_response" 1 "response file not found: $response_file" "response-file"
    echo "matcher: response file not found: $response_file" >&2
    exit 1
  fi
  cp "$response_file" "$raw_response"
  runner_label="response-file"
elif [[ -n "$runner_command" ]]; then
  runner_label="runner-command"
  command_text="$runner_command"
  command_text="${command_text//\{\{promptFile\}\}/$prompt_out}"
  command_text="${command_text//\{\{responseFile\}\}/$raw_response}"
  set +e
  (cd "$ROOT" && bash -c "$command_text")
  runner_rc=$?
  set -e
  if [[ "$runner_rc" -ne 0 ]]; then
    write_result "runner_failed" "$runner_rc" "runner command failed" "$runner_label"
    echo "matcher: runner command failed with exit $runner_rc" >&2
    exit "$runner_rc"
  fi
  if [[ ! -f "$raw_response" ]]; then
    write_result "empty_response" 1 "runner did not write response file" "$runner_label"
    echo "matcher: runner did not write response file: $raw_response" >&2
    exit 1
  fi
else
  write_result "manual-required" 3 "no matcher runner configured" ""
  cat >&2 <<EOF
matcher: no matcher runner configured.
	Set RECONKIT_MATCHER_COMMAND or pass --runner-command with {{promptFile}} and {{responseFile}},
or pass --response-file for an offline one-shot response.
Prompt written to: $prompt_out
EOF
  exit 3
fi

set +e
"$ROOT/scripts/lib/matcher-parse.sh" --input "$raw_response" --out "$trial_out" --json "$parse_report"
parse_rc=$?
set -e
if [[ "$parse_rc" -ne 0 ]]; then
  parse_status="$(jq -r '.status // "parse_error"' "$parse_report" 2>/dev/null || printf 'parse_error')"
  parse_reason="$(jq -r '.reason // "failed to parse response"' "$parse_report" 2>/dev/null || printf 'failed to parse response')"
  write_result "$parse_status" 1 "$parse_reason" "$runner_label"
  exit 1
fi

write_result "success" 0 "" "$runner_label"
cat "$result_out"
