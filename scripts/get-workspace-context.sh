#!/usr/bin/env bash
# MCP tool: get_workspace_context
# Returns JSON on stdout; verbose trace on stderr (--quiet to suppress trace).
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${MIZUCHI_WORKSPACE_CONTEXT_LEGACY:-0}" != "1" ]]; then
  early_quiet=0
  case "${1:-}" in
    --quiet) early_quiet=1; shift ;;
    -h|--help)
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
      exit 0
      ;;
  esac
  if [[ $# -gt 0 ]]; then
    echo "Error: unknown argument: $1" >&2
    echo "  ./scripts/get-workspace-context.sh --help" >&2
    exit 2
  fi

  if command -v ruby >/dev/null 2>&1; then
    ruby -ryaml -rjson - "$root_dir" "$root_dir/prompts" "$root_dir/.cursor/mcp.json" <<'RUBY'
root, prompts_dir, mcp_config = ARGV

def read_yaml(path)
  return {} unless File.file?(path)
  data = YAML.load_file(path) || {}
  data.is_a?(Hash) ? data : {}
rescue StandardError
  {}
end

def dig_path(data, path)
  path.split(".").reduce(data) { |acc, key| acc.is_a?(Hash) ? acc[key] : nil }
end

def prompt_status(prompt_dir)
  notes = File.join(prompt_dir, "notes.md")
  return "pending" unless File.file?(notes)
  body = File.read(notes)
  return "integrated" if body.match?(/status.*integrated/)
  return "matched" if body.match?(/status.*matched/)
  return "in_progress" if body.match?(/status.*in_progress|status.*in-progress/)
  return "blocked" if body.match?(/status.*blocked/)
  "pending"
rescue StandardError
  "pending"
end

def adapter_supported?(adapter)
  %w[odyssey elf-ps2].include?(adapter)
end

def adapter_family(adapter)
  {"odyssey" => "odyssey", "elf-ps2" => "elf-ps2"}.fetch(adapter, "unknown")
end

def adapter_load_tool(adapter)
  {"odyssey" => "agdec-http", "elf-ps2" => "ghidra"}.fetch(adapter, "unknown")
end

def adapter_context_path(adapter)
  adapter_supported?(adapter) ? "context/ctx.h" : ""
end

def git_head(root)
  head_path = File.join(root, ".git", "HEAD")
  return "unknown" unless File.file?(head_path)
  head = File.read(head_path).strip
  return head.sub(%r{\Aref: refs/heads/}, "") if head.start_with?("ref: refs/heads/")
  head.empty? ? "unknown" : "detached"
rescue StandardError
  "unknown"
end

def remote_count(root)
  remotes_dir = File.join(root, ".git", "refs", "remotes")
  return Dir.children(remotes_dir).reject { |entry| entry.start_with?(".") }.length if File.directory?(remotes_dir)
  packed_refs = File.join(root, ".git", "packed-refs")
  return 0 unless File.file?(packed_refs)
  File.readlines(packed_refs).grep(%r{refs/remotes/}).map { |line| line.split.last.to_s.split("/")[2] }.compact.uniq.length
rescue StandardError
  0
end

prompt_queue = []
if File.directory?(prompts_dir)
  Dir.children(prompts_dir).sort.each do |name|
    prompt_dir = File.join(prompts_dir, name)
    next unless File.directory?(prompt_dir)
    next if name == "_template"
    case_data = read_yaml(File.join(prompt_dir, "case.yaml"))
    settings = read_yaml(File.join(prompt_dir, "settings.yaml"))
    adapter = dig_path(case_data, "adapter.id").to_s
    adapter = "unknown" if adapter.empty?
    target_family = dig_path(case_data, "target.family").to_s
    target_family = adapter_family(adapter) if target_family.empty?
    load_tool = dig_path(case_data, "load.tool").to_s
    load_tool = adapter_load_tool(adapter) if load_tool.empty?
    context_path = dig_path(case_data, "load.contextPath").to_s
    context_path = adapter_context_path(adapter) if context_path.empty?
    function_name = dig_path(case_data, "symbol.name").to_s
    function_name = settings["functionName"].to_s if function_name.empty?
    function_name = name.sub(/^fun_/, "FUN_").upcase if function_name.empty?
    proof_target = dig_path(case_data, "proof.targetObjectPath").to_s
    proof_target = settings["targetObjectPath"].to_s if proof_target.empty?
    case_id = dig_path(case_data, "caseId").to_s
    prompt_queue << {
      case_id: case_id.empty? ? name : case_id,
      status: prompt_status(prompt_dir),
      function_name: function_name,
      last_updated_mtime: (File.mtime(prompt_dir).to_i rescue 0),
      adapter: adapter,
      adapter_supported: adapter_supported?(adapter),
      target_family: target_family,
      proof_target: proof_target,
      load_tool: load_tool,
      context_path: context_path,
      name: name
    }
  end
end

recent_builds = []
if File.directory?(prompts_dir)
  Dir.glob(File.join(prompts_dir, "*", "build", "candidate.o")).sort.reverse.first(10).each do |candidate|
    build_dir = File.dirname(candidate)
    recent_builds << {
      prompt: File.basename(File.dirname(build_dir)),
      mtime: (File.mtime(candidate).to_i rescue 0)
    }
  end
end

servers = []
if File.file?(mcp_config)
  begin
    url = JSON.parse(File.read(mcp_config)).dig("mcpServers", "agdec-http", "url")
    servers << url if url && !url.empty?
  rescue StandardError
  end
end

total = prompt_queue.length
matched = prompt_queue.count { |item| item[:status] == "matched" }
integrated = prompt_queue.count { |item| item[:status] == "integrated" }
blocked = prompt_queue.count { |item| item[:status] == "blocked" }
adapter_counts = prompt_queue.each_with_object({}) { |item, acc| acc[item[:adapter]] = acc.fetch(item[:adapter], 0) + 1 }

puts JSON.generate({
  prompt_queue: prompt_queue,
  ghidra_status: {connected_servers: servers, loaded_programs: [], analysis_state: "unavailable"},
  build_artifacts: recent_builds,
  active_branches: {current_branch: git_head(root), remote_count: remote_count(root), unpushed_commits: 0},
  workspace_metrics: {
    total_prompts: total,
    matched: matched,
    integrated: integrated,
    blocked: blocked,
    match_rate_percent: total.positive? ? ((matched * 100 / total).floor) : 0,
    integration_rate_percent: total.positive? ? ((integrated * 100 / total).floor) : 0,
    adapter_counts: adapter_counts
  }
})
RUBY
    if [[ "$early_quiet" -eq 0 ]]; then
      printf '\n--- get-workspace-context summary (GET_WORKSPACE_CONTEXT_OK) ---\n' >&2
      printf 'passed=1 failed=0\n' >&2
      printf 'GET_WORKSPACE_CONTEXT_OK\n' >&2
    fi
    exit 0
  fi
fi
# shellcheck source=scripts/lib/check-log.sh
source "$root_dir/scripts/lib/check-log.sh"
# shellcheck source=scripts/lib/guide-manifest.sh
source "$root_dir/scripts/lib/guide-manifest.sh"
# shellcheck source=scripts/lib/cli-agent.sh
source "$root_dir/scripts/lib/cli-agent.sh"
# shellcheck source=scripts/lib/prompt-metadata.sh
source "$root_dir/scripts/lib/prompt-metadata.sh"

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
    local item
    item=$(prompt_metadata_summary_json "$prompt_dir" | jq --arg name "$prompt_name" '. + {name: $name}')
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
  local prompt_queue="$1"

  jq -c '
    def count_status($status): map(select(.status == $status)) | length;
    . as $items
    | ($items | length) as $total
    | ($items | count_status("matched")) as $matched
    | ($items | count_status("integrated")) as $integrated
    | ($items | count_status("blocked")) as $blocked
    | {
        total_prompts: $total,
        matched: $matched,
        integrated: $integrated,
        blocked: $blocked,
        match_rate_percent: (if $total > 0 then (($matched * 100 / $total) | floor) else 0 end),
        integration_rate_percent: (if $total > 0 then (($integrated * 100 / $total) | floor) else 0 end),
        adapter_counts: (reduce $items[] as $item ({}; .[$item.adapter] = ((.[$item.adapter] // 0) + 1)))
      }
  ' <<<"$prompt_queue"
}

emit_workspace_context_fast() {
  ruby -ryaml -rjson - "$root_dir" "$GUIDE_PROMPTS_DIR" "$GUIDE_MCP_CONFIG" <<'RUBY'
root, prompts_dir, mcp_config = ARGV

def read_yaml(path)
  return {} unless File.file?(path)
  data = YAML.load_file(path) || {}
  data.is_a?(Hash) ? data : {}
rescue StandardError
  {}
end

def dig_path(data, path)
  path.split(".").reduce(data) do |acc, key|
    acc.is_a?(Hash) ? acc[key] : nil
  end
end

def prompt_status(prompt_dir)
  notes = File.join(prompt_dir, "notes.md")
  return "pending" unless File.file?(notes)
  body = File.read(notes)
  return "integrated" if body.match?(/status.*integrated/)
  return "matched" if body.match?(/status.*matched/)
  return "in_progress" if body.match?(/status.*in_progress|status.*in-progress/)
  return "blocked" if body.match?(/status.*blocked/)
  "pending"
rescue StandardError
  "pending"
end

def adapter_supported?(adapter)
  %w[odyssey elf-ps2].include?(adapter)
end

def adapter_family(adapter)
  case adapter
  when "odyssey" then "odyssey"
  when "elf-ps2" then "elf-ps2"
  else "unknown"
  end
end

def adapter_load_tool(adapter)
  case adapter
  when "odyssey" then "agdec-http"
  when "elf-ps2" then "ghidra"
  else "unknown"
  end
end

def adapter_context_path(adapter)
  adapter_supported?(adapter) ? "context/ctx.h" : ""
end

def git_head(root)
  git_dir = File.join(root, ".git")
  head_path = File.join(git_dir, "HEAD")
  return "unknown" unless File.file?(head_path)
  head = File.read(head_path).strip
  return head.sub(%r{\Aref: refs/heads/}, "") if head.start_with?("ref: refs/heads/")
  head.empty? ? "unknown" : "detached"
rescue StandardError
  "unknown"
end

def remote_count(root)
  remotes_dir = File.join(root, ".git", "refs", "remotes")
  return Dir.children(remotes_dir).reject { |entry| entry.start_with?(".") }.length if File.directory?(remotes_dir)
  packed_refs = File.join(root, ".git", "packed-refs")
  return 0 unless File.file?(packed_refs)
  File.readlines(packed_refs).grep(%r{refs/remotes/}).map { |line| line.split.last.to_s.split("/")[2] }.compact.uniq.length
rescue StandardError
  0
end

prompt_queue = []
if File.directory?(prompts_dir)
  Dir.children(prompts_dir).sort.each do |name|
    prompt_dir = File.join(prompts_dir, name)
    next unless File.directory?(prompt_dir)
    next if name == "_template"

    case_data = read_yaml(File.join(prompt_dir, "case.yaml"))
    settings = read_yaml(File.join(prompt_dir, "settings.yaml"))
    adapter = dig_path(case_data, "adapter.id").to_s
    adapter = "unknown" if adapter.empty?
    target_family = dig_path(case_data, "target.family").to_s
    target_family = adapter_family(adapter) if target_family.empty?
    load_tool = dig_path(case_data, "load.tool").to_s
    load_tool = adapter_load_tool(adapter) if load_tool.empty?
    context_path = dig_path(case_data, "load.contextPath").to_s
    context_path = adapter_context_path(adapter) if context_path.empty?
    function_name = dig_path(case_data, "symbol.name").to_s
    function_name = settings["functionName"].to_s if function_name.empty?
    function_name = name.sub(/^fun_/, "FUN_").upcase if function_name.empty?
    proof_target = dig_path(case_data, "proof.targetObjectPath").to_s
    proof_target = settings["targetObjectPath"].to_s if proof_target.empty?

    prompt_queue << {
      case_id: (dig_path(case_data, "caseId").to_s.empty? ? name : dig_path(case_data, "caseId").to_s),
      status: prompt_status(prompt_dir),
      function_name: function_name,
      last_updated_mtime: (File.mtime(prompt_dir).to_i rescue 0),
      adapter: adapter,
      adapter_supported: adapter_supported?(adapter),
      target_family: target_family,
      proof_target: proof_target,
      load_tool: load_tool,
      context_path: context_path,
      name: name
    }
  end
end

recent_builds = []
if File.directory?(prompts_dir)
  Dir.glob(File.join(prompts_dir, "*", "build", "candidate.o")).sort.reverse.first(10).each do |candidate|
    build_dir = File.dirname(candidate)
    recent_builds << {
      prompt: File.basename(File.dirname(build_dir)),
      mtime: (File.mtime(candidate).to_i rescue 0)
    }
  end
end

servers = []
if File.file?(mcp_config)
  begin
    mcp = JSON.parse(File.read(mcp_config))
    url = mcp.dig("mcpServers", "agdec-http", "url")
    servers << url if url && !url.empty?
  rescue StandardError
  end
end

branch = git_head(root)
remotes = remote_count(root)
unpushed = 0

total = prompt_queue.length
matched = prompt_queue.count { |item| item[:status] == "matched" }
integrated = prompt_queue.count { |item| item[:status] == "integrated" }
blocked = prompt_queue.count { |item| item[:status] == "blocked" }
adapter_counts = prompt_queue.each_with_object({}) do |item, acc|
  acc[item[:adapter]] = acc.fetch(item[:adapter], 0) + 1
end

puts JSON.pretty_generate({
  prompt_queue: prompt_queue,
  ghidra_status: {
    connected_servers: servers,
    loaded_programs: [],
    analysis_state: "unavailable"
  },
  build_artifacts: recent_builds,
  active_branches: {
    current_branch: branch,
    remote_count: remotes,
    unpushed_commits: unpushed
  },
  workspace_metrics: {
    total_prompts: total,
    matched: matched,
    integrated: integrated,
    blocked: blocked,
    match_rate_percent: total.positive? ? ((matched * 100 / total).floor) : 0,
    integration_rate_percent: total.positive? ? ((integrated * 100 / total).floor) : 0,
    adapter_counts: adapter_counts
  }
})
RUBY
}

main() {
  local prompt_queue ghidra_status build_artifacts active_branches workspace_metrics

  if command -v ruby >/dev/null 2>&1; then
    local fast_json
    check_log_run_step "workspace context fast path"
    fast_json="$(emit_workspace_context_fast)"
    check_log_summary "GET_WORKSPACE_CONTEXT_OK"
    echo "$fast_json"
    printf 'GET_WORKSPACE_CONTEXT_OK prompts=%s\n' \
      "$(jq -r '.workspace_metrics.total_prompts' <<<"$fast_json")" >&2
    return
  fi

  check_log_run_step "build prompt_queue"
  prompt_queue=$(build_prompt_queue)
  check_log_run_step "ghidra_status"
  ghidra_status=$(get_ghidra_status)
  check_log_run_step "build_artifacts"
  build_artifacts=$(get_build_artifacts)
  check_log_run_step "active_branches"
  active_branches=$(get_active_branches)
  check_log_run_step "workspace_metrics"
  workspace_metrics=$(get_workspace_metrics "$prompt_queue")

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
    "$(jq -r '.total_prompts' <<<"$workspace_metrics")" >&2
}

main
