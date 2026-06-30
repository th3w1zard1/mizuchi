#!/usr/bin/env bash

# Test suite for help-command.sh

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script_path="$root_dir/scripts/help-command.sh"

tests_run=0
tests_passed=0
tests_failed=0

run_test() {
  local test_name="$1"
  local test_fn="$2"
  
  ((tests_run++))
  printf "Test: %-60s " "$test_name"
  
  if $test_fn > /dev/null 2>&1; then
    echo "PASS"
    ((tests_passed++))
  else
    echo "FAIL"
    ((tests_failed++))
  fi
}

# Test 1: Script exists and is executable
test_script_exists() {
  [[ -f "$script_path" && -x "$script_path" ]]
}

# Test 2: Script returns valid JSON
test_returns_valid_json() {
  local output
  output=$("$script_path" 2>&1)
  echo "$output" | jq . > /dev/null 2>&1
}

# Test 3: JSON has required top-level fields
test_has_required_fields() {
  local output
  output=$("$script_path" 2>&1)
  
  # Check for required fields
  echo "$output" | jq '.title' > /dev/null 2>&1 || return 1
  echo "$output" | jq '.timestamp' > /dev/null 2>&1 || return 1
  echo "$output" | jq '.agents' > /dev/null 2>&1 || return 1
  echo "$output" | jq '.commands' > /dev/null 2>&1 || return 1
  echo "$output" | jq '.mcp_tools' > /dev/null 2>&1 || return 1
  echo "$output" | jq '.quick_reference' > /dev/null 2>&1 || return 1
}

# Test 4: Agents section has correct structure
test_agents_section_valid() {
  local output
  output=$("$script_path" 2>&1)
  
  # Check agents is an object with title, description, items
  echo "$output" | jq '.agents.title' > /dev/null 2>&1 || return 1
  echo "$output" | jq '.agents.description' > /dev/null 2>&1 || return 1
  echo "$output" | jq '.agents.items' > /dev/null 2>&1 || return 1
  
  # Check items is an array
  [[ "$(echo "$output" | jq -r '.agents.items | type')" == "array" ]]
}

# Test 5: Agents are populated
test_agents_not_empty() {
  local output
  output=$("$script_path" 2>&1)
  
  local count
  count=$(echo "$output" | jq '.agents.items | length')
  [[ "$count" -gt 0 ]]
}

# Test 6: Agent items have required fields
test_agent_items_have_fields() {
  local output
  output=$("$script_path" 2>&1)
  
  # Check first agent has name, description, capabilities
  echo "$output" | jq '.agents.items[0].name' > /dev/null 2>&1 || return 1
  echo "$output" | jq '.agents.items[0].description' > /dev/null 2>&1 || return 1
  echo "$output" | jq '.agents.items[0].capabilities' > /dev/null 2>&1 || return 1
}

# Test 7: Commands section has correct structure
test_commands_section_valid() {
  local output
  output=$("$script_path" 2>&1)
  
  echo "$output" | jq '.commands.title' > /dev/null 2>&1 || return 1
  echo "$output" | jq '.commands.description' > /dev/null 2>&1 || return 1
  echo "$output" | jq '.commands.items' > /dev/null 2>&1 || return 1
  
  [[ "$(echo "$output" | jq -r '.commands.items | type')" == "array" ]]
}

# Test 8: Commands are populated
test_commands_not_empty() {
  local output
  output=$("$script_path" 2>&1)
  
  local count
  count=$(echo "$output" | jq '.commands.items | length')
  [[ "$count" -gt 0 ]]
}

# Test 9: Command items have required fields
test_command_items_have_fields() {
  local output
  output=$("$script_path" 2>&1)
  
  echo "$output" | jq '.commands.items[0].name' > /dev/null 2>&1 || return 1
  echo "$output" | jq '.commands.items[0].description' > /dev/null 2>&1 || return 1
}

# Test 10: MCP tools section has correct structure
test_mcp_tools_section_valid() {
  local output
  output=$("$script_path" 2>&1)
  
  echo "$output" | jq '.mcp_tools.title' > /dev/null 2>&1 || return 1
  echo "$output" | jq '.mcp_tools.description' > /dev/null 2>&1 || return 1
  echo "$output" | jq '.mcp_tools.items' > /dev/null 2>&1 || return 1
  
  [[ "$(echo "$output" | jq -r '.mcp_tools.items | type')" == "array" ]]
}

