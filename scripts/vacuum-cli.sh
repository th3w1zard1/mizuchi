#!/usr/bin/env bash
# CLI for vacuum queue inspection and control.
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
Usage: vacuum-cli.sh [--quiet] <command> [args]

Commands:
  init              Initialize queue from prompts/ (init-vacuum-state.sh)
  status            JSON counts for queue buckets
  next              Print highest-priority pending function name
  score             Run scorer.sh
  inspect-queue     Dump queue.json
  reset-queue       Move function back to pending (--function <name>)

Options:
  --quiet           Suppress verbose trace (machine output still on stdout)
  -h, --help        Show help

Examples:
  ./scripts/vacuum-cli.sh init
  ./scripts/vacuum-cli.sh status | jq .
  ./scripts/vacuum-cli.sh next
  ./scripts/vacuum-cli.sh --quiet score
  ./scripts/vacuum-cli.sh reset-queue --function fun_00148020
EOF
}

quiet=0
raw_args=("$@")
filtered=()
for arg in "${raw_args[@]}"; do
  case "$arg" in
    --quiet) quiet=1 ;;
    -h|--help) usage; exit 0 ;;
    *) filtered+=("$arg") ;;
  esac
done

[[ "$quiet" -eq 1 ]] && check_log_configure --quiet
check_log_init "vacuum-cli"
guide_manifest_load "$root_dir"
guide_manifest_trace_defaults "$root_dir"

cmd="${filtered[0]:-}"
rest=("${filtered[@]:1}")

case "$cmd" in
  init)
    init_args=()
    [[ "$quiet" -eq 1 ]] && init_args=(--quiet)
    check_log_run_step "init-vacuum-state"
    "$root_dir/scripts/init-vacuum-state.sh" "${init_args[@]}"
    ;;
  start|resume)
    echo "Error: vacuum loop start/resume not in this slice — use ./scripts/vacuum.sh" >&2
    echo "  ./scripts/vacuum.sh" >&2
    exit 2
    ;;
  status)
    queue_init
    check_log_read_file "$queue_file" "$(guide_manifest_rel "$root_dir" "$queue_file")" "queue state"
    q="$(queue_load)"
    check_log_summary "VACUUM_CLI_OK"
    jq -n \
      --argjson pending "$(jq '.pending|length' <<<"$q")" \
      --argjson matched "$(jq '.matched|length' <<<"$q")" \
      --argjson integrated "$(jq '.integrated|length' <<<"$q")" \
      --argjson failed "$(jq '.failed|length' <<<"$q")" \
      --argjson difficult "$(jq '.difficult|length' <<<"$q")" \
      '{pending:$pending, matched:$matched, integrated:$integrated, failed:$failed, difficult:$difficult}'
    printf 'VACUUM_CLI_OK command=status\n' >&2
    ;;
  next)
    queue_init
    next_fn="$(queue_get_next_pending)"
    check_log_summary "VACUUM_CLI_OK"
    if [[ -z "$next_fn" ]]; then
      echo "No pending functions"
      printf 'VACUUM_CLI_OK command=next empty=1\n' >&2
      exit 0
    fi
    check_log_trace "next  $next_fn"
    echo "$next_fn"
    printf 'VACUUM_CLI_OK command=next name=%s\n' "$next_fn" >&2
    ;;
  score)
    scorer_args=()
    [[ "$quiet" -eq 1 ]] && scorer_args=(--quiet)
    check_log_run_step "scorer"
    "$root_dir/scripts/scorer.sh" "${scorer_args[@]}"
    check_log_summary "VACUUM_CLI_OK"
    printf 'VACUUM_CLI_OK command=score\n' >&2
    ;;
  inspect-queue)
    queue_init
    check_log_read_file "$queue_file" "$(guide_manifest_rel "$root_dir" "$queue_file")" "queue state"
    check_log_summary "VACUUM_CLI_OK"
    queue_load | jq .
    printf 'VACUUM_CLI_OK command=inspect-queue\n' >&2
    ;;
  reset-queue)
    fn=""
    while [[ ${#rest[@]} -gt 0 ]]; do
      case "${rest[0]}" in
        --function) fn="${rest[1]:-}"; rest=("${rest[@]:2}") ;;
        *) echo "Error: unknown argument: ${rest[0]}" >&2; usage; exit 2 ;;
      esac
    done
    if [[ -z "$fn" ]]; then
      echo "Error: --function is required" >&2
      echo "  ./scripts/vacuum-cli.sh reset-queue --function fun_00148020" >&2
      exit 2
    fi
    queue_init
    queue_move "$fn" difficult pending || true
    queue_move "$fn" failed pending || true
    check_log_file_written "$queue_file" "$root_dir" 1
    check_log_summary "VACUUM_CLI_OK"
    echo "Reset $fn to pending (if present)"
    printf 'VACUUM_CLI_OK command=reset-queue function=%s\n' "$fn" >&2
    ;;
  ""|*)
    echo "Error: unknown command: ${cmd:-}" >&2
    usage
    exit 2
    ;;
esac
