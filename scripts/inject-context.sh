#!/usr/bin/env bash
set -euo pipefail

# Context injection helper: builds dynamic context blocks for agent startup
# Usage: inject-context.sh <agent-name> [--json]
# 
# Examples:
#   inject-context.sh ghidra-binary-scout
#   inject-context.sh decomp-function-agent --json
#
# Output formats:
#   Default (markdown): context block formatted for agent prompts
#   --json: JSON object with all context fields

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/check-log.sh
source "$root_dir/scripts/lib/check-log.sh"
# shellcheck source=scripts/lib/guide-manifest.sh
source "$root_dir/scripts/lib/guide-manifest.sh"

CHECK_LOG_QUIET=0
check_log_init "inject-context"
guide_manifest_load "$root_dir"
guide_manifest_trace_defaults "$root_dir"

# Logging helper (to stderr) — legacy alias
log_debug() {
  check_log_trace "$*"
}

# Parse arguments
AGENT_NAME=""
OUTPUT_FORMAT="markdown"  # markdown or json

parse_arguments() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --json)
        OUTPUT_FORMAT="json"
        shift
        ;;
      *)
        if [[ -z "$AGENT_NAME" ]]; then
          AGENT_NAME="$1"
        fi
        shift
        ;;
    esac
  done
  
  if [[ -z "$AGENT_NAME" ]]; then
    echo "Usage: inject-context.sh <agent-name> [--json]" >&2
    echo "" >&2
    echo "Available agents:" >&2
    echo "  - ghidra-binary-scout" >&2
    echo "  - decomp-prompt-architect" >&2
    echo "  - decomp-function-agent" >&2
    exit 1
  fi
}

# Load agent metadata from AGENT.md
load_agent_metadata() {
  local agent_name="$1"
  local agent_file="$root_dir/.cursor/agents/${agent_name}.md"
  
  if [[ ! -f "$agent_file" ]]; then
    check_log_fail "agent file not found: $(guide_manifest_rel "$root_dir" "$agent_file")"
    check_log_summary "INJECT_CONTEXT_FAIL"
    echo "Error: Agent file not found: $agent_file" >&2
    exit 1
  fi

  check_log_read_file "$agent_file" "$(guide_manifest_rel "$root_dir" "$agent_file")" "agent metadata"
  
  # Extract frontmatter fields (between --- markers)
  # Fields we care about: capabilities, context_injection, context_fields
  
  local context_injection="false"
  
  # Extract context_injection field
  if grep -q "^context_injection: true" "$agent_file" 2>/dev/null; then
    context_injection="true"
  fi
  
  # Extract capabilities array (all lines starting with "  - " after "capabilities:" and before "context_")
  # Use awk to extract the section and parse it properly
  local capabilities_temp
  capabilities_temp=$(awk '/^capabilities:$/{flag=1; next} /^[a-z_]+:/{if(flag) exit} flag && /^  - /{print}' "$agent_file" | \
    sed 's/^  - "//' | sed 's/"$//' | \
    jq -R . | jq -s .)
  
  # Extract context_fields array
  local context_fields_temp
  context_fields_temp=$(awk '/^context_fields:$/{flag=1; next} /^[a-z_]+:/{if(flag) exit} flag && /^  - /{print}' "$agent_file" | \
    sed 's/^  - "//' | sed 's/"$//' | \
    jq -R . | jq -s .)
  
  # Output as JSON for easy parsing
  jq -n \
    --arg name "$agent_name" \
    --arg injection "$context_injection" \
    --argjson capabilities "$capabilities_temp" \
    --argjson context_fields "$context_fields_temp" \
    '{name: $name, context_injection: ($injection == "true"), capabilities: $capabilities, context_fields: $context_fields}'
}

