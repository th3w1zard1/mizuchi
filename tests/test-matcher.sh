#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

mkdir -p "$work_dir/scripts/lib" "$work_dir/prompts/fun_001/build"
cp "$root_dir/scripts/matcher.sh" "$work_dir/scripts/"
cp "$root_dir/scripts/lib/matcher-prompt.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/matcher-parse.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/check-log.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/guide-manifest.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/cli-agent.sh" "$work_dir/scripts/lib/"

cat >"$work_dir/prompts/fun_001/prompt.md" <<'EOF'
```asm
ret
```
EOF

cat >"$work_dir/live-response.txt" <<'EOF'
```c
int test(void){return 0;}
```
EOF

export MIZUCHI_ROOT="$work_dir"
export MATCHER_COMMAND="cat \"$work_dir/live-response.txt\""

"$work_dir/scripts/matcher.sh" --prompt fun_001 >/dev/null

grep -q "int test" "$work_dir/prompts/fun_001/trial.c"

echo "test-matcher: PASS"
