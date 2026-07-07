#!/usr/bin/env bash
set -euo pipefail

# MCP tool: list_prompts
# Returns JSON array of available prompt folders with metadata
# Optional filter: status=matched|in_progress|integrated|pending|blocked

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$root_dir/scripts/lib/case-metadata.sh"
. "$root_dir/scripts/lib/prompt-settings.sh"
READINESS_CACHE_KEY=""
READINESS_CACHE_VALUE=""

normalize_prompt_status() {
  case "$1" in
    pending|matched|in_progress|in-progress|integrated|blocked)
      printf '%s' "${1//-/_}"
      ;;
    *)
      return 1
      ;;
  esac
}

# Helper: extract status from canonical metadata, then legacy notes.md.
get_prompt_status() {
  local prompt_dir="$1"
  local notes_file="$prompt_dir/notes.md"

  local status
  if status="$(case_metadata_get "$prompt_dir" status 2>/dev/null)" && normalize_prompt_status "$status" >/dev/null; then
    normalize_prompt_status "$status"
    return
  fi

  if status="$(prompt_settings_get "$prompt_dir" status 2>/dev/null)" && normalize_prompt_status "$status" >/dev/null; then
    normalize_prompt_status "$status"
    return
  fi

  if [[ -f "$notes_file" ]]; then
    if grep -qi "status.*blocked" "$notes_file" 2>/dev/null; then
      echo "blocked"
      return
    elif grep -qi "status.*integrated" "$notes_file" 2>/dev/null; then
      echo "integrated"
      return
    elif grep -qi "status.*matched" "$notes_file" 2>/dev/null; then
      echo "matched"
      return
    elif grep -qi "status.*in_progress\|status.*in-progress" "$notes_file" 2>/dev/null; then
      echo "in_progress"
      return
    fi
  fi

  echo "pending"
}

get_blocked_reason() {
  local prompt_dir="$1"
  case_metadata_get "$prompt_dir" blockedReason 2>/dev/null || true
}

get_case_field() {
  local prompt_dir="$1" field="$2"
  case_metadata_get "$prompt_dir" "$field" 2>/dev/null || true
}

get_readiness_summary() {
  local prompts_dir="${1:-${MIZUCHI_PROMPTS_DIR:-$root_dir/prompts}}"
  if [[ "$READINESS_CACHE_KEY" == "$prompts_dir" && -n "$READINESS_CACHE_VALUE" ]]; then
    printf '%s' "$READINESS_CACHE_VALUE"
    return
  fi
  local summary
  set +e
  summary="$("$root_dir/scripts/decomp-readiness.sh" --all --prompts-dir "$prompts_dir" 2>/dev/null)"
  local rc=$?
  set -e
  if [[ "$rc" -eq 0 || "$rc" -eq 1 ]]; then
    READINESS_CACHE_KEY="$prompts_dir"
    READINESS_CACHE_VALUE="$summary"
    printf '%s' "$READINESS_CACHE_VALUE"
  else
    READINESS_CACHE_KEY="$prompts_dir"
    READINESS_CACHE_VALUE="$(jq -n '{schema: "mizuchi.decomp-readiness-summary.v1", status: "error", prompts: []}')"
    printf '%s' "$READINESS_CACHE_VALUE"
  fi
}

# Helper: extract function name from prompt metadata
get_function_name() {
  local prompt_dir="$1"
  local prompt_name="$2"
  
  # Default: derive from folder name (e.g., fun_00148020 -> FUN_00148020)
  local function_name
  function_name=$(echo "$prompt_name" | sed 's/fun_/FUN_/' | tr '[:lower:]' '[:upper:]')
  
  local from_case
  from_case="$(case_metadata_get "$prompt_dir" functionName 2>/dev/null || true)"
  if [[ -n "$from_case" ]]; then
    echo "$from_case"
    return
  fi

  local from_settings
  from_settings="$(prompt_settings_get "$prompt_dir" functionName 2>/dev/null || true)"
  if [[ -n "$from_settings" ]]; then
    echo "$from_settings"
    return
  fi

  # Try to refine from prompt.md if available.
  if [[ -f "$prompt_dir/prompt.md" ]]; then
    local from_md
    from_md=$(grep -oP 'Decompile `\K[^`]+' "$prompt_dir/prompt.md" | head -1 || echo "")
    if [[ -n "$from_md" ]]; then
      function_name="$from_md"
    fi
  fi
  
  echo "$function_name"
}