# Build a compact workspace context without shelling out to the full summary
# script. The hook only needs a few fields, so we assemble them directly from
# prompt folders, git state, and MCP config.
get_workspace_context_data() {
  local prompts_dir="$GUIDE_PROMPTS_DIR"
  local mcp_config="$GUIDE_MCP_CONFIG"
  local prompt_items=()
  local build_items=()
  local matched=0
  local integrated=0
  local blocked=0
  local total=0

  if [[ -d "$prompts_dir" ]]; then
    for prompt_dir in "$prompts_dir"/*; do
      [[ -d "$prompt_dir" ]] || continue
      local prompt_name notes_file status mtime
      prompt_name="$(basename "$prompt_dir")"
      [[ "$prompt_name" == "_template" ]] && continue

      status="pending"
      notes_file="$prompt_dir/notes.md"
      if [[ -f "$notes_file" ]]; then
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

      case "$status" in
        matched) matched=$((matched + 1)) ;;
        integrated) integrated=$((integrated + 1)) ;;
        blocked) blocked=$((blocked + 1)) ;;
      esac

      mtime="$(stat -c %Y "$prompt_dir" 2>/dev/null || echo 0)"
      prompt_items+=("$(jq -n \
        --arg name "$prompt_name" \
        --arg status "$status" \
        --arg mtime "$mtime" \
        '{name: $name, status: $status, last_updated_mtime: ($mtime | tonumber)}')")
    done
  fi

  total="${#prompt_items[@]}"

  if [[ -d "$prompts_dir" ]]; then
    while IFS= read -r build_dir; do
      if [[ -f "$build_dir/candidate.o" ]]; then
        local prompt_name mtime
        prompt_name="$(basename "$(dirname "$build_dir")")"
        mtime="$(stat -c %Y "$build_dir/candidate.o" 2>/dev/null || echo 0)"
        build_items+=("$(jq -n --arg prompt "$prompt_name" --arg mtime "$mtime" '{prompt: $prompt, mtime: ($mtime | tonumber)}')")
      fi
    done < <(find "$prompts_dir" -path '*/build' -type d 2>/dev/null | sort -rV | head -10 || true)
  fi

  local current_branch="unknown"
  local remote_count=0
  local unpushed_commits=0
  if [[ -f "$root_dir/.git/HEAD" ]]; then
    local git_head
    IFS= read -r git_head <"$root_dir/.git/HEAD" || git_head=""
    case "$git_head" in
      "ref: refs/heads/"*) current_branch="${git_head#ref: refs/heads/}" ;;
      "") current_branch="unknown" ;;
      *) current_branch="detached" ;;
    esac
  fi
  if [[ -d "$root_dir/.git/refs/remotes" ]]; then
    local remote_entry
    for remote_entry in "$root_dir/.git/refs/remotes"/*; do
      [[ -d "$remote_entry" ]] || continue
      remote_count=$((remote_count + 1))
    done
  fi

  local connected_servers=()
  if [[ -f "$mcp_config" ]]; then
    local agdec_url
    agdec_url=$(jq -r '.mcpServers."agdec-http".url // empty' "$mcp_config" 2>/dev/null || echo "")
    [[ -n "$agdec_url" && "$agdec_url" != "null" ]] && connected_servers+=("$agdec_url")
  fi

  local prompt_queue_json build_artifacts_json connected_servers_json
  prompt_queue_json="$(printf '%s\n' "${prompt_items[@]}" | jq -s '.')"
  build_artifacts_json="$(printf '%s\n' "${build_items[@]}" | jq -s '.')"
  connected_servers_json="$(printf '%s\n' "${connected_servers[@]}" | jq -R . | jq -s .)"

  jq -n \
    --argjson prompt_queue "$prompt_queue_json" \
    --argjson build_artifacts "$build_artifacts_json" \
    --argjson connected_servers "$connected_servers_json" \
    --arg branch "$current_branch" \
    --arg remote_count "$remote_count" \
    --arg unpushed_commits "$unpushed_commits" \
    --arg total_prompts "$total" \
    --arg matched "$matched" \
    --arg integrated "$integrated" \
    --arg blocked "$blocked" \
    '{
      prompt_queue: $prompt_queue,
      ghidra_status: {connected_servers: $connected_servers, loaded_programs: [], analysis_state: "unavailable"},
      build_artifacts: $build_artifacts,
      active_branches: {current_branch: $branch, remote_count: ($remote_count | tonumber), unpushed_commits: ($unpushed_commits | tonumber)},
      workspace_metrics: {
        total_prompts: ($total_prompts | tonumber),
        matched: ($matched | tonumber),
        integrated: ($integrated | tonumber),
        blocked: ($blocked | tonumber),
        match_rate_percent: (if ($total_prompts | tonumber) > 0 then ((($matched | tonumber) * 100 / ($total_prompts | tonumber)) | floor) else 0 end),
        integration_rate_percent: (if ($total_prompts | tonumber) > 0 then ((($integrated | tonumber) * 100 / ($total_prompts | tonumber)) | floor) else 0 end),
        adapter_counts: {}
      }
    }'
}

# Extract specific fields from workspace context
extract_context_field() {
  local context_json="$1"
  local field_name="$2"
  
  case "$field_name" in
    workspace_state)
      echo "$context_json" | jq -c '{
        total_prompts: (.workspace_metrics.total_prompts // 0),
        matched: (.workspace_metrics.matched // 0),
        integrated: (.workspace_metrics.integrated // 0),
        in_progress: ((.workspace_metrics.total_prompts // 0) - (.workspace_metrics.matched // 0) - (.workspace_metrics.integrated // 0)),
        match_rate: (.workspace_metrics.match_rate_percent // 0)
      }'
      ;;
    prompt_queue_summary)
      echo "$context_json" | jq -c '{
        total: ((.prompt_queue // []) | length),
        recent: ((.prompt_queue // []) | sort_by(.last_updated_mtime // 0) | reverse | .[0:3] | map({name: .name, status: .status}))
      }'
      ;;
    recent_activity)
      echo "$context_json" | jq -c '{
        recent_builds: (.build_artifacts | .[0:3] | map({prompt: .prompt})),
        branch: .active_branches.current_branch,
        unpushed: .active_branches.unpushed_commits
      }'
      ;;
    ghidra_status)
      echo "$context_json" | jq -c '.ghidra_status'
      ;;
    constraints)
      # Static constraints (not extracted from context)
      echo '{}'
      ;;
    *)
      echo "{}"
      ;;
  esac
}

# Build markdown context block
build_markdown_context() {
  local agent_metadata="$1"
  local workspace_context="$2"
  
  local agent_name
  agent_name=$(echo "$agent_metadata" | jq -r '.name')
  
  local capabilities
  capabilities=$(echo "$agent_metadata" | jq -r '(.capabilities // [])[]' | sed 's/^/  - /')
  
  local context_fields
  context_fields=$(echo "$agent_metadata" | jq -r '(.context_fields // [])[]')
  
  # Build context summary from requested fields
  local context_summary=""
  while IFS= read -r field; do
    [[ -z "$field" ]] && continue
    local field_data
    field_data=$(extract_context_field "$workspace_context" "$field")
    
    case "$field" in
      workspace_state)
        local total matched integrated match_rate
        total=$(echo "$field_data" | jq -r '.total_prompts')
        matched=$(echo "$field_data" | jq -r '.matched')
        integrated=$(echo "$field_data" | jq -r '.integrated')
        match_rate=$(echo "$field_data" | jq -r '.match_rate')
        
        context_summary+="**Workspace State:**
- Total prompts: $total
- Matched: $matched | Integrated: $integrated | Match rate: ${match_rate}%
"
        ;;
      prompt_queue_summary)
        local queue_total
        queue_total=$(echo "$field_data" | jq -r '.total')
        
        context_summary+="**Recent Work:**
- Queued: $queue_total prompts
"
        ;;
      recent_activity)
        context_summary+="**Recent Activity:**
- Latest operations in this session
"
        ;;
      ghidra_status)
        local server_count
        server_count=$(echo "$field_data" | jq -r '.connected_servers | length')
        
        context_summary+="**Ghidra Status:**
- Servers: $server_count connected
"
        ;;
      constraints)
        context_summary+="**Constraints:**
- Never modify source tree directly during matching
- Always verify with objdiff before integrating
- Stop on first gate failure; report diagnostic
- See CAPABILITY_MATRIX.md for detailed operation matrix
"
        ;;
    esac
  done <<< "$context_fields"
  
  # Build final markdown context block
  cat <<EOF
## Workspace Context (Injected at Startup)

**Your Capabilities ($agent_name):**
$capabilities

**MCP Tools Available:**
- get_workspace_context() — query workspace state
- list_prompts() — discover available work
- run_objdiff() — verify matches
- /help — list all available commands

$context_summary

---
See CAPABILITY_MATRIX.md for the complete agent operation matrix.
EOF
}

# Build JSON context object
build_json_context() {
  local agent_metadata="$1"
  local workspace_context="$2"
  
  local agent_name
  agent_name=$(echo "$agent_metadata" | jq -r '.name')
  
  local context_fields
  context_fields=$(echo "$agent_metadata" | jq -r '(.context_fields // [])[]')
  
  # Build context object from requested fields
  local context_obj="{\"agent\": \"$agent_name\", \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\", \"fields\": {"
  
  local first=true
  while IFS= read -r field; do
    [[ -z "$field" ]] && continue
    
    if [[ "$first" == false ]]; then
      context_obj+=","
    fi
    first=false
    
    local field_data
    field_data=$(extract_context_field "$workspace_context" "$field")
    
    context_obj+="\"$field\": $field_data"
  done <<< "$context_fields"
  
  context_obj+="}}"
  
  echo "$context_obj" | jq .
}

# Main function
main() {
  parse_arguments "$@"
  
  # Load agent metadata
  local agent_metadata
  agent_metadata=$(load_agent_metadata "$AGENT_NAME")
  
  # Check if context injection is enabled for this agent
  local context_injection
  context_injection=$(echo "$agent_metadata" | jq -r '.context_injection')
  
  if [[ "$context_injection" != "true" ]]; then
    check_log_trace "warn  context injection disabled for agent: $AGENT_NAME"
    check_log_summary "INJECT_CONTEXT_SKIP"
    echo "Warning: Context injection not enabled for agent: $AGENT_NAME" >&2
    exit 0
  fi
  
  # Get workspace context data
  local workspace_context
  workspace_context=$(get_workspace_context_data)
  
  # Output in requested format
  case "$OUTPUT_FORMAT" in
    markdown)
      build_markdown_context "$agent_metadata" "$workspace_context"
      ;;
    json)
      build_json_context "$agent_metadata" "$workspace_context"
      ;;
    *)
      check_log_fail "unknown output format: $OUTPUT_FORMAT"
      check_log_summary "INJECT_CONTEXT_FAIL"
      echo "Error: Unknown output format: $OUTPUT_FORMAT" >&2
      exit 1
      ;;
  esac
  check_log_summary "INJECT_CONTEXT_OK"
}

main "$@"
