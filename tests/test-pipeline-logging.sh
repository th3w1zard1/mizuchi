#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# objdiff-gate missing args: actionable error + summary
err_out="$("$ROOT/scripts/objdiff-gate.sh" 2>&1)" || rc=$?
rc="${rc:-0}"
[[ "$rc" -ne 0 ]]
grep -q "Error:" <<<"$err_out"
grep -q "objdiff-gate.sh target.o" <<<"$err_out"
grep -q "summary (OBJDIFF_GATE_FAIL)" <<<"$err_out"

# programmatic-phase help documents --quiet
help_out="$("$ROOT/scripts/run-programmatic-phase.sh" --help 2>&1)"
grep -q -- "--quiet" <<<"$help_out"
grep -q "Examples:" <<<"$help_out"

# get-context missing prompt
ctx_err="$("$ROOT/scripts/get-context.sh" 2>&1)" || ctx_rc=$?
ctx_rc="${ctx_rc:-0}"
[[ "$ctx_rc" -ne 0 ]]
grep -q "GET_CONTEXT_FAIL\|Error:" <<<"$ctx_err"

echo "test-pipeline-logging: PASS"
