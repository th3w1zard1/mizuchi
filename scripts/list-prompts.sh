#!/usr/bin/env bash
set -euo pipefail

# MCP tool: list_prompts
# Returns JSON array of available prompt folders with metadata
# Optional filter: status=matched|in_progress|integrated|pending|blocked

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Helper: extract status from notes.md
get_prompt_status() {
  local prompt_dir="$1"
  local notes_file="$prompt_dir/notes.md"
  
  # Default status
  local status="pending"
  
  if [[ -f "$notes_file" ]]; then
    # Look for patterns like "status: matched" or "status: in_progress" or "status: integrated"
    if grep -q "status.*integrated" "$notes_file" 2>/dev/null; then
      status="integrated"
    elif grep -q "status.*matched" "$notes_file" 2>/dev/null; then
      status="matched"
    elif grep -q "status.*in_progress\|status.*in-progress" "$notes_file" 2>/dev/null; then
      status="in_progress"
    elif grep -q "status.*blocked" "$notes_file" 2>/dev/null; then
      status="blocked"
    fi
  fi
  
  echo "$status"
}

# Helper: extract function name from prompt metadata
get_function_name() {
  local prompt_dir="$1"
  local prompt_name="$2"
  
  # Default: derive from folder name (e.g., fun_00148020 -> FUN_00148020)
  local function_name
  function_name=$(echo "$prompt_name" | sed 's/fun_/FUN_/' | tr '[:lower:]' '[:upper:]')
  
  # Try to refine from prompt.md if available
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
  local prompts_dir="$root_dir/prompts"
  local prompts=()
  
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
      '{name: $name, status: $status, function_name: $func, last_updated: $updated}')
    
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
  
  jq -n --argjson prompts "$filtered_prompts" '{prompts: $prompts}'
}

main "$@"
