#!/usr/bin/env bash
set -euo pipefail

# Help command backend
# Returns structured capability list: agents, commands, MCP tools, quick reference
# JSON on stdout; verbose trace + summary on stderr.

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/check-log.sh
source "$root_dir/scripts/lib/check-log.sh"
# shellcheck source=scripts/lib/guide-manifest.sh
source "$root_dir/scripts/lib/guide-manifest.sh"

quiet=0

usage() {
  cat <<EOF
Usage: help-command.sh [--quiet]

Emits Mizuchi help and entrypoint metadata as JSON on stdout.
Verbose trace and summary go to stderr (default).

Options:
  --quiet    Suppress verbose trace (keep summary)

Examples:
  ./scripts/help-command.sh
  ./scripts/help-command.sh --quiet | jq '.title'
  ./scripts/decomp-cli.sh help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet) quiet=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'help-command: unexpected argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

CHECK_LOG_QUIET=$quiet
check_log_init "help-command"
guide_manifest_load "$root_dir"
guide_manifest_trace_defaults "$root_dir"

# Helper: extract agent metadata from agent files
get_agents() {
  local agents_dir="$root_dir/.cursor/agents"
  local agents=()

  check_log_read_dir "$agents_dir" ".cursor/agents" "agent definitions" || true

  if [[ ! -d "$agents_dir" ]]; then
    echo "[]"
    return
  fi

  for agent_file in "$agents_dir"/*.md; do
    [[ -f "$agent_file" ]] || continue
    check_log_read_file "$agent_file" "$(guide_manifest_rel "$root_dir" "$agent_file")" "agent"

    # Extract name from frontmatter
    local agent_name
    agent_name=$(sed -n '/^name:/s/^name:[[:space:]]*//p' "$agent_file" | head -1)

    # Extract description from frontmatter
    local description
    description=$(sed -n '/^description:/s/^description:[[:space:]]*//p' "$agent_file" | head -1)

    # Extract capabilities - get lines between "capabilities:" and next "  -" or other field
    local capabilities_str
    capabilities_str=$(awk '/^capabilities:/{flag=1; next} /^[a-z]/{flag=0} flag {print}' "$agent_file")

    if [[ -n "$agent_name" ]]; then
      # Parse capabilities array from YAML
      local cap_array
      cap_array=$(echo "$capabilities_str" | sed -n 's/^[[:space:]]*-[[:space:]]*"\(.*\)"$/\1/p' | jq -R -s -c 'split("\n") | map(select(length > 0))')

      local agent_json
      agent_json=$(jq -n \
        --arg name "$agent_name" \
        --arg desc "$description" \
        --argjson caps "$cap_array" \
        '{name: $name, description: $desc, capabilities: $caps}')

      agents+=("$agent_json")
    fi
  done

  # Output as JSON array
  if [[ ${#agents[@]} -eq 0 ]]; then
    echo "[]"
  else
    printf '%s\n' "${agents[@]}" | jq -s '.'
  fi
}

# Helper: extract command metadata from COMMANDS
get_commands() {
  local commands_dir="$root_dir/.cursor/commands"
  local commands=()

  check_log_read_dir "$commands_dir" ".cursor/commands" "slash commands" || true

  if [[ ! -d "$commands_dir" ]]; then
    echo "[]"
    return
  fi

  for cmd_file in "$commands_dir"/*.md; do
    [[ -f "$cmd_file" ]] || continue

    # Skip help.md itself
    [[ "$(basename "$cmd_file")" == "help.md" ]] && continue
    check_log_read_file "$cmd_file" "$(guide_manifest_rel "$root_dir" "$cmd_file")" "command"

    local cmd_name
    cmd_name=$(basename "$cmd_file" .md)

    local description
    description=$(sed -n '/^description:/s/^description:[[:space:]]*//p' "$cmd_file" | head -1)

    if [[ -n "$cmd_name" ]]; then
      local cmd_json
      cmd_json=$(jq -n \
        --arg name "/$cmd_name" \
        --arg desc "$description" \
        '{name: $name, description: $desc}')

      commands+=("$cmd_json")
    fi
  done

  # Output as JSON array
  if [[ ${#commands[@]} -eq 0 ]]; then
    echo "[]"
  else
    printf '%s\n' "${commands[@]}" | jq -s '.'
  fi
}

# Helper: list MCP tools from mcp.json
get_mcp_tools() {
  local mcp_file="$root_dir/.cursor/mcp.json"
  local tools=()

  check_log_read_file "$mcp_file" ".cursor/mcp.json" "MCP config" || {
    echo "[]"
    return
  }

  # Extract MCP server names and format as tools
  local servers
  servers=$(jq -r '.mcpServers | keys[]' "$mcp_file" 2>/dev/null || echo "")

  while IFS= read -r server; do
    [[ -z "$server" ]] && continue
    check_log_mcp_server ".cursor/mcp.json" "$server" || true

    # Map server names to tool names
    case "$server" in
      workspace-context)
        tools+=("$(jq -n --arg n 'get_workspace_context' --arg d 'Query current workspace state: prompt queue, ghidra status, build artifacts, active branches' '{name: $n, description: $d}')")
        ;;
      list-prompts)
        tools+=("$(jq -n --arg n 'list_prompts' --arg d 'List available prompt folders with metadata; optional filter by status' '{name: $n, description: $d}')")
        ;;
      run-objdiff)
        tools+=("$(jq -n --arg n 'run_objdiff' --arg d 'Verify match by comparing target and candidate object files' '{name: $n, description: $d}')")
        ;;
      mizuchi)
        tools+=("$(jq -n --arg n 'compile_and_view_assembly' --arg d 'Compile C code and view resulting assembly for matching comparison' '{name: $n, description: $d}')")
        ;;
      agdec-http)
        tools+=("$(jq -n --arg n 'agentdecompile_mcp' --arg d 'AgentDecompile MCP: search-everything, get-function, get-call-graph, match-function' '{name: $n, description: $d}')")
        ;;
    esac
  done <<< "$servers"

  # Output as JSON array
  if [[ ${#tools[@]} -eq 0 ]]; then
    echo "[]"
  else
    printf '%s\n' "${tools[@]}" | jq -s '.'
  fi
}

# Helper: build quick reference section
get_quick_reference() {
  jq -n \
    --arg title "Quick Reference" \
    --arg section1_title "Primary CLI" \
    --arg section1 "./scripts/decomp-cli.sh help                           — show the canonical runtime surface
./scripts/decomp-cli.sh bootstrap-case --prompt prompts/<case-id>/ — initialize a normalized case workspace
./scripts/decomp-cli.sh status [status=...]              — inspect queue and case state
./scripts/decomp-cli.sh verify-surface                   — validate workspace parity and guide coverage" \
    --arg section2_title "Typical Workflow" \
    --arg section2 "1. Use /ghidra-scout or AgentDecompile MCP for discovery
2. Use decomp-cli bootstrap-case to initialize prompts/<case-id>/
3. Use /decomp-prompt or decomp-prompt-architect to refine prompt-local context
4. Use /decomp-function or ./scripts/decomp-cli.sh decomp-function <case-id>
5. Re-run verification and integration only after proof passes" \
    --arg section3_title "Common Queries (for agents)" \
    --arg section3 "get_workspace_context()      — Get full workspace snapshot
list_prompts(status=matched)   — List only matched functions
list_prompts(status=in_progress) — List work in progress
run_objdiff(target, candidate) — Verify match before integration" \
    --arg section4_title "Capabilities by Role" \
    --arg section4 "ghidra-binary-scout: Exploration only (no matching)
decomp-prompt-architect: Prompt assembly (no matching)
decomp-function-agent: End-to-end matching + verification" \
    '{title: $title, sections: [
       {title: $section1_title, content: $section1},
       {title: $section2_title, content: $section2},
       {title: $section3_title, content: $section3}
       ,
       {title: $section4_title, content: $section4}
     ]}'
}

# Main: assemble help output
main() {
  check_log_run_step "assemble workspace help JSON"

  local agents
  agents=$(get_agents)

  local commands
  commands=$(get_commands)

  local mcp_tools
  mcp_tools=$(get_mcp_tools)

  local quick_ref
  quick_ref=$(get_quick_reference)

  check_log_summary "HELP_COMMAND_OK"

  # Return all sections as structured JSON
  jq -n \
    --argjson agents "$agents" \
    --argjson commands "$commands" \
    --argjson mcp_tools "$mcp_tools" \
    --argjson quick_ref "$quick_ref" \
    '{
      title: "Mizuchi Workspace Help",
      timestamp: (now | todate),
      entrypoint: {
        title: "Primary Entrypoint",
        command: "./scripts/decomp-cli.sh",
        description: "Canonical shell surface for intake, orchestration, status, and proof-aware verification"
      },
      agents: {
        title: "Available Agents",
        description: "Mizuchi-specialized agents for proof-aware reverse-engineering tasks",
        items: $agents
      },
      commands: {
        title: "Available Commands",
        description: "Cursor slash-command parity surface around the primary runtime",
        items: $commands
      },
      mcp_tools: {
        title: "Available MCP Tools",
        description: "Workspace MCP primitives for queries and verification",
        items: $mcp_tools
      },
      quick_reference: $quick_ref
    }'
}

main "$@"
