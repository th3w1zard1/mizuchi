#!/usr/bin/env bash
set -euo pipefail

lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root_dir="${MIZUCHI_ROOT:-$(cd "$lib_dir/../.." && pwd)}"
state_dir="${MIZUCHI_STATE_DIR:-$root_dir/state}"
queue_file="${MIZUCHI_QUEUE_FILE:-$state_dir/queue.json}"

# shellcheck source=scripts/lib/queue-schema.sh
source "$lib_dir/queue-schema.sh"

queue_init() {
  mkdir -p "$state_dir"
  if [[ ! -f "$queue_file" ]]; then
    empty_queue_json >"$queue_file"
  fi
}

queue_validate() {
  jq -e '.pending and .matched and .integrated and .failed and .difficult and .attempts' "$queue_file" >/dev/null
}

queue_load() {
  queue_init
  queue_validate
  cat "$queue_file"
}

queue_save() {
  local content="${1:-}"
  local tmp
  tmp="$(mktemp "$state_dir/queue.XXXXXX.json")"
  printf '%s\n' "$content" >"$tmp"
  jq -e . "$tmp" >/dev/null
  mv "$tmp" "$queue_file"
}

queue_add_pending() {
  local name="${1:?missing function name}"
  local score="${2:-0}"
  local reason="${3:-unscored}"
  local q
  q="$(queue_load)"
  q="$(jq --arg n "$name" --argjson s "$score" --arg r "$reason" '
    .pending += [{name:$n, score:$s, reason:$r}] |
    .pending |= unique_by(.name)
  ' <<<"$q")"
  queue_save "$q"
}

queue_move() {
  local name="${1:?missing function name}"
  local from_state="${2:?missing from state}"
  local to_state="${3:?missing to state}"
  is_valid_queue_state "$from_state"
  is_valid_queue_state "$to_state"
  local q item
  q="$(queue_load)"
  item="$(jq --arg n "$name" --arg f "$from_state" -c '
    (.[$f] // []) | map(select(.name == $n)) | .[0] // empty
  ' <<<"$q")"
  if [[ -z "$item" ]]; then
    return 0
  fi
  q="$(jq --arg n "$name" --arg f "$from_state" --arg t "$to_state" --argjson item "$item" '
    .[$f] |= map(select(.name != $n)) |
    .[$t] += [$item] |
    .[$t] |= unique_by(.name)
  ' <<<"$q")"
  queue_save "$q"
}

queue_get_next_pending() {
  queue_load | jq -r '
    .pending
    | sort_by((.score // 0), .name)
    | reverse
    | .[0].name // empty
  '
}

queue_set_score() {
  local name="${1:?missing function name}"
  local score="${2:?missing score}"
  local reason="${3:-heuristic}"
  local q
  q="$(queue_load)"
  q="$(jq --arg n "$name" --argjson s "$score" --arg r "$reason" '
    .pending |= map(if .name == $n then .score = $s | .reason = $r else . end)
  ' <<<"$q")"
  queue_save "$q"
}

queue_increment_attempt() {
  local name="${1:?missing function name}"
  local q count
  q="$(queue_load)"
  q="$(jq --arg n "$name" '
    .attempts[$n] = ((.attempts[$n] // 0) + 1)
  ' <<<"$q")"
  queue_save "$q"
  count="$(jq -r --arg n "$name" '.attempts[$n] // 0' "$queue_file")"
  printf '%s\n' "$count"
}

queue_get_attempts() {
  local name="${1:?missing function name}"
  queue_load | jq -r --arg n "$name" '.attempts[$n] // 0'
}
