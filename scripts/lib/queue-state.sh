#!/usr/bin/env bash
# Persistent queue helpers for the autonomous matching loop.
set -euo pipefail

queue_empty_json() {
  jq -n '{
    schema: "mizuchi.vacuum-queue.v1",
    pending: [],
    matched: [],
    integrated: [],
    failed: [],
    difficult: [],
    attempts: {}
  }'
}

queue_validate_state() {
  local file="$1"
  jq -e '
    type == "object"
    and (.pending | type == "array")
    and (.matched | type == "array")
    and (.integrated | type == "array")
    and (.failed | type == "array")
    and (.difficult | type == "array")
    and ((.attempts // {}) | type == "object")
  ' "$file" >/dev/null
}

queue_load() {
  local file="$1"
  if [[ ! -f "$file" ]]; then
    queue_empty_json
    return 0
  fi
  queue_validate_state "$file" || {
    echo "queue-state: invalid queue JSON: $file" >&2
    return 1
  }
  jq '.schema = (.schema // "mizuchi.vacuum-queue.v1") | .attempts = (.attempts // {})' "$file"
}

queue_save() {
  local file="$1" input="${2:-}"
  local dir tmp
  dir="$(dirname "$file")"
  mkdir -p "$dir"
  tmp="$(mktemp "$dir/.queue.XXXXXX")"

  if [[ -n "$input" ]]; then
    jq '.' "$input" >"$tmp"
  else
    jq '.' >"$tmp"
  fi
  queue_validate_state "$tmp"
  mv "$tmp" "$file"
}

queue_seed_from_prompts() {
  local prompts_dir="$1"
  find "$prompts_dir" -mindepth 1 -maxdepth 1 -type d | sort | while IFS= read -r prompt; do
    local name status score
    name="$(basename "$prompt")"
    [[ "$name" == _* ]] && continue
    status="pending"
    if [[ -f "$prompt/case.yaml" ]]; then
      status="$(ruby -ryaml -e 'v=YAML.load_file(ARGV[0])["status"] rescue nil; puts(v || "pending")' "$prompt/case.yaml")"
    fi
    score=0
    case "$status" in
      matched|integrated|blocked) continue ;;
    esac
    jq -n --arg name "$name" --argjson score "$score" \
      '{name: $name, score: $score, reason: "seeded from prompt folder"}'
  done | jq -s '{
    schema: "mizuchi.vacuum-queue.v1",
    pending: .,
    matched: [],
    integrated: [],
    failed: [],
    difficult: [],
    attempts: {}
  }'
}

