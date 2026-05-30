#!/usr/bin/env bash
# Seed vacuum queue from prompts/ and run initial scorer pass.
set -euo pipefail

root_dir="${MIZUCHI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# shellcheck source=scripts/lib/check-log.sh
source "$root_dir/scripts/lib/check-log.sh"
# shellcheck source=scripts/lib/guide-manifest.sh
source "$root_dir/scripts/lib/guide-manifest.sh"
# shellcheck source=scripts/lib/queue-state.sh
source "$root_dir/scripts/lib/queue-state.sh"

usage() {
  cat <<EOF
Usage: init-vacuum-state.sh [--quiet]

Creates state/queue.json from prompts/ and runs scorer.

Options:
  --quiet   Suppress verbose trace
  -h, --help  Show help

Examples:
  ./scripts/init-vacuum-state.sh
  ./scripts/vacuum-cli.sh init
EOF
}

quiet=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet) quiet=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Error: unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ "$quiet" -eq 1 ]] && check_log_configure --quiet
check_log_init "init-vacuum-state"
guide_manifest_load "$root_dir"
guide_manifest_trace_defaults "$root_dir"

queue_init
existed=0
[[ -f "$queue_file" ]] && existed=1
queue_save "$(empty_queue_json)"
check_log_file_written "$queue_file" "$root_dir" "$existed"

prompts_dir="$GUIDE_PROMPTS_DIR"
added=0
if [[ -d "$prompts_dir" ]]; then
  check_log_read_dir "$prompts_dir" "$(guide_manifest_rel "$root_dir" "$prompts_dir")" "prompts root"
  while IFS= read -r prompt; do
    queue_add_pending "$prompt" 0 "unscored"
    check_log_file_written "$queue_file" "$root_dir" 1
    check_log_trace "queue add pending $prompt"
    added=$((added + 1))
  done < <(find "$prompts_dir" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | grep -v '^_template$' | sort)
else
  check_log_trace "read  $(guide_manifest_rel "$root_dir" "$prompts_dir")/ (missing — empty queue)"
fi

scorer_args=()
[[ "$quiet" -eq 1 ]] && scorer_args=(--quiet)
"$root_dir/scripts/scorer.sh" "${scorer_args[@]}" >/dev/null || true

check_log_summary "INIT_VACUUM_STATE_OK"
echo "Initialized vacuum state at $(guide_manifest_rel "$root_dir" "$queue_file") (pending=$added)"
printf 'INIT_VACUUM_STATE_OK pending=%s queue=%s\n' "$added" "$(guide_manifest_rel "$root_dir" "$queue_file")" >&2
