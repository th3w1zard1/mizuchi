#!/usr/bin/env bash
# Score pending queue items; writes state/scores.json (stdout summary line).
set -euo pipefail

root_dir="${MIZUCHI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
state_dir="${MIZUCHI_STATE_DIR:-$root_dir/state}"
scores_file="${MIZUCHI_SCORES_FILE:-$state_dir/scores.json}"

# shellcheck source=scripts/lib/check-log.sh
source "$root_dir/scripts/lib/check-log.sh"
# shellcheck source=scripts/lib/guide-manifest.sh
source "$root_dir/scripts/lib/guide-manifest.sh"
# shellcheck source=scripts/lib/queue-state.sh
source "$root_dir/scripts/lib/queue-state.sh"
# shellcheck source=scripts/lib/scorer-ml-hooks.sh
source "$root_dir/scripts/lib/scorer-ml-hooks.sh"

usage() {
  cat <<EOF
Usage: scorer.sh [--quiet]

Scores pending queue functions and writes state/scores.json.

Options:
  --quiet   Suppress verbose trace (keep summary + result line)
  -h, --help  Show help

Examples:
  ./scripts/scorer.sh
  ./scripts/scorer.sh --quiet
  ./scripts/vacuum-cli.sh score
EOF
}

quiet=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet) quiet=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Error: unknown argument: $1" >&2; echo "  ./scripts/scorer.sh --help" >&2; exit 2 ;;
  esac
done

[[ "$quiet" -eq 1 ]] && check_log_configure --quiet
check_log_init "scorer"
guide_manifest_load "$root_dir"
guide_manifest_trace_defaults "$root_dir"

mkdir -p "$state_dir"
check_log_file_op "$(guide_manifest_rel "$root_dir" "$state_dir")" "ensure-dir"

queue_init
check_log_read_file "$queue_file" "$(guide_manifest_rel "$root_dir" "$queue_file")" "queue state"

queue="$(queue_load)"
pending_count="$(jq '.pending | length' <<<"$queue")"
scores_existed=0
[[ -f "$scores_file" ]] && scores_existed=1

if [[ "$pending_count" -eq 0 ]]; then
  jq -n '{scores: [], generated_at: now | todate}' >"$scores_file"
  check_log_file_written "$scores_file" "$root_dir" "$scores_existed"
  check_log_summary "SCORER_OK"
  echo "No pending functions"
  printf 'SCORER_OK scored=0\n' >&2
  exit 0
fi

scores='[]'
while IFS= read -r fn; do
  prompt_file="$GUIDE_PROMPTS_DIR/$fn/prompt.md"
  if [[ -f "$prompt_file" ]]; then
    check_log_read_file "$prompt_file" "$(guide_manifest_rel "$root_dir" "$prompt_file")" "prompt.md"
  else
    check_log_trace "read  $(guide_manifest_rel "$root_dir" "$prompt_file") (missing — default score)"
  fi
  scored="$(scorer_ml_predict "$prompt_file")"
  score="$(jq -r '.score' <<<"$scored")"
  reason="$(jq -r '.reason' <<<"$scored")"
  queue_set_score "$fn" "$score" "$reason"
  check_log_file_written "$queue_file" "$root_dir" 1
  check_log_trace "score $fn=$score ($reason)"
  scores="$(jq --arg n "$fn" --argjson s "$score" --arg r "$reason" '. += [{name:$n, score:$s, reason:$r}]' <<<"$scores")"
done < <(jq -r '.pending[].name' <<<"$queue")

scores="$(jq 'sort_by(.score) | reverse' <<<"$scores")"
jq -n --argjson scores "$scores" '{scores: $scores, generated_at: (now | todate)}' >"$scores_file"
check_log_file_written "$scores_file" "$root_dir" "$scores_existed"

count="$(jq '.scores|length' "$scores_file")"
check_log_summary "SCORER_OK"
echo "Scored ${count} functions"
printf 'SCORER_OK scored=%s file=%s\n' "$count" "$(guide_manifest_rel "$root_dir" "$scores_file")" >&2
