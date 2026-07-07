#!/usr/bin/env bash
# Deterministic prompt-local scoring for autonomous matching order.
set -euo pipefail

scorer_extract_asm() {
  local prompt_dir="$1"
  if [[ -f "$prompt_dir/settings.yaml" ]]; then
    ruby -ryaml -e '
      data = YAML.load_file(ARGV[0]) rescue {}
      asm = data["asm"]
      if asm && asm.to_s.strip != ""
        puts asm
        exit 0
      end
      exit 1
    ' "$prompt_dir/settings.yaml" 2>/dev/null && return 0
  fi

  if [[ -f "$prompt_dir/prompt.md" ]]; then
    awk '
      /^```asm[[:space:]]*$/ { in_asm=1; next }
      /^```[[:space:]]*$/ && in_asm { exit }
      in_asm { print }
    ' "$prompt_dir/prompt.md"
    return 0
  fi

  return 1
}

scorer_prompt_status() {
  local prompt_dir="$1"
  if [[ -f "$prompt_dir/case.yaml" ]]; then
    ruby -ryaml -e 'v=YAML.load_file(ARGV[0])["status"] rescue nil; puts(v || "pending")' "$prompt_dir/case.yaml"
  else
    printf 'pending\n'
  fi
}

scorer_measure_asm_json() {
  awk '
    function trim(s) { sub(/^[[:space:]]+/, "", s); sub(/[[:space:]]+$/, "", s); return s }
    function mnemonic(s, parts) {
      s = trim(s)
      sub(/[[:space:]].*$/, "", s)
      sub(/[,;].*$/, "", s)
      return tolower(s)
    }
    {
      line=$0
      sub(/[#;].*$/, "", line)
      line=trim(line)
      if (line == "") next
      if (line ~ /^[[:alnum:]_.$@]+:[[:space:]]*$/) {
        labels++
        next
      }
      if (line ~ /^\.[[:alpha:]_][[:alnum:]_.-]*( |$)/) {
        directives++
        next
      }
      instrs++
      m=mnemonic(line)
      if (m ~ /^j/ && m != "jmp") branches++
      if (m == "jmp" || m == "jmpq" || m == "ljmp") jumps++
      if (m ~ /^call/) calls++
      if (m ~ /^ret/) returns++
    }
    END {
      printf("{\"instructions\":%d,\"branches\":%d,\"jumps\":%d,\"calls\":%d,\"labels\":%d,\"returns\":%d,\"directives\":%d}\n", instrs+0, branches+0, jumps+0, calls+0, labels+0, returns+0, directives+0)
    }
  '
}

scorer_score_metrics_json() {
  jq -c '
    . as $m
    | (
        100
        - (($m.instructions // 0) * 1.25)
        - (($m.branches // 0) * 8)
        - (($m.jumps // 0) * 5)
        - (($m.calls // 0) * 6)
        + ([($m.labels // 0) * 1.5, 10] | min)
      ) as $raw
    | (if $raw < 0 then 0 elif $raw > 100 then 100 else $raw end) as $score
    | {
        score: (($score * 100 | round) / 100),
        reason: "\($m.instructions // 0) instrs, \($m.branches // 0) branches, \($m.jumps // 0) jumps, \($m.calls // 0) calls, \($m.labels // 0) labels",
        metrics: $m
      }
  '
}

scorer_score_prompt_json() {
  local prompt_dir="$1" name="${2:-}"
  [[ -n "$name" ]] || name="$(basename "$prompt_dir")"

  local asm metrics status mtime
  asm="$(scorer_extract_asm "$prompt_dir" || true)"
  status="$(scorer_prompt_status "$prompt_dir")"
  if [[ -d "$prompt_dir" ]]; then
    mtime="$(find "$prompt_dir" -maxdepth 1 -type f -printf '%T@\n' 2>/dev/null | sort -nr | head -n 1)"
  else
    mtime=""
  fi

  if [[ -z "${asm//[[:space:]]/}" ]]; then
    jq -n --arg name "$name" --arg prompt_dir "$prompt_dir" --arg status "$status" --arg mtime "${mtime:-0}" '{
      schema: "reconkit.scorer-entry.v1",
      name: $name,
      promptDir: $prompt_dir,
      status: $status,
      score: 0,
      reason: "missing asm context",
      metrics: {instructions: 0, branches: 0, jumps: 0, calls: 0, labels: 0, returns: 0, directives: 0},
      promptMtime: ($mtime | tonumber? // 0)
    }'
    return 0
  fi

  metrics="$(printf '%s\n' "$asm" | scorer_measure_asm_json)"
  printf '%s\n' "$metrics" | scorer_score_metrics_json | jq \
    --arg name "$name" \
    --arg prompt_dir "$prompt_dir" \
    --arg status "$status" \
    --arg mtime "${mtime:-0}" \
    '. + {
      schema: "reconkit.scorer-entry.v1",
      name: $name,
      promptDir: $prompt_dir,
      status: $status,
      promptMtime: ($mtime | tonumber? // 0)
    }'
}
