#!/usr/bin/env bash
set -euo pipefail

# MCP tool: list_prompts
# Returns JSON array of available prompt folders with metadata
# Optional filter: status=matched|in_progress|integrated|pending|blocked

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/check-log.sh
source "$ROOT/scripts/lib/check-log.sh"
# shellcheck source=scripts/lib/guide-manifest.sh
source "$ROOT/scripts/lib/guide-manifest.sh"
# shellcheck source=scripts/lib/prompt-metadata.sh
source "$ROOT/scripts/lib/prompt-metadata.sh"

quiet=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet) quiet=1; shift ;;
    -h|--help)
      cat <<EOF
Usage: list-prompts.sh [status=<filter>] [--quiet]

JSON output on stdout; verbose trace on stderr (default).

Examples:
  ./scripts/list-prompts.sh
  ./scripts/list-prompts.sh status=matched
  ./scripts/list-prompts.sh --quiet
EOF
      exit 0
      ;;
    *) break ;;
  esac
done

CHECK_LOG_QUIET=$quiet
check_log_init "list-prompts"
guide_manifest_load "$ROOT"
guide_manifest_trace_defaults "$ROOT"

# Build prompts array
build_prompts_array() {
  local prompts_dir="$GUIDE_PROMPTS_DIR"
  local prompts=()

  check_log_read_dir "$prompts_dir" "$(guide_manifest_rel "$ROOT" "$prompts_dir")" "prompts root" || {
    echo "[]"
    return
  }
  
  for prompt_dir in "$prompts_dir"/*; do
    # Skip non-directories and _template
    if [[ ! -d "$prompt_dir" ]]; then
      continue
    fi
    
    local prompt_name
    prompt_name=$(basename "$prompt_dir")
    
    if [[ "$prompt_name" == "_template" ]]; then
      continue
    fi
    
    local metadata
    metadata="$(prompt_metadata_summary_json "$prompt_dir")"

    local status
    status="$(jq -r '.status' <<<"$metadata")"
    check_log_trace "read  prompt/${prompt_name} status=${status}"

    local last_updated
    last_updated="$(prompt_metadata_last_updated_date "$prompt_dir")"
    
    local item
    item=$(jq -n \
      --arg name "$prompt_name" \
      --arg updated "$last_updated" \
      --argjson metadata "$metadata" \
      '$metadata + {name: $name, last_updated: $updated}')
    
    prompts+=("$item")
  done
  
  # Output as JSON array
  if [[ ${#prompts[@]} -eq 0 ]]; then
    echo "[]"
  else
    printf '%s\n' "${prompts[@]}" | jq -s '.'
  fi
}

# Filter prompts by status
filter_by_status() {
  local prompts_json="$1"
  local status_filter="$2"
  
  if [[ -z "$status_filter" ]]; then
    echo "$prompts_json"
    return
  fi
  
  echo "$prompts_json" | jq "[.[] | select(.status == \"$status_filter\")]"
}

validate_status_filter() {
  local status_filter="$1"
  [[ -z "$status_filter" ]] && return 0

  case "$status_filter" in
    matched|in_progress|integrated|pending|blocked) return 0 ;;
    *)
      jq -n --arg status "$status_filter" \
        '{error: ("Invalid status filter: " + $status), valid_statuses: ["matched","in_progress","integrated","pending","blocked"]}'
      return 1
      ;;
  esac
}

# Parse arguments
parse_arguments() {
  local status_filter=""
  
  # Simple argument parsing for status=value format
  for arg in "$@"; do
    if [[ "$arg" =~ ^status=(.+)$ ]]; then
      status_filter="${BASH_REMATCH[1]}"
    fi
  done
  
  echo "$status_filter"
}

# Main: assemble JSON response
main() {
  local status_filter
  status_filter=$(parse_arguments "$@")
  validate_status_filter "$status_filter" || exit 1
  
  local prompts_array
  prompts_array=$(build_prompts_array)
  
  local filtered_prompts
  filtered_prompts=$(filter_by_status "$prompts_array" "$status_filter")

  local count
  count="$(echo "$filtered_prompts" | jq 'length')"
  check_log_trace "ok    listed ${count} prompt(s)"
  check_log_summary "LIST_PROMPTS_OK"

  jq -n --argjson prompts "$filtered_prompts" '{prompts: $prompts}'
}

main "$@"
