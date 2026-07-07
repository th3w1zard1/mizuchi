#!/usr/bin/env bash
# Build a fixed one-shot matching prompt from a ReconstructKit prompt folder.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/lib/prompt-settings.sh
. "$ROOT/scripts/lib/prompt-settings.sh"
# shellcheck source=scripts/lib/case-metadata.sh
. "$ROOT/scripts/lib/case-metadata.sh"

matcher_prompt_usage() {
  echo "usage: matcher-prompt.sh --prompt <prompt-dir> [--out <file>]" >&2
}

matcher_prompt_examples() {
  local prompt_dir="$1"
  local prompts_root
  prompts_root="$(dirname "$prompt_dir")"
  find "$prompts_root" -mindepth 1 -maxdepth 1 -type d | sort | while IFS= read -r other; do
    [[ "$other" == "$prompt_dir" ]] && continue
    [[ -f "$other/case.yaml" && -f "$other/candidate.c" ]] || continue
    if [[ "$(case_metadata_get_default "$other" status "")" == "matched" ]]; then
      printf '### %s\n\n' "$(basename "$other")"
      sed -n '1,80p' "$other/candidate.c"
      printf '\n\n'
    fi
  done | sed -n '1,220p'
}

matcher_prompt_main() {
  local prompt_dir="" out_file=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --prompt) prompt_dir="$2"; shift 2 ;;
      --out) out_file="$2"; shift 2 ;;
      -h|--help) matcher_prompt_usage; return 0 ;;
      *) echo "matcher-prompt: unknown option: $1" >&2; matcher_prompt_usage; return 2 ;;
    esac
  done

  [[ -z "$prompt_dir" ]] && { matcher_prompt_usage; return 2; }
  prompt_settings_require_dir "$prompt_dir" || return $?
  prompt_dir="$(cd "$prompt_dir" && pwd)"

  local prompt_name function_name target_object target_family binary_path case_status proof_scope
  prompt_name="$(basename "$prompt_dir")"
  function_name="$(prompt_settings_get "$prompt_dir" functionName)"
  target_object="$(prompt_settings_get "$prompt_dir" targetObjectPath)"
  target_family="$(case_metadata_get_default "$prompt_dir" targetFamily "unknown")"
  binary_path="$(case_metadata_get_default "$prompt_dir" binaryPath "unknown")"
  case_status="$(case_metadata_get_default "$prompt_dir" status "pending")"
  proof_scope="$(case_metadata_get_default "$prompt_dir" proofScope "per-function-objdiff-zero")"

  local tmp
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' RETURN

  {
    cat <<EOF
You are matching a decompiled function from a compiled game binary.
Your task is to write C code that compiles to identical object code for exactly one function.

## Function Context
- Prompt: $prompt_name
- Function: $function_name
- Target object: $target_object
- Target family: $target_family
- Binary path: $binary_path
- Current case status: $case_status
- Proof scope: $proof_scope

## Build And Verify Contract
You have ONE SHOT. Do not ask for an interactive loop and do not depend on later feedback.
The candidate will be compiled by this workspace and accepted only if objdiff reports 0 differences.
Functional equivalence is insufficient; register allocation, stack layout, and instruction selection matter.

## Output Format
Return only one fenced C code block. Do not include prose outside the code block.

Example:

\`\`\`c
int $function_name(void) {
  return 0;
}
\`\`\`

## Prompt Folder
EOF
    sed -n '1,260p' "$prompt_dir/prompt.md"

    cat <<'EOF'

## settings.yaml Assembly
EOF
    prompt_settings_get "$prompt_dir" asm || true

    cat <<'EOF'

## Similar Matched Examples
EOF
    matcher_prompt_examples "$prompt_dir"

    cat <<'EOF'

## Final Instruction
Return the single best C implementation in one fenced C code block.
EOF
  } >"$tmp"

  if [[ -n "$out_file" ]]; then
    mkdir -p "$(dirname "$out_file")"
    cp "$tmp" "$out_file"
  fi
  cat "$tmp"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  matcher_prompt_main "$@"
fi
