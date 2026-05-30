#!/usr/bin/env bash
set -euo pipefail

root_dir="${MIZUCHI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# shellcheck source=scripts/lib/queue-state.sh
source "$root_dir/scripts/lib/queue-state.sh"

usage() {
  cat <<EOF
Usage: $0 <status|next|score|inspect-queue|reset-queue|init> [--function <name>]
EOF
}

cmd="${1:-}"
shift || true

case "$cmd" in
  init)
    "$root_dir/scripts/init-vacuum-state.sh"
    ;;
  start|resume)
    echo "Cycle 3 U5 (vacuum loop) is not in this slice — use init, status, next, score" >&2
    exit 2
    ;;
  status)
    queue_init
    q="$(queue_load)"
    jq -n \
      --argjson pending "$(jq '.pending|length' <<<"$q")" \
      --argjson matched "$(jq '.matched|length' <<<"$q")" \
      --argjson integrated "$(jq '.integrated|length' <<<"$q")" \
      --argjson failed "$(jq '.failed|length' <<<"$q")" \
      --argjson difficult "$(jq '.difficult|length' <<<"$q")" \
      '{pending:$pending, matched:$matched, integrated:$integrated, failed:$failed, difficult:$difficult}'
    ;;
  next)
    queue_init
    next_fn="$(queue_get_next_pending)"
    if [[ -z "$next_fn" ]]; then
      echo "No pending functions"
      exit 0
    fi
    echo "$next_fn"
    ;;
  score)
    "$root_dir/scripts/scorer.sh"
    ;;
  inspect-queue)
    queue_init
    queue_load | jq .
    ;;
  reset-queue)
    fn=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --function) fn="${2:-}"; shift 2 ;;
        *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
      esac
    done
    if [[ -z "$fn" ]]; then
      echo "--function is required" >&2
      exit 2
    fi
    queue_move "$fn" difficult pending || true
    queue_move "$fn" failed pending || true
    echo "Reset $fn to pending (if present)"
    ;;
  *)
    usage
    exit 2
    ;;
esac
