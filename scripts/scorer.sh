#!/usr/bin/env bash
set -euo pipefail

root_dir="${MIZUCHI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
state_dir="${MIZUCHI_STATE_DIR:-$root_dir/state}"
scores_file="${MIZUCHI_SCORES_FILE:-$state_dir/scores.json}"

# shellcheck source=scripts/lib/queue-state.sh
source "$root_dir/scripts/lib/queue-state.sh"
# shellcheck source=scripts/lib/scorer-ml-hooks.sh
source "$root_dir/scripts/lib/scorer-ml-hooks.sh"

mkdir -p "$state_dir"
queue_init

queue="$(queue_load)"
pending_count="$(jq '.pending | length' <<<"$queue")"
if [[ "$pending_count" -eq 0 ]]; then
  jq -n '{scores: [], generated_at: now | todate}' >"$scores_file"
  echo "No pending functions"
  exit 0
fi

scores='[]'
while IFS= read -r fn; do
  prompt_file="$root_dir/prompts/$fn/prompt.md"
  scored="$(scorer_ml_predict "$prompt_file")"
  score="$(jq -r '.score' <<<"$scored")"
  reason="$(jq -r '.reason' <<<"$scored")"
  queue_set_score "$fn" "$score" "$reason"
  scores="$(jq --arg n "$fn" --argjson s "$score" --arg r "$reason" '. += [{name:$n, score:$s, reason:$r}]' <<<"$scores")"
done < <(jq -r '.pending[].name' <<<"$queue")

scores="$(jq 'sort_by(.score) | reverse' <<<"$scores")"
jq -n --argjson scores "$scores" '{scores: $scores, generated_at: (now | todate)}' >"$scores_file"
echo "Scored $(jq '.scores|length' "$scores_file") functions"
