#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./scripts/decomp-cli.sh <command> [args]

Commands:
  ghidra-scout <target>
  decomp-prompt <prompt-name>
  decomp-atlas <prompt-name>
  decomp-function <prompt-name>
  decomp-integrate <prompt-name> <target.o>
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
  verify-surface)
    "$root_dir/scripts/verify-workspace-surface.sh"
    ;;
  *)
    echo "unknown command: $cmd" >&2
    usage
    exit 1
    ;;
esac
