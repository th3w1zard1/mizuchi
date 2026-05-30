#!/usr/bin/env bash
set -euo pipefail

# Get workspace context for agent startup
# Returns JSON with: prompt_queue, ghidra_status, build_artifacts, active_branches, workspace_metrics

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Helper: safely extract status from notes.md
get_prompt_status() {
  local prompt_dir="$1"
  local notes_file="$prompt_dir/notes.md"
  
  # Default status
  local status="pending"
  
  if [[ -f "$notes_file" ]]; then
    # Try to extract status from notes.md frontmatter or first meaningful line
    # Look for patterns like "status: matched" or "status: in_progress" or "status: integrated"
    if grep -q "status.*integrated" "$notes_file" 2>/dev/null; then
      status="integrated"
    elif grep -q "status.*matched" "$notes_file" 2>/dev/null; then
      status="matched"
    elif grep -q "status.*in_progress\|status.*in-progress" "$notes_file" 2>/dev/null; then
      status="in_progress"
    fi
  fi
  
  echo "$status"
}

# Build prompt_queue array
build_prompt_queue() {
  local prompts_dir="$root_dir/prompts"
  
  if [[ ! -d "$prompts_dir" ]]; then
    echo "[]"
    return
  fi
  
  local queue_items=()
  
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
    
    # Get modification time
    local mtime
    mtime=$(stat -c %Y "$prompt_dir" 2>/dev/null || echo "0")
    
    # Get function_name from folder name (e.g., fun_00148020 -> FUN_00148020)
    # Or extract from prompt.md if available
    local function_name
    function_name=$(echo "$prompt_name" | sed 's/fun_/FUN_/' | tr '[:lower:]' '[:upper:]')
    
    # Try to refine from prompt.md if available
    if [[ -f "$prompt_dir/prompt.md" ]]; then
      local from_md
      from_md=$(grep -o 'Decompile `[^`]\+' "$prompt_dir/prompt.md" 2>/dev/null | head -1 | sed 's/Decompile `//;s/`$//' || echo "")
      if [[ -n "$from_md" ]]; then
        function_name="$from_md"
      fi
    fi
    
    local item
    item=$(jq -n \
      --arg name "$prompt_name" \
      --arg status "$status" \
      --arg func "$function_name" \
      --arg mtime "$mtime" \
      '{name: $name, status: $status, function_name: $func, last_updated_mtime: ($mtime | tonumber)}')
    
    queue_items+=("$item")
  done
  
  # Output as JSON array
  if [[ ${#queue_items[@]} -eq 0 ]]; then
    echo "[]"
  else
    # Join array items with commas
    printf '%s\n' "${queue_items[@]}" | jq -s '.'
  fi
}

# Get git branch and status
get_active_branches() {
  local current_branch="unknown"
  local remotes=0
  local unpushed=0

  if (cd "$root_dir" && git rev-parse --is-inside-work-tree >/dev/null 2>&1); then
    current_branch=$(cd "$root_dir" && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    remotes=$(cd "$root_dir" && git remote 2>/dev/null | wc -l | tr -d '[:space:]')
    if [[ "$current_branch" != "unknown" ]] && \
       (cd "$root_dir" && git rev-parse --verify "origin/$current_branch" >/dev/null 2>&1); then
      unpushed=$(cd "$root_dir" && git log "origin/$current_branch..HEAD" --oneline 2>/dev/null | wc -l | tr -d '[:space:]')
    fi
  fi

  jq -n \
    --arg branch "$current_branch" \
    --arg remotes "$remotes" \
    --arg unpushed "$unpushed" \
    '{current_branch: $branch, remote_count: ($remotes | tonumber), unpushed_commits: ($unpushed | tonumber)}'
}

# Get recent build artifacts
get_build_artifacts() {
  local artifacts_dir="$root_dir/prompts"
  local recent_builds=()
  
  # Find most recently modified build/ directories and compiled .o files
  if [[ -d "$artifacts_dir" ]]; then
    while IFS= read -r build_dir; do
      if [[ -f "$build_dir/candidate.o" ]]; then
        local mtime
        mtime=$(stat -c %Y "$build_dir/candidate.o" 2>/dev/null || echo "0")
        local prompt_name
        prompt_name=$(basename "$(dirname "$build_dir")")
        recent_builds+=("$(jq -n --arg prompt "$prompt_name" --arg mtime "$mtime" '{prompt: $prompt, mtime: ($mtime | tonumber)}')")
      fi
    done < <(find "$artifacts_dir" -path '*/build' -type d 2>/dev/null | sort -rV | head -10 || true)
  fi
  
  if [[ ${#recent_builds[@]} -eq 0 ]]; then
    echo "[]"
  else
    printf '%s\n' "${recent_builds[@]}" | jq -s '.'
  fi
}

# Check Ghidra status (if available; graceful fallback)
get_ghidra_status() {
  local servers=()
  local loaded_programs=()
  
  # Try to query agdec HTTP endpoint if configured in mcp.json
  local mcp_config="$root_dir/.cursor/mcp.json"
  if [[ -f "$mcp_config" ]]; then
    local agdec_url
    agdec_url=$(jq -r '.mcpServers."agdec-http".url // empty' "$mcp_config" 2>/dev/null || echo "")
    
    if [[ -n "$agdec_url" && "$agdec_url" != "null" ]]; then
      servers+=("$agdec_url")
    fi
  fi
  
  # Default: no active analysis (would require runtime Ghidra query)
  jq -n \
    --argjson servers "$(printf '%s\n' "${servers[@]}" | jq -R . | jq -s .)" \
    --argjson programs "[]" \
    '{connected_servers: $servers, loaded_programs: $programs, analysis_state: "unavailable"}'
}

# Calculate workspace metrics
get_workspace_metrics() {
  local prompts_dir="$root_dir/prompts"
  
  local total_prompts=0
  local matched_count=0
  local integrated_count=0
  
  if [[ -d "$prompts_dir" ]]; then
    for prompt_dir in "$prompts_dir"/*; do
      if [[ ! -d "$prompt_dir" ]]; then
        continue
      fi
      
      local prompt_name
      prompt_name=$(basename "$prompt_dir")
      
      if [[ "$prompt_name" == "_template" ]]; then
        continue
      fi
      
      total_prompts=$((total_prompts + 1))
      
      local status
      status=$(get_prompt_status "$prompt_dir")
      
      case "$status" in
        matched) matched_count=$((matched_count + 1)) ;;
        integrated) integrated_count=$((integrated_count + 1)) ;;
      esac
    done
  fi
  
  local match_rate=0
  if [[ $total_prompts -gt 0 ]]; then
    match_rate=$((matched_count * 100 / total_prompts))
  fi
  
  local integration_rate=0
  if [[ $total_prompts -gt 0 ]]; then
    integration_rate=$((integrated_count * 100 / total_prompts))
  fi
  
  jq -n \
    --arg total "$total_prompts" \
    --arg matched "$matched_count" \
    --arg integrated "$integrated_count" \
    --arg match_rate "$match_rate" \
    --arg integration_rate "$integration_rate" \
    '{total_prompts: ($total | tonumber), matched: ($matched | tonumber), integrated: ($integrated | tonumber), match_rate_percent: ($match_rate | tonumber), integration_rate_percent: ($integration_rate | tonumber)}'
}

# Main: assemble JSON response
main() {
  local prompt_queue
  prompt_queue=$(build_prompt_queue)
  
  local ghidra_status
  ghidra_status=$(get_ghidra_status)
  
  local build_artifacts
  build_artifacts=$(get_build_artifacts)
  
  local active_branches
  active_branches=$(get_active_branches)
  
  local workspace_metrics
  workspace_metrics=$(get_workspace_metrics)
  
  jq -n \
    --argjson prompt_queue "$prompt_queue" \
    --argjson ghidra_status "$ghidra_status" \
    --argjson build_artifacts "$build_artifacts" \
    --argjson active_branches "$active_branches" \
    --argjson workspace_metrics "$workspace_metrics" \
    '{
      prompt_queue: $prompt_queue,
      ghidra_status: $ghidra_status,
      build_artifacts: $build_artifacts,
      active_branches: $active_branches,
      workspace_metrics: $workspace_metrics
    }'
}

main
