#!/usr/bin/env bash
set -euo pipefail

# Help command backend
# Returns structured capability list: agents, commands, MCP tools, quick reference

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Helper: extract agent metadata from agent files
get_agents() {
  local agents_dir="$root_dir/.cursor/agents"
  local agents=()
  
  if [[ ! -d "$agents_dir" ]]; then
    echo "[]"
    return
  fi
  
  for agent_file in "$agents_dir"/*.md; do
    [[ -f "$agent_file" ]] || continue
    
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
  
  if [[ ! -d "$commands_dir" ]]; then
    echo "[]"
    return
  fi
  
  for cmd_file in "$commands_dir"/*.md; do
    [[ -f "$cmd_file" ]] || continue
    
    # Skip help.md itself
    [[ "$(basename "$cmd_file")" == "help.md" ]] && continue
    
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
  
  if [[ ! -f "$mcp_file" ]]; then
    echo "[]"
    return
  fi
  
  # Extract MCP server names and format as tools
  local servers
  servers=$(jq -r '.mcpServers | keys[]' "$mcp_file" 2>/dev/null || echo "")
  
  while IFS= read -r server; do
    [[ -z "$server" ]] && continue
    
    # Map server names to tool names
    case "$server" in
      workspace-context)
        tools+=("$(jq -n --arg n 'get_workspace_context' --arg d 'Query current workspace state: prompt queue, build artifacts, active branches' '{name: $n, description: $d}')")
        ;;
      list-prompts)
        tools+=("$(jq -n --arg n 'list_prompts' --arg d 'List available prompt folders with metadata; optional filter by status' '{name: $n, description: $d}')")
        ;;
      run-objdiff)
        tools+=("$(jq -n --arg n 'run_objdiff' --arg d 'Verify match by comparing target and candidate object files' '{name: $n, description: $d}')")
        ;;
      mizuchi)
        tools+=("$(jq -n --arg n 'compile_and_view_assembly' --arg d 'Compile C code and view resulting assembly for matching comparison' '{name: $n, description: $d}')")
        tools+=("$(jq -n --arg n 'integrate_verified_match' --arg d 'Re-run match verification, land candidate source, and record integration receipt' '{name: $n, description: $d}')")
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
    --arg section1_title "Typical Workflow" \
    --arg section1 "1. Use /decomp-prompt or decomp-prompt-architect to create a prompt folder from target assembly, m2c seed, and known source context
2a. Import one-shot package tasks with ./scripts/decomp-cli.sh import-one-shot-tasks --package target/<app>/one-shot-source --prompts-dir prompts
2b. Audit imported task coverage with ./scripts/decomp-cli.sh one-shot-task-coverage --package target/<app>/one-shot-source --prompts-dir prompts --queue state/queue.json
3. Initialize autonomous vacuum state with ./scripts/decomp-cli.sh vacuum init --queue state/queue.json --prompts-dir prompts
4. Score queue order with ./scripts/decomp-cli.sh scorer --queue state/queue.json --update-queue --out state/scores.json
5. Run ./scripts/decomp-cli.sh vacuum start --queue state/queue.json --max-functions 1 --timeout 30m for persistent autonomous orchestration
6. Use ./scripts/decomp-cli.sh matcher <name> for a fixed one-shot trial.c, or /decomp-function for full programmatic → AI flow
7. Inspect build/matcher.json and build/decomp-function.json for receipts
8. Optionally commit a verified unit with ./scripts/decomp-cli.sh commit-verified-match --prompt prompts/<name> --dry-run
9. Use /decomp-integrate to land matched function into source tree
10. Resume or triage with ./scripts/decomp-cli.sh vacuum resume|status|reset-queue --name <fn>
11. Check status anytime with get_workspace_context(), list_prompts(), or queue summary" \
    --arg section2_title "Common Queries (for agents)" \
    --arg section2 "get_workspace_context()      — Get full workspace snapshot
list_prompts(status=matched)   — List only matched functions
list_prompts(status=blocked)   — List prompts blocked by missing proof inputs/toolchains
list_prompts(status=in_progress) — List work in progress
run_objdiff(target, candidate) — Verify match before integration
integrate_verified_match(prompt, source_out) — Land verified match with receipt" \
    --arg section3_title "Capabilities by Role" \
    --arg section3 "decomp-prompt-architect: Prompt assembly (no matching)
decomp-function-agent: End-to-end matching + verification" \
    '{title: $title, sections: [
       {title: $section1_title, content: $section1},
       {title: $section2_title, content: $section2},
       {title: $section3_title, content: $section3}
     ]}'
}

# Main: assemble help output
main() {
  local agents
  agents=$(get_agents)
  
  local commands
  commands=$(get_commands)
  
  local mcp_tools
  mcp_tools=$(get_mcp_tools)
  
  local quick_ref
  quick_ref=$(get_quick_reference)
  
  # Return all sections as structured JSON
  jq -n \
    --argjson agents "$agents" \
    --argjson commands "$commands" \
    --argjson mcp_tools "$mcp_tools" \
    --argjson quick_ref "$quick_ref" \
    '{
      title: "Mizuchi Workspace Help",
      timestamp: (now | todate),
      agents: {
        title: "Available Agents",
        description: "Mizuchi-specialized agents for matching-decompilation tasks",
        items: $agents
      },
      commands: {
        title: "Available Commands",
        description: "Slash commands for running agents and operations",
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
