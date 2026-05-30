#!/usr/bin/env bash
set -euo pipefail

matcher_build_prompt() {
  local prompt_name="${1:?missing prompt name}"
  local root_dir="${MIZUCHI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
  local prompt_file="$root_dir/prompts/$prompt_name/prompt.md"
  if [[ ! -f "$prompt_file" ]]; then
    echo "Prompt file missing: $prompt_file" >&2
    return 1
  fi

  cat <<EOF
You are matching a decompiled function from a binary. Produce only one C code block.

Constraints:
- One-shot only (no iterative loop)
- Keep behavior faithful to assembly
- Output only a single \`\`\`c code block

Source prompt:
$(cat "$prompt_file")
EOF
}
