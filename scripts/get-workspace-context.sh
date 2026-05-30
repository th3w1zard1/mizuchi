#!/usr/bin/env bash
# MCP tool: get_workspace_context
# Returns JSON on stdout; verbose trace on stderr (--quiet to suppress trace).
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/check-log.sh
source "$root_dir/scripts/lib/check-log.sh"
# shellcheck source=scripts/lib/guide-manifest.sh
source "$root_dir/scripts/lib/guide-manifest.sh"
# shellcheck source=scripts/lib/cli-agent.sh
source "$root_dir/scripts/lib/cli-agent.sh"

usage() {
  cat <<EOF
Usage: get-workspace-context.sh [--quiet]

Returns workspace JSON on stdout: prompt_queue, ghidra_status, build_artifacts,
active_branches, workspace_metrics.

Options:
  --quiet   Suppress verbose trace (keep summary + result token)
  -h, --help  Show help

Examples:
  ./scripts/get-workspace-context.sh | jq .workspace_metrics
  ./scripts/get-workspace-context.sh --quiet | jq '.prompt_queue[] | select(.status=="matched")'
EOF
}

quiet=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet) quiet=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Error: unknown argument: $1" >&2; echo "  ./scripts/get-workspace-context.sh --help" >&2; exit 2 ;;
  esac
done

[[ "$quiet" -eq 1 ]] && check_log_configure --quiet
check_log_init "get-workspace-context"
guide_manifest_load "$root_dir"
guide_manifest_trace_defaults "$root_dir"

get_prompt_status() {
  local prompt_dir="$1"
  local notes_file="$prompt_dir/notes.md"
  local status="pending"

  if [[ -f "$notes_file" ]]; then
    check_log_read_file "$notes_file" "$(guide_manifest_rel "$root_dir" "$notes_file")" "prompt status"
    if grep -q "status.*integrated" "$notes_file" 2>/dev/null; then
      status="integrated"
    elif grep -q "status.*matched" "$notes_file" 2>/dev/null; then
      status="matched"
    elif grep -q "status.*in_progress\|status.*in-progress" "$notes_file" 2>/dev/null; then
      status="in_progress"
    fi
  else
    check_log_trace "read  $(guide_manifest_rel "$root_dir" "$notes_file") (missing — status=pending)"
  fi

  echo "$status"
}

