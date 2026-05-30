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

help_run="$("$CLI" help run-objdiff 2>&1)"
grep -q "Examples:" <<<"$help_run"
grep -q "objdiff-gate.sh\|run-objdiff" <<<"$help_run"

err_out="$("$CLI" run-objdiff 2>&1)" || true
grep -q "Error:" <<<"$err_out"
grep -q "decomp-cli.sh run-objdiff" <<<"$err_out"

echo "test-decomp-cli: PASS"
