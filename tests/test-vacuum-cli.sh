#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

mkdir -p "$work_dir/scripts/lib" "$work_dir/prompts/fun_001" "$work_dir/state"
cp "$root_dir/scripts/vacuum-cli.sh" "$work_dir/scripts/"
cp "$root_dir/scripts/init-vacuum-state.sh" "$work_dir/scripts/"
cp "$root_dir/scripts/init-vacuum-state.sh" "$work_dir/scripts/"
cp "$root_dir/scripts/scorer.sh" "$work_dir/scripts/"
cp "$root_dir/scripts/lib/queue-schema.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/queue-state.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/scorer-heuristic.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/scorer-ml-hooks.sh" "$work_dir/scripts/lib/"

cat >"$work_dir/prompts/fun_001/prompt.md" <<'EOF'
```asm
ret
```
EOF

export MIZUCHI_ROOT="$work_dir"
export MIZUCHI_STATE_DIR="$work_dir/state"

"$work_dir/scripts/init-vacuum-state.sh" >/dev/null

"$work_dir/scripts/vacuum-cli.sh" status | jq . >/dev/null
"$work_dir/scripts/vacuum-cli.sh" inspect-queue | jq . >/dev/null
"$work_dir/scripts/vacuum-cli.sh" next | grep -q 'fun_001'
"$work_dir/scripts/vacuum-cli.sh" score | grep -q 'Scored'

status_json="$("$work_dir/scripts/vacuum-cli.sh" status)"
[[ "$(jq -r '.pending' <<<"$status_json")" -ge 0 ]]

echo "test-vacuum-cli: PASS"
