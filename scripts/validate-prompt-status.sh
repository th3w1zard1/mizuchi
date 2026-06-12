#!/usr/bin/env bash
# Fail-closed check: notes.md must not claim "matched" without proof artifacts.
# Usage: validate-prompt-status.sh [--quiet]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
quiet=0

# shellcheck source=scripts/lib/prompt-metadata.sh
source "$ROOT/scripts/lib/prompt-metadata.sh"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet) quiet=1; shift ;;
    -h|--help)
      echo "usage: $0 [--quiet]" >&2
      exit 2
      ;;
    *) echo "unexpected argument: $1" >&2; exit 2 ;;
  esac
done

log() {
  [[ "$quiet" -eq 0 ]] && echo "$1" >&2
}

errors=0

for prompt_dir in "$ROOT"/prompts/*/; do
  [[ -d "$prompt_dir" ]] || continue
  name="$(basename "$prompt_dir")"
  [[ "$name" == "_template" ]] && continue

  notes="$prompt_dir/notes.md"
  [[ -f "$notes" ]] || continue

  if [[ "$(prompt_metadata_status "$prompt_dir")" != "matched" ]]; then
    continue
  fi

  target_rel="$(prompt_metadata_proof_target "$prompt_dir")"
  if [[ -z "$target_rel" ]]; then
    log "invalid: prompts/$name claims matched but no canonical proof target is configured"
    errors=1
    continue
  fi

  if [[ "$target_rel" == /* ]]; then
    target_abs="$target_rel"
  else
    target_abs="$ROOT/$target_rel"
  fi
  if [[ ! -f "$target_abs" ]]; then
    log "invalid: prompts/$name claims matched but golden object missing: $target_rel"
    errors=1
    continue
  fi

  candidate="$prompt_dir/build/candidate.o"
  if [[ ! -f "$candidate" ]]; then
    log "invalid: prompts/$name claims matched but candidate missing: prompts/$name/build/candidate.o"
    errors=1
    continue
  fi

  if command -v objdiff >/dev/null 2>&1; then
    if ! "$ROOT/scripts/objdiff-gate.sh" "$target_abs" "$candidate" --quiet; then
      log "invalid: prompts/$name claims matched but objdiff-gate failed"
      errors=1
    fi
  else
    log "invalid: prompts/$name claims matched; objdiff not on PATH — cannot verify 0 diffs"
    errors=1
  fi
done

if [[ "$errors" -ne 0 ]]; then
  exit 1
fi

echo "PROMPT_STATUS_OK"
