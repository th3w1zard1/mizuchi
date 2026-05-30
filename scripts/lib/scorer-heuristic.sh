#!/usr/bin/env bash
set -euo pipefail

count_asm_lines() {
  local prompt_file="${1:?missing prompt file}"
  awk '
    /^```asm/ { in_asm=1; next }
    /^```/ && in_asm { in_asm=0; next }
    in_asm { print }
  ' "$prompt_file"
}

score_function_from_prompt() {
  local prompt_file="${1:?missing prompt file}"
  if [[ ! -f "$prompt_file" ]]; then
    jq -n '{score: 0, reason: "missing prompt.md"}'
    return
  fi

  local asm instr branches labels
  asm="$(count_asm_lines "$prompt_file")"
  instr="$(grep -Ec '^\s*[a-z]' <<<"$asm" || true)"
  branches="$(grep -Eci '\b(b|beq|bne|blt|bgt|ble|bge|jmp|je|jne|jg|jl|ja|jb)\b' <<<"$asm" || true)"
  labels="$(grep -Ec '^\s*[A-Za-z_][A-Za-z0-9_]*:' <<<"$asm" || true)"

  local score
  score=$((100 - (branches * 5) - (instr / 5) + (labels * 2)))
  if (( score < 0 )); then
    score=0
  fi

  jq -n --argjson score "$score" --arg instr "$instr" --arg branches "$branches" --arg labels "$labels" \
    '{score: $score, reason: ("\($instr) instrs, \($branches) branches, \($labels) labels")}'
}
