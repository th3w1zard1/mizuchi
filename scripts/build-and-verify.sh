#!/usr/bin/env bash
set -euo pipefail

root_dir="${MIZUCHI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# shellcheck source=scripts/lib/build-defensive.sh
source "$root_dir/scripts/lib/build-defensive.sh"
# shellcheck source=scripts/lib/verify-objdiff.sh
source "$root_dir/scripts/lib/verify-objdiff.sh"

usage() {
  cat <<EOF
Usage: $0 --prompt <prompt-name> --target <target.o> [--commit]
EOF
}

prompt_name=""
target_obj=""
commit_on_match=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt) prompt_name="${2:-}"; shift 2 ;;
    --target) target_obj="${2:-}"; shift 2 ;;
    --commit) commit_on_match=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$prompt_name" || -z "$target_obj" ]]; then
  usage
  exit 2
fi

prompt_dir="$root_dir/prompts/$prompt_name"
trial_c="$prompt_dir/trial.c"
build_dir="$prompt_dir/build"
candidate_o="$build_dir/candidate.o"
mkdir -p "$build_dir"

if [[ ! -f "$trial_c" ]]; then
  echo "{\"status\":\"compile_error\",\"message\":\"trial.c missing\"}"
  exit 1
fi

if ! build_compile_defensive "$trial_c" "$candidate_o" >/dev/null; then
  echo "{\"status\":\"compile_error\",\"message\":\"BUILD FAILED. Treat this as a failed attempt and retry with simpler code.\"}"
  exit 1
fi

set +e
verify_json="$(verify_with_objdiff "$target_obj" "$candidate_o" 2>&1)"
verify_rc=$?
set -e
if [[ "$verify_rc" -ne 0 ]]; then
  jq -n \
    --arg status "infra_error" \
    --arg message "Verification tooling failed" \
    --arg raw "$verify_json" \
    '{status:$status,message:$message,raw:$raw}'
  exit 2
fi

status="$(jq -r '.status // "unknown"' <<<"$verify_json")"
diffs="$(jq -r '.differences // -1' <<<"$verify_json")"
if [[ "$status" == "error" ]]; then
  jq -n \
    --arg status "infra_error" \
    --arg message "$(jq -r '.message // "Verification tooling failed"' <<<"$verify_json")" \
    --argjson upstream "$verify_json" \
    '{status:$status,message:$message,upstream:$upstream}'
  exit 2
fi

if [[ "$status" == "matched" && "$diffs" == "0" && "$commit_on_match" -eq 1 ]]; then
  git add "$trial_c" "$candidate_o" 2>/dev/null || true
  if ! git diff --cached --quiet; then
    git commit -m "match($prompt_name): verified objdiff 0"
  fi
fi

echo "$verify_json"
