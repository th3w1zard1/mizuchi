#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# objdiff-gate missing args: actionable error + summary
err_out="$("$ROOT/scripts/objdiff-gate.sh" 2>&1)" || rc=$?
rc="${rc:-0}"
[[ "$rc" -ne 0 ]]
grep -q "Error:\|fail  missing" <<<"$err_out"
grep -q "objdiff-gate.sh target.o" <<<"$err_out"
grep -q "summary (OBJDIFF_GATE_FAIL)" <<<"$err_out"
grep -q "mcp   server=agdec-http" <<<"$err_out"

# programmatic-phase help documents --quiet
help_out="$("$ROOT/scripts/run-programmatic-phase.sh" --help 2>&1)"
grep -q -- "--quiet" <<<"$help_out"
grep -q "Examples:" <<<"$help_out"

# get-context missing prompt
ctx_err="$("$ROOT/scripts/get-context.sh" 2>&1)" || ctx_rc=$?
ctx_rc="${ctx_rc:-0}"
[[ "$ctx_rc" -ne 0 ]]
grep -q "GET_CONTEXT_FAIL\|Error:" <<<"$ctx_err"

# run-m2c help + missing prompt
m2c_help="$("$ROOT/scripts/run-m2c.sh" --help 2>&1)"
grep -q "Examples:" <<<"$m2c_help"
grep -q "build/m2c.c" <<<"$m2c_help"
m2c_err="$("$ROOT/scripts/run-m2c.sh" 2>&1)" || m2c_rc=$?
m2c_rc="${m2c_rc:-0}"
[[ "$m2c_rc" -ne 0 ]]
grep -q "Error:\|missing --prompt" <<<"$m2c_err"

# compile-trial help
ct_help="$("$ROOT/scripts/compile-trial.sh" --help 2>&1)"
grep -q "Examples:" <<<"$ct_help"
grep -q "candidate.o" <<<"$ct_help"

# validate-prompt-settings help
vps_help="$("$ROOT/scripts/validate-prompt-settings.sh" --help 2>&1)"
grep -q "Examples:" <<<"$vps_help"

# list-prompts traces MCP servers on stderr
lp_err="$("$ROOT/scripts/list-prompts.sh" --quiet 2>&1 >/dev/null)" || true
grep -q "summary (LIST_PROMPTS_OK)" <<<"$lp_err"

echo "test-pipeline-logging: PASS"
