#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

mkdir -p "$work_dir/scripts" "$work_dir/prompts/honest"
cp "$ROOT/scripts/validate-prompt-status.sh" "$work_dir/scripts/"
chmod +x "$work_dir/scripts/validate-prompt-status.sh"

cat >"$work_dir/prompts/honest/notes.md" <<'EOF'
**status: blocked**
EOF

out="$( (cd "$work_dir" && ./scripts/validate-prompt-status.sh --quiet) )"
[[ "$out" == "PROMPT_STATUS_OK" ]]

mkdir -p "$work_dir/prompts/bad"
cat >"$work_dir/prompts/bad/notes.md" <<'EOF'
**status: matched**
EOF
cat >"$work_dir/prompts/bad/settings.yaml" <<'EOF'
targetObjectPath: build/missing.o
EOF

set +e
(cd "$work_dir" && ./scripts/validate-prompt-status.sh --quiet >/dev/null 2>&1)
bad_status=$?
set -e
[[ "$bad_status" -ne 0 ]]

rm -rf "$work_dir/prompts/bad"
out2="$( (cd "$work_dir" && ./scripts/validate-prompt-status.sh --quiet) )"
[[ "$out2" == "PROMPT_STATUS_OK" ]]

echo "validate_prompt_status_test: ok"