# Test 11: MCP tools are populated
test_mcp_tools_not_empty() {
  local output
  output=$("$script_path" 2>&1)
  
  local count
  count=$(echo "$output" | jq '.mcp_tools.items | length')
  [[ "$count" -gt 0 ]]
}

# Test 12: MCP tool items have required fields
test_mcp_tool_items_have_fields() {
  local output
  output=$("$script_path" 2>&1)
  
  echo "$output" | jq '.mcp_tools.items[0].name' > /dev/null 2>&1 || return 1
  echo "$output" | jq '.mcp_tools.items[0].description' > /dev/null 2>&1 || return 1
}

# Test 13: Quick reference section has correct structure
test_quick_reference_valid() {
  local output
  output=$("$script_path" 2>&1)
  
  echo "$output" | jq '.quick_reference.title' > /dev/null 2>&1 || return 1
  echo "$output" | jq '.quick_reference.sections' > /dev/null 2>&1 || return 1
  
  [[ "$(echo "$output" | jq -r '.quick_reference.sections | type')" == "array" ]]
}

# Test 14: Quick reference has content
test_quick_reference_not_empty() {
  local output
  output=$("$script_path" 2>&1)
  
  local count
  count=$(echo "$output" | jq '.quick_reference.sections | length')
  [[ "$count" -gt 0 ]]
}

test_quick_reference_mentions_blocked_filter() {
  local output
  output=$("$script_path" 2>&1)

  echo "$output" | jq -r '.quick_reference.sections[].content' | grep -q 'list_prompts(status=blocked)'
}

test_quick_reference_mentions_decomp_function_receipt() {
  local output
  output=$("$script_path" 2>&1)

  echo "$output" | jq -r '.quick_reference.sections[].content' | grep -q 'build/decomp-function.json'
}

test_quick_reference_mentions_matcher() {
  local output
  output=$("$script_path" 2>&1)

  echo "$output" | jq -r '.quick_reference.sections[].content' | grep -q 'decomp-cli.sh matcher'
}

test_quick_reference_mentions_vacuum_init_primary() {
  local output
  output=$("$script_path" 2>&1)

  echo "$output" | jq -r '.quick_reference.sections[].content' | grep -q 'decomp-cli.sh vacuum init'
}

test_quick_reference_mentions_scorer() {
  local output
  output=$("$script_path" 2>&1)

  echo "$output" | jq -r '.quick_reference.sections[].content' | grep -q 'decomp-cli.sh scorer'
}

test_quick_reference_mentions_vacuum() {
  local output
  output=$("$script_path" 2>&1)

  echo "$output" | jq -r '.quick_reference.sections[].content' | grep -q 'decomp-cli.sh vacuum start' && \
  echo "$output" | jq -r '.quick_reference.sections[].content' | grep -q -- '--timeout 30m'
}

test_quick_reference_mentions_vacuum_reset() {
  local output
  output=$("$script_path" 2>&1)

  echo "$output" | jq -r '.quick_reference.sections[].content' | grep -q 'vacuum resume|status|reset-queue'
}

test_quick_reference_mentions_commit_verified_match() {
  local output
  output=$("$script_path" 2>&1)

  echo "$output" | jq -r '.quick_reference.sections[].content' | grep -q 'decomp-cli.sh commit-verified-match'
}

test_quick_reference_mentions_vacuum_init() {
  local output
  output=$("$script_path" 2>&1)

  echo "$output" | jq -r '.quick_reference.sections[].content' | grep -q 'decomp-cli.sh vacuum init'
}

test_quick_reference_mentions_import_one_shot_tasks() {
  local output
  output=$("$script_path" 2>&1)

  echo "$output" | jq -r '.quick_reference.sections[].content' | grep -q 'decomp-cli.sh import-one-shot-tasks'
}

test_quick_reference_mentions_one_shot_task_coverage() {
  local output
  output=$("$script_path" 2>&1)

  echo "$output" | jq -r '.quick_reference.sections[].content' | grep -q 'decomp-cli.sh one-shot-task-coverage'
}

# Test 15: Agents include expected names
test_expected_agent_names() {
  local output
  output=$("$script_path" 2>&1)
  
  # Check for expected agent names
  echo "$output" | jq '.agents.items[].name' | grep -q "decomp-prompt-architect"
}

# Test 16: Commands include expected names
test_expected_command_names() {
  local output
  output=$("$script_path" 2>&1)
  
  # Check for some expected commands
  echo "$output" | jq '.commands.items[].name' | grep -q "/decomp-function"
}

