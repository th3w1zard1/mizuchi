#!/usr/bin/env bash
# Shared helpers for agent-friendly CLIs: layered help, examples, actionable errors.

cli_agent_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd
}

cli_agent_missing_arg() {
  local script="$1"
  local message="$2"
  local example="$3"
  printf 'Error: %s\n' "$message" >&2
  printf '  %s\n' "$example" >&2
  exit 2
}

cli_agent_unknown_command() {
  local script="$1"
  local cmd="$2"
  printf 'Error: unknown command: %s\n' "$cmd" >&2
  printf '  %s help\n' "$script" >&2
  printf '  %s help <command>\n' "$script" >&2
  exit 2
}
