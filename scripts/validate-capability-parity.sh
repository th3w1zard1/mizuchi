#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cli_file="$ROOT/scripts/decomp-cli.sh"
matrix_file="$ROOT/CAPABILITY_MATRIX.md"

required_cli_commands=(
  help
  ghidra-scout
  decomp-prompt
  decomp-atlas
  decomp-function
  decomp-integrate
  list-prompts
  inject-context
  run-objdiff
  programmatic-phase
  verify-surface
)

missing=0
for cmd in "${required_cli_commands[@]}"; do
  if ! grep -qE "^[[:space:]]+$cmd([[:space:]]|$)" "$cli_file"; then
    echo "missing CLI command in usage: $cmd" >&2
    missing=1
  fi
done

declare -A matrix_tokens=(
  ["list_prompts"]="list-prompts"
  ["run_objdiff"]="run-objdiff"
  ["inject-context"]="inject-context"
)

for token in "${!matrix_tokens[@]}"; do
  if ! grep -q "$token" "$matrix_file"; then
    echo "missing capability token in CAPABILITY_MATRIX.md: $token" >&2
    missing=1
  fi
done

if [[ "$missing" -ne 0 ]]; then
  exit 1
fi

echo "CAPABILITY_PARITY_OK"
