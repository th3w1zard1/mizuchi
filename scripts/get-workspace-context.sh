#!/usr/bin/env bash
set -euo pipefail

# Get workspace context for agent startup
# Returns JSON with: prompt_queue, build_artifacts, active_branches, workspace_metrics

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

# Helper: safely extract status from canonical metadata, then legacy notes.md.
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

# Build prompt_queue array
build_prompt_queue() {
  local prompts_dir="${MIZUCHI_PROMPTS_DIR:-$root_dir/prompts}"
  local readiness_summary="${1:-}"
  if [[ -z "$readiness_summary" ]]; then
    readiness_summary="$(get_readiness_summary "$prompts_dir")"
  fi
  
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
    
    # Get modification time
    local mtime
    mtime=$(stat -c %Y "$prompt_dir" 2>/dev/null || echo "0")
    
    # Get function_name from folder name (e.g., fun_00148020 -> FUN_00148020)
    # Or extract from prompt.md if available
    local function_name
    function_name=$(echo "$prompt_name" | sed 's/fun_/FUN_/' | tr '[:lower:]' '[:upper:]')
    
    local from_case
    from_case="$(case_metadata_get "$prompt_dir" functionName 2>/dev/null || true)"
    if [[ -n "$from_case" ]]; then
      function_name="$from_case"
    fi

    local from_settings
    from_settings="$(prompt_settings_get "$prompt_dir" functionName 2>/dev/null || true)"
    if [[ -n "$from_settings" ]]; then
      function_name="$from_settings"
    fi

    # Try to refine from prompt.md if available and metadata did not provide it.
    if [[ -f "$prompt_dir/prompt.md" ]]; then
      local from_md
      from_md=$(grep -oP 'Decompile `\K[^`]+' "$prompt_dir/prompt.md" | head -1 || echo "")
      if [[ -n "$from_md" && -z "$from_case" && -z "$from_settings" ]]; then
        function_name="$from_md"
      fi
    fi
    
    local item
    item=$(jq -n \
      --arg name "$prompt_name" \
      --arg status "$status" \
      --arg func "$function_name" \
      --arg mtime "$mtime" \
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
        last_updated_mtime: ($mtime | tonumber),
        readiness_status: $readiness_status,
        readiness_blockers: $readiness_blockers,
        readiness_warnings: $readiness_warnings,
        blocked_reason: (if $blocked_reason == "" then null else $blocked_reason end),
        integrated_source_path: (if $integrated_source_path == "" then null else $integrated_source_path end),
        integration_receipt_path: (if $integration_receipt_path == "" then null else $integration_receipt_path end),
        integrated_at: (if $integrated_at == "" then null else $integrated_at end)
      }')
    
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
  local current_branch
  current_branch=$(cd "$root_dir" && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
  
  local remotes
  remotes=$(cd "$root_dir" && git remote 2>/dev/null | wc -l || echo "0")
  
  # Count unpushed commits
  local unpushed=0
  if [[ "$current_branch" != "unknown" ]]; then
    unpushed=$(cd "$root_dir" && git log "origin/$current_branch..HEAD" --oneline 2>/dev/null | wc -l || echo "0")
  fi
  
  jq -n \
    --arg branch "$current_branch" \
    --arg remotes "$remotes" \
    --arg unpushed "$unpushed" \
    '{current_branch: $branch, remote_count: ($remotes | tonumber), unpushed_commits: ($unpushed | tonumber)}'
}

# Get recent build artifacts
get_build_artifacts() {
  local artifacts_dir="${MIZUCHI_PROMPTS_DIR:-$root_dir/prompts}"
  local recent_builds=()
  
  # Find most recently modified build/ directories and compiled .o files
  if [[ -d "$artifacts_dir" ]]; then
    while IFS= read -r build_dir; do
      if [[ -f "$build_dir/candidate.o" || -f "$build_dir/programmatic-phase.json" || -f "$build_dir/ai-phase.json" || -f "$build_dir/decomp-function.json" ]]; then
        local mtime
        mtime=$(stat -c %Y "$build_dir/candidate.o" 2>/dev/null || stat -c %Y "$build_dir/decomp-function.json" 2>/dev/null || stat -c %Y "$build_dir/programmatic-phase.json" 2>/dev/null || stat -c %Y "$build_dir/ai-phase.json" 2>/dev/null || echo "0")
        local prompt_name
        prompt_name=$(basename "$(dirname "$build_dir")")
        local decomp_function_status terminal_phase programmatic_status matched_stage ai_status ai_runner
        decomp_function_status=""
        terminal_phase=""
        programmatic_status=""
        matched_stage=""
        ai_status=""
        ai_runner=""
        if [[ -f "$build_dir/decomp-function.json" ]]; then
          decomp_function_status="$(jq -r '.status // ""' "$build_dir/decomp-function.json" 2>/dev/null || true)"
          terminal_phase="$(jq -r '.terminalPhase // ""' "$build_dir/decomp-function.json" 2>/dev/null || true)"
        fi
        if [[ -f "$build_dir/programmatic-phase.json" ]]; then
          programmatic_status="$(jq -r '.status // ""' "$build_dir/programmatic-phase.json" 2>/dev/null || true)"
          matched_stage="$(jq -r '.matchedStage // ""' "$build_dir/programmatic-phase.json" 2>/dev/null || true)"
        fi
        if [[ -f "$build_dir/ai-phase.json" ]]; then
          ai_status="$(jq -r '.status // ""' "$build_dir/ai-phase.json" 2>/dev/null || true)"
          ai_runner="$(jq -r '.runner // ""' "$build_dir/ai-phase.json" 2>/dev/null || true)"
        fi
        recent_builds+=("$(jq -n \
          --arg prompt "$prompt_name" \
          --arg mtime "$mtime" \
          --arg decomp_function_status "$decomp_function_status" \
          --arg terminal_phase "$terminal_phase" \
          --arg programmatic_status "$programmatic_status" \
          --arg matched_stage "$matched_stage" \
          --arg ai_status "$ai_status" \
          --arg ai_runner "$ai_runner" \
          '{
            prompt: $prompt,
            mtime: ($mtime | tonumber),
            decomp_function_status: (if $decomp_function_status == "" then null else $decomp_function_status end),
            terminal_phase: (if $terminal_phase == "" then null else $terminal_phase end),
            programmatic_status: (if $programmatic_status == "" then null else $programmatic_status end),
            matched_stage: (if $matched_stage == "" then null else $matched_stage end),
            ai_status: (if $ai_status == "" then null else $ai_status end),
            ai_runner: (if $ai_runner == "" then null else $ai_runner end)
          }')")
      fi
    done < <(find "$artifacts_dir" -mindepth 2 -maxdepth 2 -type d -name build 2>/dev/null | sort -rV | head -10)
  fi
  
  if [[ ${#recent_builds[@]} -eq 0 ]]; then
    echo "[]"
  else
    printf '%s\n' "${recent_builds[@]}" | jq -s '.'
  fi
}

# Calculate workspace metrics
get_workspace_metrics() {
  local prompts_dir="${MIZUCHI_PROMPTS_DIR:-$root_dir/prompts}"
  
  local total_prompts=0
  local matched_count=0
  local integrated_count=0
  local blocked_count=0
  
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
      
      ((total_prompts++))
      
      local status
      status=$(get_prompt_status "$prompt_dir")
      
      case "$status" in
        matched) ((matched_count++)) ;;
        integrated) ((integrated_count++)) ;;
        blocked) ((blocked_count++)) ;;
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
    --arg blocked "$blocked_count" \
    --arg match_rate "$match_rate" \
    --arg integration_rate "$integration_rate" \
    '{total_prompts: ($total | tonumber), matched: ($matched | tonumber), integrated: ($integrated | tonumber), blocked: ($blocked | tonumber), match_rate_percent: ($match_rate | tonumber), integration_rate_percent: ($integration_rate | tonumber)}'
}

get_readiness_metrics() {
  local prompts_dir="${MIZUCHI_PROMPTS_DIR:-$root_dir/prompts}"
  local summary="${1:-}"
  if [[ -z "$summary" ]]; then
    summary="$(get_readiness_summary "$prompts_dir")"
  fi
  jq '{
    status: .status,
    total: (.total // 0),
    ready: (.ready // 0),
    notReady: (.notReady // 0),
    blockersTotal: (.blockersTotal // 0),
    warningsTotal: (.warningsTotal // 0),
    blockerSummary: (.blockerSummary // {})
  }' <<<"$summary"
}

# Main: assemble JSON response
main() {
  local readiness_summary
  readiness_summary="$(get_readiness_summary "${MIZUCHI_PROMPTS_DIR:-$root_dir/prompts}")"

  local prompt_queue
  prompt_queue=$(build_prompt_queue "$readiness_summary")
  
  local build_artifacts
  build_artifacts=$(get_build_artifacts)
  
  local active_branches
  active_branches=$(get_active_branches)
  
  local workspace_metrics
  workspace_metrics=$(get_workspace_metrics)

  local readiness_metrics
  readiness_metrics=$(get_readiness_metrics "$readiness_summary")
  
  jq -n \
    --argjson prompt_queue "$prompt_queue" \
    --argjson build_artifacts "$build_artifacts" \
    --argjson active_branches "$active_branches" \
    --argjson workspace_metrics "$workspace_metrics" \
    --argjson readiness_metrics "$readiness_metrics" \
    '{
      prompt_queue: $prompt_queue,
      build_artifacts: $build_artifacts,
      active_branches: $active_branches,
      workspace_metrics: $workspace_metrics,
      readiness_metrics: $readiness_metrics
    }'
}

main
