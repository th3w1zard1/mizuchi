#!/usr/bin/env bash
# Initialize autonomous vacuum state from prompt manifests.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat >&2 <<'EOF'
usage:
  init-vacuum-state.sh [--prompts-dir prompts] [--queue state/queue.json]
                       [--scores state/scores.json] [--log-dir logs]
                       [--session state/vacuum-session.json]

Creates state/log directories, seeds queue.json from prompt case.yaml status,
scores pending prompts, and writes an initialization session receipt.
EOF
}

prompts_dir="$ROOT/prompts"
queue="state/queue.json"
scores="state/scores.json"
log_dir="logs"
session="state/vacuum-session.json"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompts-dir) prompts_dir="$2"; shift 2 ;;
    --queue) queue="$2"; shift 2 ;;
    --scores) scores="$2"; shift 2 ;;
    --log-dir) log_dir="$2"; shift 2 ;;
    --session) session="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "init-vacuum-state: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

queue_abs="$queue"
scores_abs="$scores"
log_dir_abs="$log_dir"
session_abs="$session"
[[ "$queue_abs" = /* ]] || queue_abs="$ROOT/$queue_abs"
[[ "$scores_abs" = /* ]] || scores_abs="$ROOT/$scores_abs"
[[ "$log_dir_abs" = /* ]] || log_dir_abs="$ROOT/$log_dir_abs"
[[ "$session_abs" = /* ]] || session_abs="$ROOT/$session_abs"

if [[ ! -d "$prompts_dir" ]]; then
  echo "init-vacuum-state: prompts dir not found: $prompts_dir" >&2
  exit 2
fi

mkdir -p "$(dirname "$queue_abs")" "$(dirname "$scores_abs")" "$log_dir_abs" "$(dirname "$session_abs")"

queue_tmp="$(mktemp)"
entries_tmp="$(mktemp)"
trap 'rm -f "$queue_tmp" "$entries_tmp"' EXIT

find "$prompts_dir" -mindepth 1 -maxdepth 1 -type d | sort | while IFS= read -r prompt; do
  name="$(basename "$prompt")"
  [[ "$name" == _* ]] && continue
  status="pending"
  if [[ -f "$prompt/case.yaml" ]]; then
    status="$(ruby -ryaml -e 'v=YAML.load_file(ARGV[0])["status"] rescue nil; puts(v || "pending")' "$prompt/case.yaml")"
  fi
  case "$status" in
    in-progress|in_progress) status="pending" ;;
    "") status="pending" ;;
  esac
  jq -n --arg name "$name" --arg status "$status" --arg prompt "$prompt" \
    '{name: $name, status: $status, promptDir: $prompt}'
done >"$entries_tmp"

jq -s '{
  schema: "mizuchi.vacuum-queue.v1",
  pending: [ .[] | select(.status == "pending") | {name: .name, score: 0, reason: "initialized from prompt manifest"} ],
  matched: [ .[] | select(.status == "matched") | {name: .name, score: 0, reason: "initialized from prompt manifest"} ],
  integrated: [ .[] | select(.status == "integrated") | {name: .name, score: 0, reason: "initialized from prompt manifest"} ],
  failed: [],
  difficult: [],
  attempts: {}
}' "$entries_tmp" >"$queue_tmp"

"$ROOT/scripts/lib/queue-state.sh" init --queue "$queue_abs" >/dev/null
"$ROOT/scripts/lib/queue-state.sh" summary --queue "$queue_abs" >/dev/null
cp "$queue_tmp" "$queue_abs"

"$ROOT/scripts/scorer.sh" --prompts-dir "$prompts_dir" --queue "$queue_abs" --update-queue --out "$scores_abs" >/dev/null

blocked_count="$(jq -s '[.[] | select(.status == "blocked")] | length' "$entries_tmp")"
total_count="$(jq -s 'length' "$entries_tmp")"
now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
summary="$("$ROOT/scripts/lib/queue-state.sh" summary --queue "$queue_abs")"

jq -n \
  --arg schema "mizuchi.vacuum-session.v1" \
  --arg status "initialized" \
  --arg updated_at "$now" \
  --arg queue "$queue_abs" \
  --arg scores "$scores_abs" \
  --arg log "$log_dir_abs/vacuum-progress.log" \
  --argjson blocked "$blocked_count" \
  --argjson total "$total_count" \
  '{
    schema: $schema,
    status: $status,
    updatedAt: $updated_at,
    queue: $queue,
    scores: $scores,
    log: $log,
    currentFunction: null,
    message: "vacuum state initialized",
    backoffSeconds: 0,
    debugLog: null,
    promptTotal: $total,
    blockedPrompts: $blocked
  }' >"$session_abs"

jq -n \
  --arg schema "mizuchi.vacuum-init.v1" \
  --arg status "initialized" \
  --arg queue "$queue_abs" \
  --arg scores "$scores_abs" \
  --arg logDir "$log_dir_abs" \
  --arg session "$session_abs" \
  --argjson promptTotal "$total_count" \
  --argjson blocked "$blocked_count" \
  --argjson summary "$summary" \
  '{
    schema: $schema,
    status: $status,
    promptTotal: $promptTotal,
    blockedPrompts: $blocked,
    queue: $queue,
    scores: $scores,
    logDir: $logDir,
    session: $session,
    summary: $summary
  }'
