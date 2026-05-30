#!/usr/bin/env bash
# Fail-closed check: notes.md must not claim "matched" without proof artifacts.
# Usage: validate-prompt-status.sh [--quiet]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
quiet=0

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

read_target_object_path() {
  local settings="$1"
  if [[ ! -f "$settings" ]]; then
    echo ""
    return 0
  fi
  if command -v ruby >/dev/null 2>&1; then
    ruby -ryaml -e 'print YAML.load_file(ARGV[0])["targetObjectPath"].to_s' "$settings" 2>/dev/null || true
    return 0
  fi
  grep -E '^targetObjectPath:' "$settings" | head -1 | sed 's/^targetObjectPath:[[:space:]]*//' || true
}

notes_claims_matched() {
  local notes="$1"
  grep -qiE '^[[:space:]]*\*?\*?status:[[:space:]]*matched\*?\*?[[:space:]]*$' "$notes"
}

errors=0

for prompt_dir in "$ROOT"/prompts/*/; do
  [[ -d "$prompt_dir" ]] || continue
  name="$(basename "$prompt_dir")"
  [[ "$name" == "_template" ]] && continue

  notes="$prompt_dir/notes.md"
  settings="$prompt_dir/settings.yaml"
  [[ -f "$notes" ]] || continue

  if ! notes_claims_matched "$notes"; then
    continue
  fi

  target_rel="$(read_target_object_path "$settings")"
  if [[ -z "$target_rel" ]]; then
    log "invalid: prompts/$name claims matched but settings.yaml has no targetObjectPath"
    errors=1
    continue
  fi

  target_abs="$ROOT/$target_rel"
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
