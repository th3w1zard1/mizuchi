#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/cli-agent.sh
source "$ROOT/scripts/lib/cli-agent.sh"

CLI="$ROOT/scripts/decomp-cli.sh"

usage_top() {
  cat <<EOF
Usage: ./scripts/decomp-cli.sh <command> [args]

Agent-friendly entry point for Mizuchi matching-decompilation workflows.
Verbose logging is the default on underlying scripts; pass --quiet where supported.

Commands:
  help [command]              Workspace help (JSON) or per-command examples
  ghidra-scout <target>       Point agent at Ghidra MCP discovery
  decomp-prompt <name>        Validate prompts/<name>/settings.yaml
  decomp-atlas <name>         Point agent at Decomp Atlas for examples
  decomp-function <name>      Run programmatic phase for prompts/<name>/
  decomp-integrate <name> <target.o>
                              Objdiff gate before integration
  list-prompts [status=...]   List prompt folders with optional status filter
  inject-context <agent> [--json]
  run-objdiff <target.o> <candidate.o>
  programmatic-phase --prompt <prompt-dir>
  verify-surface              Run workspace surface + guide validators

Examples:
  ./scripts/decomp-cli.sh help decomp-function
  ./scripts/decomp-cli.sh list-prompts status=matched
  ./scripts/decomp-cli.sh inject-context ghidra-binary-scout
  ./scripts/decomp-cli.sh programmatic-phase --prompt prompts/fun_00148020/
  ./scripts/decomp-cli.sh verify-surface
EOF
}

usage_command() {
  local cmd="${1:-}"
  case "$cmd" in
    help)
      cat <<EOF
Usage: decomp-cli.sh help [command]

Returns Mizuchi Workspace Help as JSON, or per-command examples when a
command name is supplied.

Examples:
  ./scripts/decomp-cli.sh help
  ./scripts/decomp-cli.sh help run-objdiff
EOF
      ;;
    ghidra-scout)
      cat <<EOF
Usage: decomp-cli.sh ghidra-scout <target>

Use /ghidra-scout or agdec-http MCP for interactive binary discovery.

Examples:
  ./scripts/decomp-cli.sh ghidra-scout 0x00401000
  ./scripts/decomp-cli.sh ghidra-scout FUN_00401000
EOF
      ;;
    decomp-prompt)
      cat <<EOF
Usage: decomp-cli.sh decomp-prompt <prompt-name>

Validates prompts/<name>/settings.yaml (functionName, targetObjectPath, asm).

Examples:
  ./scripts/decomp-cli.sh decomp-prompt fun_00148020
EOF
      ;;
    decomp-atlas)
      cat <<EOF
Usage: decomp-cli.sh decomp-atlas <prompt-name>

Use /decomp-atlas to gather similar matched examples for prompts/<name>/.

Examples:
  ./scripts/decomp-cli.sh decomp-atlas fun_00148020
EOF
      ;;
    decomp-function)
      cat <<EOF
Usage: decomp-cli.sh decomp-function <prompt-name>

Runs get-context → m2c → compile/objdiff → permuter for prompts/<name>/.

Examples:
  ./scripts/decomp-cli.sh decomp-function fun_00148020
EOF
      ;;
    decomp-integrate)
      cat <<EOF
Usage: decomp-cli.sh decomp-integrate <prompt-name> <target.o>

Runs objdiff gate: target.o vs prompts/<name>/build/candidate.o

Examples:
  ./scripts/decomp-cli.sh decomp-integrate fun_00148020 path/to/FUN_00401000.o
EOF
      ;;
    list-prompts)
      cat <<EOF
Usage: decomp-cli.sh list-prompts [status=<matched|in_progress|integrated|pending|blocked>]

Lists prompt folders under prompts/ with metadata.

Examples:
  ./scripts/decomp-cli.sh list-prompts
  ./scripts/decomp-cli.sh list-prompts status=matched
EOF
      ;;
    inject-context)
      cat <<EOF
Usage: decomp-cli.sh inject-context <agent-name> [--json]

Injects workspace context for a named agent (.cursor/agents/<name>.md).

Examples:
  ./scripts/decomp-cli.sh inject-context ghidra-binary-scout
  ./scripts/decomp-cli.sh inject-context decomp-function-agent --json
EOF
      ;;
    run-objdiff)
      cat <<EOF
Usage: decomp-cli.sh run-objdiff <target.o> <candidate.o> [--quiet]

Verifies byte-identical match via objdiff (exit 0 = 0 differences).

Examples:
  ./scripts/decomp-cli.sh run-objdiff target.o prompts/foo/build/candidate.o
  ./scripts/decomp-cli.sh run-objdiff target.o candidate.o --quiet
EOF
      ;;
    programmatic-phase)
      cat <<EOF
Usage: decomp-cli.sh programmatic-phase --prompt <prompt-dir> [--quiet] [options]

Direct wrapper for scripts/run-programmatic-phase.sh.

