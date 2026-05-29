#!/usr/bin/env bash
# Bootstrap a Mizuchi prompt folder with required files for RE workflows.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'EOF'
usage: bootstrap-re-pipeline.sh --prompt <prompts/<name>/>

Creates prompt.md and settings.yaml when missing, then validates settings format.
Existing files are preserved.
EOF
}

PROMPT_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) PROMPT_DIR="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$PROMPT_DIR" ]]; then
  echo "missing --prompt" >&2
  usage
  exit 2
fi

mkdir -p "$PROMPT_DIR"

prompt_md="$PROMPT_DIR/prompt.md"
settings_yaml="$PROMPT_DIR/settings.yaml"

if [[ ! -f "$prompt_md" ]]; then
  cat >"$prompt_md" <<'EOF'
# Decompiled Function Prompt

## Goal
Produce C code for the target function and iterate until objdiff reaches 0.

## Notes
- Run the programmatic phase first.
- Preserve behavior and ABI.
EOF
fi

if [[ ! -f "$settings_yaml" ]]; then
  cat >"$settings_yaml" <<'EOF'
functionName: replace_me
targetObjectPath: path/to/{{functionName}}.o
asm: |
  # Paste Ghidra/objdump assembly for the target function.
  nop
  nop
  nop
EOF
fi

"$ROOT/scripts/validate-prompt-settings.sh" "$PROMPT_DIR"
echo "RE_BOOTSTRAP_OK prompt=$PROMPT_DIR"
