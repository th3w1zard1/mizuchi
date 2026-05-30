#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 --name <label>" >&2
  exit 2
}

name=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)
      shift
      [[ $# -gt 0 ]] || usage
      name="$1"
      shift
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "unexpected argument: $1" >&2
      usage
      ;;
  esac
done

[[ -n "$name" ]] || usage

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

printf 'LFG_SMOKE_OK name=%s\n' "$name"

surface_out="$("$ROOT/scripts/verify-workspace-surface.sh")"
[[ "$surface_out" == "WORKSPACE_SURFACE_OK" ]] || {
  echo "unexpected surface output: $surface_out" >&2
  exit 1
}

prompt_out="$("$ROOT/scripts/validate-prompt-status.sh" --quiet)"
[[ "$prompt_out" == "PROMPT_STATUS_OK" ]] || {
  echo "unexpected prompt status output: $prompt_out" >&2
  exit 1
}

printf '%s\n' "$surface_out"
printf '%s\n' "$prompt_out"
