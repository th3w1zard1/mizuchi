#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/scorer-heuristic.sh
source "$ROOT/scripts/lib/scorer-heuristic.sh"
# shellcheck source=scripts/lib/scorer-ml-hooks.sh
source "$ROOT/scripts/lib/scorer-ml-hooks.sh"

usage() {
  cat >&2 <<'EOF'
usage:
  scorer.sh --prompts-dir <prompts> [--out <state/scores.json>] [--queue <state/queue.json>] [--update-queue]
  scorer.sh --prompt <prompt-dir-or-name> [--prompts-dir <prompts>]

Scores prompt folders for autonomous matching. Higher score means easier first.
EOF
}

prompt_root="$ROOT/prompts"
prompt_arg=""
out=""
queue=""
update_queue=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompts-dir) prompt_root="$2"; shift 2 ;;
    --prompt) prompt_arg="$2"; shift 2 ;;
    --out) out="$2"; shift 2 ;;
    --queue) queue="$2"; shift 2 ;;
    --update-queue) update_queue=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "scorer: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

score_one() {
  local prompt="$1" name
  if [[ -d "$prompt" ]]; then
    name="$(basename "$prompt")"
  else
    name="$prompt"
    prompt="$prompt_root/$name"
  fi
  scorer_ml_predict_prompt_json "$prompt" "$name"
}

write_json() {
  local path="$1"
  mkdir -p "$(dirname "$path")"
  jq '.' >"$path"
}

if [[ -n "$prompt_arg" ]]; then
  score_one "$prompt_arg"
  exit 0
fi

if [[ ! -d "$prompt_root" ]]; then
  echo "scorer: prompts dir not found: $prompt_root" >&2
  exit 2
fi

entries_tmp="$(mktemp)"
trap 'rm -f "$entries_tmp" "$queue_tmp"' EXIT
queue_tmp=""

if [[ -n "$queue" && -f "$queue" ]]; then
  jq -r '.pending[]?.name' "$queue" | while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    [[ "$name" == _* ]] && continue
    score_one "$name"
  done | jq -s '.' >"$entries_tmp"
else
  find "$prompt_root" -mindepth 1 -maxdepth 1 -type d | sort | while IFS= read -r prompt; do
    name="$(basename "$prompt")"
    [[ "$name" == _* ]] && continue
    status="$(scorer_prompt_status "$prompt")"
    case "$status" in
      matched|integrated|blocked) continue ;;
    esac
    score_one "$prompt"
  done | jq -s '.' >"$entries_tmp"
fi

report="$(
  jq -n \
    --arg schema "reconkit.scorer.v1" \
    --arg generated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg prompts_dir "$prompt_root" \
    --arg queue_path "$queue" \
    --slurpfile entries "$entries_tmp" \
    --argjson ml "$(scorer_ml_metadata_json)" \
    '{
      schema: $schema,
      generatedAt: $generated_at,
      promptsDir: $prompts_dir,
      queue: (if $queue_path == "" then null else $queue_path end),
      scorer: (if $ml.enabled then "ml" else "heuristic" end),
      ml: $ml,
      entries: ($entries[0] | sort_by([-(.score // 0), (.name // "")])),
      count: ($entries[0] | length)
    }'
)"

if [[ -n "$out" ]]; then
  printf '%s\n' "$report" | write_json "$out"
fi

if [[ "$update_queue" == true ]]; then
  if [[ -z "$queue" ]]; then
    echo "scorer: --update-queue requires --queue" >&2
    exit 2
  fi
  queue_tmp="$(mktemp)"
  if [[ -f "$queue" ]]; then
    jq '.' "$queue" >"$queue_tmp"
  else
    "$ROOT/scripts/lib/queue-state.sh" init --queue "$queue" --prompts-dir "$prompt_root" >/dev/null
    jq '.' "$queue" >"$queue_tmp"
  fi
  jq --argjson entries "$(printf '%s\n' "$report" | jq '.entries')" '
    .pending = (
      .pending
      | map(. as $p
        | ([$entries[]? | select(.name == $p.name)] | .[0]) as $s
        | if $s then
            $p + {
              score: $s.score,
              reason: $s.reason,
              metrics: $s.metrics,
              scoredAt: (now | strftime("%Y-%m-%dT%H:%M:%SZ"))
            }
          else $p end)
      | sort_by([-(.score // 0), (.name // "")])
    )
  ' "$queue_tmp" >"$queue.tmp.$$"
  mv "$queue.tmp.$$" "$queue"
fi

printf '%s\n' "$report"