queue_get_next_pending() {
  local file="$1"
  queue_load "$file" | jq -c '
    .pending
    | sort_by([-(.score // 0), (.name // "")])
    | .[0] // empty
  '
}

queue_move_function() {
  local file="$1" name="$2" to_state="$3" reason="${4:-}"
  case "$to_state" in
    pending|matched|integrated|failed|difficult) ;;
    *) echo "queue-state: invalid destination state: $to_state" >&2; return 2 ;;
  esac

  local now tmp
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  tmp="$(mktemp)"
  queue_load "$file" | jq \
    --arg name "$name" \
    --arg to "$to_state" \
    --arg reason "$reason" \
    --arg now "$now" '
    def without_name: map(select(.name != $name));
    def find_entry:
      ([.pending[], .matched[], .integrated[], .failed[], .difficult[]] | map(select(.name == $name)) | .[0])
      // {name: $name, score: 0, reason: "created by queue_move_function"};
    find_entry as $entry
    | .pending = (.pending | without_name)
    | .matched = (.matched | without_name)
    | .integrated = (.integrated | without_name)
    | .failed = (.failed | without_name)
    | .difficult = (.difficult | without_name)
    | .[$to] += [($entry + {
        name: $name,
        updatedAt: $now
      } + (if $reason == "" then {} else {reason: $reason} end))]
  ' >"$tmp"
  queue_save "$file" "$tmp"
  rm -f "$tmp"
}

queue_increment_attempt() {
  local file="$1" name="$2" status="${3:-attempted}" message="${4:-}"
  local now tmp
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  tmp="$(mktemp)"
  queue_load "$file" | jq \
    --arg name "$name" \
    --arg status "$status" \
    --arg message "$message" \
    --arg now "$now" '
    .attempts = (.attempts // {})
    | .attempts[$name] = ((.attempts[$name] // {count: 0, history: []}) as $a
      | $a + {
          count: (($a.count // 0) + 1),
          lastAttempt: $now,
          lastStatus: $status,
          lastMessage: (if $message == "" then null else $message end),
          history: (($a.history // []) + [{
            timestamp: $now,
            status: $status,
            message: (if $message == "" then null else $message end)
          }])
        })
  ' >"$tmp"
  queue_save "$file" "$tmp"
  rm -f "$tmp"
}

queue_status_summary() {
  local file="$1"
  queue_load "$file" | jq '{
    schema: "mizuchi.vacuum-queue-summary.v1",
    pending: (.pending | length),
    matched: (.matched | length),
    integrated: (.integrated | length),
    failed: (.failed | length),
    difficult: (.difficult | length),
    attempts: ((.attempts // {}) | length),
    next: ((.pending | sort_by([-(.score // 0), (.name // "")]) | .[0]) // null)
  }'
}

queue_state_usage() {
  cat >&2 <<'EOF'
usage:
  queue-state.sh init --queue <state/queue.json> [--prompts-dir <prompts>]
  queue-state.sh next --queue <state/queue.json>
  queue-state.sh move --queue <state/queue.json> --name <fn> --to <state> [--reason <text>]
  queue-state.sh attempt --queue <state/queue.json> --name <fn> [--status <s>] [--message <text>]
  queue-state.sh summary --queue <state/queue.json>
EOF
}

queue_state_main() {
  local cmd="${1:-}" queue="" prompts_dir="" name="" to="" reason="" status="attempted" message=""
  [[ -n "$cmd" ]] && shift || true
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --queue) queue="$2"; shift 2 ;;
      --prompts-dir) prompts_dir="$2"; shift 2 ;;
      --name) name="$2"; shift 2 ;;
      --to) to="$2"; shift 2 ;;
      --reason) reason="$2"; shift 2 ;;
      --status) status="$2"; shift 2 ;;
      --message) message="$2"; shift 2 ;;
      -h|--help) queue_state_usage; return 0 ;;
      *) echo "queue-state: unknown option: $1" >&2; queue_state_usage; return 2 ;;
    esac
  done

  case "$cmd" in
    init)
      [[ -z "$queue" ]] && { queue_state_usage; return 2; }
      if [[ -n "$prompts_dir" ]]; then
        queue_seed_from_prompts "$prompts_dir" | queue_save "$queue"
      else
        queue_empty_json | queue_save "$queue"
      fi
      queue_load "$queue"
      ;;
    next)
      [[ -z "$queue" ]] && { queue_state_usage; return 2; }
      queue_get_next_pending "$queue"
      ;;
    move)
      [[ -z "$queue" || -z "$name" || -z "$to" ]] && { queue_state_usage; return 2; }
      queue_move_function "$queue" "$name" "$to" "$reason"
      queue_load "$queue"
      ;;
    attempt)
      [[ -z "$queue" || -z "$name" ]] && { queue_state_usage; return 2; }
      queue_increment_attempt "$queue" "$name" "$status" "$message"
      queue_load "$queue"
      ;;
    summary)
      [[ -z "$queue" ]] && { queue_state_usage; return 2; }
      queue_status_summary "$queue"
      ;;
    -h|--help|"")
      queue_state_usage
      ;;
    *)
      echo "queue-state: unknown command: $cmd" >&2
      queue_state_usage
      return 2
      ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  queue_state_main "$@"
fi
