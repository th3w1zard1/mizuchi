#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI="$ROOT/scripts/decomp-cli.sh"

help_json="$("$CLI" help)"
[[ "$(jq -r '.title' <<<"$help_json")" == "Mizuchi Workspace Help" ]]

prompts_json="$("$CLI" list-prompts)"
jq -e '.prompts' <<<"$prompts_json" >/dev/null

ctx_out="$("$CLI" inject-context ghidra-binary-scout)"
grep -q "## Workspace Context" <<<"$ctx_out"

set +e
"$CLI" run-objdiff >/dev/null 2>&1
run_objdiff_rc=$?
"$CLI" programmatic-phase >/dev/null 2>&1
programmatic_rc=$?
set -e

[[ "$run_objdiff_rc" -ne 0 ]]
[[ "$programmatic_rc" -ne 0 ]]

echo "test-decomp-cli: PASS"