# Test 17: MCP tools include required tools
test_required_mcp_tools() {
  local output
  output=$("$script_path" 2>&1)
  
  # Check for required tools
  echo "$output" | jq '.mcp_tools.items[].name' | grep -q "get_workspace_context" && \
  echo "$output" | jq '.mcp_tools.items[].name' | grep -q "list_prompts" && \
  echo "$output" | jq '.mcp_tools.items[].name' | grep -q "run_objdiff" && \
  echo "$output" | jq '.mcp_tools.items[].name' | grep -q "integrate_verified_match"
}

# Test 18: Timestamp is valid ISO format
test_timestamp_valid_format() {
  local output
  output=$("$script_path" 2>&1)
  
  local timestamp
  timestamp=$(echo "$output" | jq -r '.timestamp')
  
  # Check if it looks like ISO 8601 format (e.g., 2026-05-29T...)
  [[ "$timestamp" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T ]]
}

# Test 19: help.md command file exists
test_help_command_file_exists() {
  [[ -f "$root_dir/.cursor/commands/help.md" ]]
}

# Test 20: Agent AGENT.md files have capabilities
test_agent_files_have_capabilities() {
  local agents_dir="$root_dir/.cursor/agents"
  local has_capabilities=0
  
  for agent_file in "$agents_dir"/*.md; do
    if grep -q "^capabilities:" "$agent_file"; then
      ((has_capabilities++))
    fi
  done
  
  # At least one agent should have capabilities
  [[ "$has_capabilities" -gt 0 ]]
}

# Main: run all tests
main() {
  echo "========================================"
  echo "Testing help-command.sh"
  echo "========================================"
  echo
  
  run_test "Script exists and is executable" test_script_exists
  run_test "Returns valid JSON" test_returns_valid_json
  run_test "JSON has required top-level fields" test_has_required_fields
  run_test "Agents section has correct structure" test_agents_section_valid
  run_test "Agents list is not empty" test_agents_not_empty
  run_test "Agent items have required fields (name, description, capabilities)" test_agent_items_have_fields
  run_test "Commands section has correct structure" test_commands_section_valid
  run_test "Commands list is not empty" test_commands_not_empty
  run_test "Command items have required fields (name, description)" test_command_items_have_fields
  run_test "MCP tools section has correct structure" test_mcp_tools_section_valid
  run_test "MCP tools list is not empty" test_mcp_tools_not_empty
  run_test "MCP tool items have required fields (name, description)" test_mcp_tool_items_have_fields
  run_test "Quick reference section has correct structure" test_quick_reference_valid
  run_test "Quick reference has content sections" test_quick_reference_not_empty
run_test "Quick reference mentions blocked prompt filter" test_quick_reference_mentions_blocked_filter
run_test "Quick reference mentions decomp-function receipt" test_quick_reference_mentions_decomp_function_receipt
run_test "Quick reference mentions matcher command" test_quick_reference_mentions_matcher
run_test "Quick reference mentions vacuum init as primary init" test_quick_reference_mentions_vacuum_init_primary
run_test "Quick reference mentions scorer command" test_quick_reference_mentions_scorer
run_test "Quick reference mentions vacuum command" test_quick_reference_mentions_vacuum
run_test "Quick reference mentions vacuum resume/reset" test_quick_reference_mentions_vacuum_reset
run_test "Quick reference mentions commit verified match" test_quick_reference_mentions_commit_verified_match
run_test "Quick reference mentions vacuum init" test_quick_reference_mentions_vacuum_init
run_test "Quick reference mentions import one-shot tasks" test_quick_reference_mentions_import_one_shot_tasks
run_test "Quick reference mentions one-shot task coverage" test_quick_reference_mentions_one_shot_task_coverage
  run_test "Agents include expected names" test_expected_agent_names
  run_test "Commands include expected names (decomp-function)" test_expected_command_names
  run_test "MCP tools include required tools (get_workspace_context, list_prompts, run_objdiff, integrate_verified_match)" test_required_mcp_tools
  run_test "Timestamp is valid ISO format" test_timestamp_valid_format
  run_test ".cursor/commands/help.md file exists" test_help_command_file_exists
  run_test "Agent AGENT.md files have capabilities section" test_agent_files_have_capabilities
  
  echo
  echo "========================================"
  echo "Test Results: $tests_passed passed, $tests_failed failed out of $tests_run total"
  echo "========================================"
  
  if [[ $tests_failed -eq 0 ]]; then
    return 0
  else
    return 1
  fi
}

main "$@"