Examples:
  ./scripts/decomp-cli.sh programmatic-phase --prompt prompts/fun_00148020/
  ./scripts/decomp-cli.sh programmatic-phase --prompt prompts/fun_00148020/ --skip-permuter
EOF
      ;;
    verify-surface)
      cat <<EOF
Usage: decomp-cli.sh verify-surface [--quiet]

Runs scripts/verify-workspace-surface.sh (verbose by default).

Examples:
  ./scripts/decomp-cli.sh verify-surface
  ./scripts/decomp-cli.sh verify-surface --quiet
EOF
      ;;
    *)
      printf 'No detailed help for command: %s\n' "$cmd" >&2
      usage_top
      exit 2
      ;;
  esac
}

cmd="${1:-}"

if [[ -z "$cmd" || "$cmd" == "-h" || "$cmd" == "--help" ]]; then
  usage_top
  exit 0
fi

if [[ "$cmd" == "help" && -n "${2:-}" && "$2" != "--json" ]]; then
  if [[ "$2" == "-h" || "$2" == "--help" ]]; then
    usage_top
    exit 0
  fi
  usage_command "$2"
  exit 0
fi

shift || true

case "$cmd" in
  help)
    "$ROOT/scripts/help-command.sh"
    ;;
  ghidra-scout)
    target="${1:-}"
    if [[ -z "$target" ]]; then
      cli_agent_missing_arg "$CLI" "missing target for ghidra-scout" \
        "./scripts/decomp-cli.sh ghidra-scout 0x00401000"
    fi
    echo "Use /ghidra-scout for interactive MCP discovery of: $target"
    echo "mcp_server=agdec-http"
    ;;
  decomp-prompt)
    prompt_name="${1:-}"
    if [[ -z "$prompt_name" ]]; then
      cli_agent_missing_arg "$CLI" "missing prompt name" \
        "./scripts/decomp-cli.sh decomp-prompt fun_00148020"
    fi
    "$ROOT/scripts/validate-prompt-settings.sh" "$ROOT/prompts/$prompt_name"
    ;;
  decomp-atlas)
    prompt_name="${1:-}"
    if [[ -z "$prompt_name" ]]; then
      cli_agent_missing_arg "$CLI" "missing prompt name" \
        "./scripts/decomp-cli.sh decomp-atlas fun_00148020"
    fi
    echo "Use /decomp-atlas to gather similar matches for prompts/$prompt_name"
    ;;
  decomp-function)
    prompt_name="${1:-}"
    if [[ -z "$prompt_name" ]]; then
      cli_agent_missing_arg "$CLI" "missing prompt name" \
        "./scripts/decomp-cli.sh decomp-function fun_00148020"
    fi
    "$ROOT/scripts/run-programmatic-phase.sh" --prompt "$ROOT/prompts/$prompt_name" "${@:2}"
    ;;
  decomp-integrate)
    prompt_name="${1:-}"
    target_obj="${2:-}"
    if [[ -z "$prompt_name" || -z "$target_obj" ]]; then
      cli_agent_missing_arg "$CLI" "missing prompt name or target.o" \
        "./scripts/decomp-cli.sh decomp-integrate fun_00148020 path/to/target.o"
    fi
    "$ROOT/scripts/objdiff-gate.sh" "$target_obj" "$ROOT/prompts/$prompt_name/build/candidate.o" "${@:3}"
    ;;
  list-prompts)
    "$ROOT/scripts/list-prompts.sh" "$@"
    ;;
  inject-context)
    agent_name="${1:-}"
    if [[ -z "$agent_name" ]]; then
      cli_agent_missing_arg "$CLI" "missing agent name" \
        "./scripts/decomp-cli.sh inject-context ghidra-binary-scout"
    fi
    "$ROOT/scripts/inject-context.sh" "$@"
    ;;
  run-objdiff)
    target_obj="${1:-}"
    candidate_obj="${2:-}"
    if [[ -z "$target_obj" || -z "$candidate_obj" ]]; then
      cli_agent_missing_arg "$CLI" "missing target.o or candidate.o" \
        "./scripts/decomp-cli.sh run-objdiff target.o prompts/foo/build/candidate.o"
    fi
    "$ROOT/scripts/run-objdiff.sh" "$target_obj" "$candidate_obj" "${@:3}"
    ;;
  programmatic-phase)
    if [[ $# -lt 2 || "${1:-}" != "--prompt" || -z "${2:-}" ]]; then
      cli_agent_missing_arg "$CLI" "programmatic-phase requires --prompt <prompt-dir>" \
        "./scripts/decomp-cli.sh programmatic-phase --prompt prompts/fun_00148020/"
    fi
    "$ROOT/scripts/run-programmatic-phase.sh" "$@"
    ;;
  verify-surface)
    "$ROOT/scripts/verify-workspace-surface.sh" "$@"
    ;;
  *)
    cli_agent_unknown_command "$CLI" "$cmd"
    ;;
esac
