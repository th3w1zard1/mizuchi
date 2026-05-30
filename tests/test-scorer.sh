#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

mkdir -p "$work_dir/scripts/lib" "$work_dir/prompts/fun_easy" "$work_dir/prompts/fun_hard" "$work_dir/state"
cp "$root_dir/scripts/scorer.sh" "$work_dir/scripts/"
cp "$root_dir/scripts/lib/queue-schema.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/queue-state.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/scorer-heuristic.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/scorer-ml-hooks.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/check-log.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/guide-manifest.sh" "$work_dir/scripts/lib/"

cat >"$work_dir/prompts/fun_easy/prompt.md" <<'EOF'
```asm
mov eax, ebx
ret
```
EOF

cat >"$work_dir/prompts/fun_hard/prompt.md" <<'EOF'
```asm
L1:
cmp eax, 0
je L2
sub eax, 1
jmp L1
L2:
ret
```
EOF

export MIZUCHI_ROOT="$work_dir"
export MIZUCHI_STATE_DIR="$work_dir/state"

# shellcheck source=/dev/null
source "$work_dir/scripts/lib/queue-state.sh"
queue_init
queue_add_pending "fun_easy" 0 "seed"
queue_add_pending "fun_hard" 0 "seed"

"$work_dir/scripts/scorer.sh" >/dev/null

test -f "$work_dir/state/scores.json"
top="$(jq -r '.scores[0].name' "$work_dir/state/scores.json")"
[[ "$top" == "fun_easy" ]]

echo "test-scorer: PASS"
