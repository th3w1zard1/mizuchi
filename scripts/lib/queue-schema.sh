#!/usr/bin/env bash
set -euo pipefail

queue_states_json() {
  jq -n '["pending","matched","integrated","failed","difficult"]'
}

is_valid_queue_state() {
  local state="${1:-}"
  jq -e --arg s "$state" '.[] | select(. == $s)' <<<"$(queue_states_json)" >/dev/null
}

empty_queue_json() {
  jq -n '{
    pending: [],
    matched: [],
    integrated: [],
    failed: [],
    difficult: [],
    attempts: {}
  }'
}