build_prompt_queue() {
  local prompts_dir="$GUIDE_PROMPTS_DIR"

  if [[ ! -d "$prompts_dir" ]]; then
    check_log_trace "read  $(guide_manifest_rel "$root_dir" "$prompts_dir")/ (missing — empty queue)"
    echo "[]"
    return
  fi

  check_log_read_dir "$prompts_dir" "$(guide_manifest_rel "$root_dir" "$prompts_dir")" "prompts root"
  local queue_items=()

  for prompt_dir in "$prompts_dir"/*; do
    [[ -d "$prompt_dir" ]] || continue
    local prompt_name
    prompt_name=$(basename "$prompt_dir")
    [[ "$prompt_name" == "_template" ]] && continue

    check_log_trace "read  prompt $(guide_manifest_rel "$root_dir" "$prompt_dir")"
    local status mtime function_name from_md item
    status=$(get_prompt_status "$prompt_dir")
    mtime=$(stat -c %Y "$prompt_dir" 2>/dev/null || echo "0")
    function_name=$(echo "$prompt_name" | sed 's/fun_/FUN_/' | tr '[:lower:]' '[:upper:]')

    if [[ -f "$prompt_dir/prompt.md" ]]; then
      check_log_read_file "$prompt_dir/prompt.md" "$(guide_manifest_rel "$root_dir" "$prompt_dir/prompt.md")" "prompt.md"
      from_md=$(grep -o 'Decompile `[^`]\+' "$prompt_dir/prompt.md" 2>/dev/null | head -1 | sed 's/Decompile `//;s/`$//' || echo "")
      [[ -n "$from_md" ]] && function_name="$from_md"
    fi

    item=$(jq -n \
      --arg name "$prompt_name" \
      --arg status "$status" \
      --arg func "$function_name" \
      --arg mtime "$mtime" \
      '{name: $name, status: $status, function_name: $func, last_updated_mtime: ($mtime | tonumber)}')
    queue_items+=("$item")
  done

  if [[ ${#queue_items[@]} -eq 0 ]]; then
    echo "[]"
  else
    printf '%s\n' "${queue_items[@]}" | jq -s '.'
  fi
}

get_active_branches() {
  local current_branch="unknown"
  local remotes=0
  local unpushed=0

  if (cd "$root_dir" && git rev-parse --is-inside-work-tree >/dev/null 2>&1); then
    check_log_run_cmd "git" rev-parse --abbrev-ref HEAD
    current_branch=$(cd "$root_dir" && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    remotes=$(cd "$root_dir" && git remote 2>/dev/null | wc -l | tr -d '[:space:]')
    if [[ "$current_branch" != "unknown" ]] && \
       (cd "$root_dir" && git rev-parse --verify "origin/$current_branch" >/dev/null 2>&1); then
      unpushed=$(cd "$root_dir" && git log "origin/$current_branch..HEAD" --oneline 2>/dev/null | wc -l | tr -d '[:space:]')
    fi
  else
    check_log_trace "read  git (not a work tree — branch unknown)"
  fi

  jq -n \
    --arg branch "$current_branch" \
    --arg remotes "$remotes" \
    --arg unpushed "$unpushed" \
    '{current_branch: $branch, remote_count: ($remotes | tonumber), unpushed_commits: ($unpushed | tonumber)}'
}

get_build_artifacts() {
  local artifacts_dir="$GUIDE_PROMPTS_DIR"
  local recent_builds=()

  if [[ -d "$artifacts_dir" ]]; then
    while IFS= read -r build_dir; do
      if [[ -f "$build_dir/candidate.o" ]]; then
        check_log_read_file "$build_dir/candidate.o" "$(guide_manifest_rel "$root_dir" "$build_dir/candidate.o")" "candidate.o"
        local mtime prompt_name
        mtime=$(stat -c %Y "$build_dir/candidate.o" 2>/dev/null || echo "0")
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

get_ghidra_status() {
  local servers=()
  local mcp_config="$GUIDE_MCP_CONFIG"

  if [[ -f "$mcp_config" ]]; then
    check_log_read_file "$mcp_config" "$(guide_manifest_rel "$root_dir" "$mcp_config")" "MCP config"
    local server agdec_url
    for server in "${GUIDE_MCP_SERVERS[@]}"; do
      check_log_mcp_server "$(guide_manifest_rel "$root_dir" "$mcp_config")" "$server"
    done
    agdec_url=$(jq -r '.mcpServers."agdec-http".url // empty' "$mcp_config" 2>/dev/null || echo "")
    [[ -n "$agdec_url" && "$agdec_url" != "null" ]] && servers+=("$agdec_url")
  else
    check_log_fail "missing MCP config: $(guide_manifest_rel "$root_dir" "$mcp_config")"
  fi

  jq -n \
    --argjson servers "$(printf '%s\n' "${servers[@]}" | jq -R . | jq -s .)" \
    --argjson programs "[]" \
    '{connected_servers: $servers, loaded_programs: $programs, analysis_state: "unavailable"}'
}

get_workspace_metrics() {
  local prompts_dir="$GUIDE_PROMPTS_DIR"
  local total_prompts=0 matched_count=0 integrated_count=0

  if [[ -d "$prompts_dir" ]]; then
    for prompt_dir in "$prompts_dir"/*; do
      [[ -d "$prompt_dir" ]] || continue
      local prompt_name status
      prompt_name=$(basename "$prompt_dir")
      [[ "$prompt_name" == "_template" ]] && continue
      total_prompts=$((total_prompts + 1))
      status=$(get_prompt_status "$prompt_dir")
      case "$status" in
        matched) matched_count=$((matched_count + 1)) ;;
        integrated) integrated_count=$((integrated_count + 1)) ;;
      esac
    done
  fi

  local match_rate=0 integration_rate=0
  [[ $total_prompts -gt 0 ]] && match_rate=$((matched_count * 100 / total_prompts))
  [[ $total_prompts -gt 0 ]] && integration_rate=$((integrated_count * 100 / total_prompts))

  jq -n \
    --arg total "$total_prompts" \
    --arg matched "$matched_count" \
    --arg integrated "$integrated_count" \
    --arg match_rate "$match_rate" \
    --arg integration_rate "$integration_rate" \
    '{total_prompts: ($total | tonumber), matched: ($matched | tonumber), integrated: ($integrated | tonumber), match_rate_percent: ($match_rate | tonumber), integration_rate_percent: ($integration_rate | tonumber)}'
}

main() {
  local prompt_queue ghidra_status build_artifacts active_branches workspace_metrics

  check_log_run_step "build prompt_queue"
  prompt_queue=$(build_prompt_queue)
  check_log_run_step "ghidra_status"
  ghidra_status=$(get_ghidra_status)
  check_log_run_step "build_artifacts"
  build_artifacts=$(get_build_artifacts)
  check_log_run_step "active_branches"
  active_branches=$(get_active_branches)
  check_log_run_step "workspace_metrics"
  workspace_metrics=$(get_workspace_metrics)

  check_log_summary "GET_WORKSPACE_CONTEXT_OK"
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
  printf 'GET_WORKSPACE_CONTEXT_OK prompts=%s\n' \
    "$(jq -r '.workspace_metrics.total_prompts' <<<"$workspace_metrics")" >&2
}

main
