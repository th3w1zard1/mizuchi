#!/usr/bin/env bash
set -euo pipefail

root_dir="${MIZUCHI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# shellcheck source=scripts/lib/queue-state.sh
source "$root_dir/scripts/lib/queue-state.sh"

queue_init
queue_save "$(empty_queue_json)"

prompts_dir="$root_dir/prompts"
if [[ -d "$prompts_dir" ]]; then
  while IFS= read -r prompt; do
    queue_add_pending "$prompt" 0 "unscored"
  done < <(find "$prompts_dir" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | grep -v '^_template$' | sort)
fi

"$root_dir/scripts/scorer.sh" >/dev/null || true
echo "Initialized vacuum state at $queue_file"
