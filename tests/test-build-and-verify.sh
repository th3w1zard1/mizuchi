#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

mkdir -p "$work_dir/scripts/lib" "$work_dir/prompts/fun_001/build"
cp "$root_dir/scripts/build-and-verify.sh" "$work_dir/scripts/"
cp "$root_dir/scripts/run-objdiff.sh" "$work_dir/scripts/"
cp "$root_dir/scripts/lib/build-defensive.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/verify-objdiff.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/check-log.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/guide-manifest.sh" "$work_dir/scripts/lib/"
cp "$root_dir/scripts/lib/cli-agent.sh" "$work_dir/scripts/lib/"

cat >"$work_dir/prompts/fun_001/trial.c" <<'EOF'
int test(void) { return 0; }
EOF

printf 'dummy\n' >"$work_dir/prompts/fun_001/target.o"

export MIZUCHI_ROOT="$work_dir"

mkdir -p "$work_dir/bin"
cat >"$work_dir/bin/objdiff" <<'EOF'
#!/usr/bin/env bash
echo "objdiff unavailable in test harness" >&2
exit 127
EOF
chmod +x "$work_dir/bin/objdiff"

set +e
out="$(PATH="$work_dir/bin:$PATH" "$work_dir/scripts/build-and-verify.sh" --prompt fun_001 --target "$work_dir/prompts/fun_001/target.o" --quiet 2>/dev/null)"
rc=$?
set -e

[[ "$rc" -eq 2 ]]
[[ "$(jq -r '.status // empty' <<<"$out")" == "infra_error" ]]

echo "test-build-and-verify: PASS"
