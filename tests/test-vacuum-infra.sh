#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

mkdir -p "$work_dir/scripts/lib" "$work_dir/prompts/fun_001" "$work_dir/state"
cp "$root_dir/scripts/vacuum.sh" "$work_dir/scripts/"
cp "$root_dir/scripts/matcher.sh" "$work_dir/scripts/"
cp "$root_dir/scripts/build-and-verify.sh" "$work_dir/scripts/"
cp "$root_dir/scripts/run-objdiff.sh" "$work_dir/scripts/"
cp "$root_dir/scripts/init-vacuum-state.sh" "$work_dir/scripts/"
cp "$root_dir/scripts/scorer.sh" "$work_dir/scripts/"
cp "$root_dir/scripts/lib/queue-schema.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/queue-state.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/vacuum-backoff.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/vacuum-state.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/matcher-prompt.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/matcher-parse.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/build-defensive.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/verify-objdiff.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/scorer-heuristic.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/scorer-ml-hooks.sh" "$work_dir/scripts/lib/"

cat >"$work_dir/prompts/fun_001/prompt.md" <<'EOF'
```asm
ret
```
EOF

export MIZUCHI_ROOT="$work_dir"
export MIZUCHI_STATE_DIR="$work_dir/state"
export MIZUCHI_MAX_INFRA_RETRIES=1

"$work_dir/scripts/init-vacuum-state.sh" >/dev/null
"$work_dir/scripts/vacuum.sh" >/dev/null

queue_json="$(cat "$work_dir/state/queue.json")"
[[ "$(jq '.failed | length' <<<"$queue_json")" -eq 1 ]]
[[ "$(jq '.pending | length' <<<"$queue_json")" -eq 0 ]]

echo "test-vacuum-infra: PASS"
