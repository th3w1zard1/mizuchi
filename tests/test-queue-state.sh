#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

mkdir -p "$work_dir/scripts/lib" "$work_dir/state"
cp "$root_dir/scripts/lib/queue-schema.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/queue-state.sh" "$work_dir/scripts/lib/"

export MIZUCHI_ROOT="$work_dir"
export MIZUCHI_STATE_DIR="$work_dir/state"

# shellcheck source=/dev/null
source "$work_dir/scripts/lib/queue-state.sh"

queue_init
queue_validate

queue_add_pending "fun_a" 10 "seed"
queue_add_pending "fun_b" 90 "seed"
next="$(queue_get_next_pending)"
[[ "$next" == "fun_b" ]]

queue_move "fun_b" pending matched
[[ "$(queue_load | jq '.matched|length')" -eq 1 ]]
[[ "$(queue_load | jq '.pending|length')" -eq 1 ]]

count="$(queue_increment_attempt "fun_a")"
[[ "$count" -eq 1 ]]
[[ "$(queue_get_attempts "fun_a")" -eq 1 ]]

echo "test-queue-state: PASS"