# Helper: format last_updated timestamp as ISO date
get_last_updated() {
  local prompt_dir="$1"
  
  # Get modification time of the prompt directory
  local mtime
  mtime=$(stat -c %Y "$prompt_dir" 2>/dev/null || echo "0")
  
  # Convert to ISO date format (YYYY-MM-DD)
  if [[ "$mtime" != "0" ]]; then
    date -u -d "@$mtime" +"%Y-%m-%d" 2>/dev/null || echo ""
  else
    echo ""
  fi
}

# Build prompts array
build_prompts_array() {
  local prompts_dir="${MIZUCHI_PROMPTS_DIR:-$root_dir/prompts}"
  local readiness_summary="${1:-}"
  local prompts=()
  if [[ -z "$readiness_summary" ]]; then
    readiness_summary="$(get_readiness_summary "$prompts_dir")"
  fi
  
  if [[ ! -d "$prompts_dir" ]]; then
    echo "[]"
    return
  fi
  
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
    
    local status
    status=$(get_prompt_status "$prompt_dir")

    local blocked_reason
    blocked_reason=$(get_blocked_reason "$prompt_dir")

    local integrated_source_path integration_receipt_path integrated_at
    integrated_source_path="$(get_case_field "$prompt_dir" integratedSourcePath)"
    integration_receipt_path="$(get_case_field "$prompt_dir" integrationReceiptPath)"
    integrated_at="$(get_case_field "$prompt_dir" integratedAt)"

    local readiness_json readiness_status readiness_blockers readiness_warnings
    readiness_json="$(jq -c --arg name "$prompt_name" '.prompts[]? | select(.prompt == $name)' <<<"$readiness_summary")"
    if [[ -n "$readiness_json" ]]; then
      readiness_status="$(jq -r '.status // "unknown"' <<<"$readiness_json")"
      readiness_blockers="$(jq -c '.blockers // []' <<<"$readiness_json")"
      readiness_warnings="$(jq -c '.warnings // []' <<<"$readiness_json")"
    else
      readiness_status="unknown"
      readiness_blockers="[]"
      readiness_warnings="[]"
    fi
    
    local function_name
    function_name=$(get_function_name "$prompt_dir" "$prompt_name")
    
    local last_updated
    last_updated=$(get_last_updated "$prompt_dir")
    
    local item
    item=$(jq -n \
      --arg name "$prompt_name" \
      --arg status "$status" \
      --arg func "$function_name" \
      --arg updated "$last_updated" \
      --arg blocked_reason "$blocked_reason" \
      --arg integrated_source_path "$integrated_source_path" \
      --arg integration_receipt_path "$integration_receipt_path" \
      --arg integrated_at "$integrated_at" \
      --arg readiness_status "$readiness_status" \
      --argjson readiness_blockers "$readiness_blockers" \
      --argjson readiness_warnings "$readiness_warnings" \
      '{
        name: $name,
        status: $status,
        function_name: $func,
        last_updated: $updated,
        readiness_status: $readiness_status,
        readiness_blockers: $readiness_blockers,
        readiness_warnings: $readiness_warnings,
        blocked_reason: (if $blocked_reason == "" then null else $blocked_reason end),
        integrated_source_path: (if $integrated_source_path == "" then null else $integrated_source_path end),
        integration_receipt_path: (if $integration_receipt_path == "" then null else $integration_receipt_path end),
        integrated_at: (if $integrated_at == "" then null else $integrated_at end)
      }')
    
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
  
  # Validate status_filter (must be one of: matched, in_progress, integrated, pending, blocked)
  case "$status_filter" in
    matched|in_progress|integrated|pending|blocked)
      echo "$prompts_json" | jq "[.[] | select(.status == \"$status_filter\")]"
      ;;
    *)
      # Invalid status: return all prompts but set a warning
      # For now, return all prompts (graceful degradation)
      echo "$prompts_json"
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

  local readiness_summary
  readiness_summary="$(get_readiness_summary "${MIZUCHI_PROMPTS_DIR:-$root_dir/prompts}")"
  
  local prompts_array
  prompts_array=$(build_prompts_array "$readiness_summary")
  
  local filtered_prompts
  filtered_prompts=$(filter_by_status "$prompts_array" "$status_filter")

  jq -n \
    --argjson prompts "$filtered_prompts" \
    --argjson readiness "$readiness_summary" \
    '{prompts: $prompts, readiness: {status: $readiness.status, total: $readiness.total, ready: $readiness.ready, notReady: $readiness.notReady, blockersTotal: $readiness.blockersTotal, warningsTotal: $readiness.warningsTotal, blockerSummary: $readiness.blockerSummary}}'
}

main "$@"
