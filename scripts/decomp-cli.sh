#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./scripts/decomp-cli.sh <command> [args]

Commands:
  help
  ghidra-scout <target>
  decomp-prompt <prompt-name>
  decomp-atlas <prompt-name>
  decomp-function <prompt-name>
  decomp-integrate <prompt-name> <target.o>
  list-prompts [status=<matched|in_progress|integrated|pending>]
  inject-context <agent-name> [--json]
  run-objdiff <target.o> <candidate.o>
  programmatic-phase --prompt <prompt-dir>
  verify-surface
EOF
}

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cmd="${1:-}"

if [[ -z "$cmd" ]]; then
  usage
  exit 1
fi
shift || true

case "$cmd" in
  help)
    "$root_dir/scripts/help-command.sh"
    ;;
  ghidra-scout)
    target="${1:-}"
    if [[ -z "$target" ]]; then
      echo "missing target" >&2
      exit 1
    fi
    echo "Use /ghidra-scout for interactive MCP discovery of: $target"
    ;;
  decomp-prompt)
    prompt_name="${1:-}"
    if [[ -z "$prompt_name" ]]; then
      echo "missing prompt name" >&2
      exit 1
    fi
    "$root_dir/scripts/validate-prompt-settings.sh" "$root_dir/prompts/$prompt_name"
    ;;
  decomp-atlas)
    prompt_name="${1:-}"
    if [[ -z "$prompt_name" ]]; then
      echo "missing prompt name" >&2
      exit 1
    fi
    echo "Use /decomp-atlas to gather similar matches for prompts/$prompt_name"
    ;;
  decomp-function)
    prompt_name="${1:-}"
    if [[ -z "$prompt_name" ]]; then
      echo "missing prompt name" >&2
      exit 1
    fi
    "$root_dir/scripts/run-programmatic-phase.sh" --prompt "$root_dir/prompts/$prompt_name"
    ;;
  decomp-integrate)
    prompt_name="${1:-}"
    target_obj="${2:-}"
    if [[ -z "$prompt_name" || -z "$target_obj" ]]; then
      echo "usage: decomp-integrate <prompt-name> <target.o>" >&2
      exit 1
    fi
    "$root_dir/scripts/objdiff-gate.sh" "$target_obj" "$root_dir/prompts/$prompt_name/build/candidate.o"
    ;;
  list-prompts)
    "$root_dir/scripts/list-prompts.sh" "$@"
    ;;
  inject-context)
    agent_name="${1:-}"
    if [[ -z "$agent_name" ]]; then
      echo "usage: inject-context <agent-name> [--json]" >&2
      exit 1
    fi
    "$root_dir/scripts/inject-context.sh" "$@"
    ;;
  run-objdiff)
    target_obj="${1:-}"
    candidate_obj="${2:-}"
    if [[ -z "$target_obj" || -z "$candidate_obj" ]]; then
      echo "usage: run-objdiff <target.o> <candidate.o>" >&2
      exit 1
    fi
    "$root_dir/scripts/run-objdiff.sh" "$target_obj" "$candidate_obj"
    ;;
  programmatic-phase)
    if [[ $# -lt 2 || "${1:-}" != "--prompt" || -z "${2:-}" ]]; then
      echo "usage: programmatic-phase --prompt <prompt-dir>" >&2
      exit 1
    fi
    "$root_dir/scripts/run-programmatic-phase.sh" "$@"
    ;;
  verify-surface)
    "$root_dir/scripts/verify-workspace-surface.sh"
    ;;
  *)
    echo "unknown command: $cmd" >&2
    usage
    exit 1
    ;;
esac
