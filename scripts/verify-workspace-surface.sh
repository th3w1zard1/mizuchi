#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_DIR="$ROOT/scripts"

# shellcheck source=scripts/lib/check-log.sh
source "$SCRIPT_DIR/lib/check-log.sh"
# shellcheck source=scripts/lib/guide-manifest.sh
source "$SCRIPT_DIR/lib/guide-manifest.sh"

quiet=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet) quiet=1; shift ;;
    -h|--help)
      cat <<EOF
usage: verify-workspace-surface.sh [--quiet]

Verifies workspace files, hooks, MCP servers, and nested guide validators.
Verbose logging is the default; use --quiet for machine-only output.
EOF
      exit 0
      ;;
    *) echo "unexpected argument: $1" >&2; exit 2 ;;
  esac
done

CHECK_LOG_QUIET=$quiet
check_log_init "verify-workspace-surface"

guide_manifest_load "$ROOT"
guide_manifest_trace_defaults "$ROOT"

required_files=(
  "$ROOT/scripts/decomp-cli.sh"
  "$ROOT/scripts/lfg-smoke.sh"
  "$ROOT/scripts/verify-workspace-surface.sh"
  "$ROOT/scripts/validate-prompt-status.sh"
  "$ROOT/scripts/validate-case-manifests.sh"
  "$ROOT/scripts/validate-guide-coverage.sh"
  "$ROOT/scripts/validate-capability-parity.sh"
  "$ROOT/scripts/audit-plugin-readiness.sh"
  "$ROOT/scripts/run-test-suite.sh"
  "$ROOT/scripts/validate-prompt-settings.sh"
  "$ROOT/scripts/objdiff-gate.sh"
  "$ROOT/scripts/run-objdiff.sh"
  "$ROOT/scripts/inject-context.sh"
  "$ROOT/scripts/list-prompts.sh"
  "$ROOT/scripts/scorer.sh"
  "$ROOT/scripts/init-vacuum-state.sh"
  "$ROOT/scripts/vacuum-cli.sh"
  "$ROOT/scripts/lib/queue-schema.sh"
  "$ROOT/scripts/lib/queue-state.sh"
  "$ROOT/scripts/lib/scorer-heuristic.sh"
  "$ROOT/scripts/lib/check-log.sh"
  "$ROOT/scripts/lib/case-manifest.sh"
  "$ROOT/scripts/lib/guide-manifest.sh"
  "$ROOT/.cursor/hooks.json"
  "$ROOT/.cursor/mcp.json"
  "$ROOT/.cursor/rules/matching-decompilation-core.mdc"
  "$ROOT/.cursor/rules/ghidra-agentdecompile.mdc"
  "$ROOT/.cursor/agents/ghidra-binary-scout.md"
  "$ROOT/.cursor/agents/decomp-prompt-architect.md"
  "$ROOT/.cursor/agents/decomp-function-agent.md"
  "$ROOT/.cursor/commands/ghidra-scout.md"
  "$ROOT/.cursor/commands/decomp-prompt.md"
  "$ROOT/.cursor/commands/decomp-atlas.md"
  "$ROOT/.cursor/commands/decomp-function.md"
  "$ROOT/.cursor/commands/decomp-integrate.md"
  "$ROOT/.cursor/skills/ghidra-re-workflow.md"
  "$ROOT/.cursor/skills/decomp-context-builder.md"
  "$ROOT/.cursor/skills/decomp-programmatic-tools.md"
  "$ROOT/.cursor/skills/decomp-pipeline.md"
  "$ROOT/.cursor/skills/decomp-prompt-builder.md"
  "$ROOT/.cursor/skills/decomp-atlas-index.md"
  "$ROOT/.cursor/skills/decomp-verify-match.md"
  "$ROOT/.cursor/skills/decomp-integrator.md"
  "$ROOT/.cursor/skills/decomp-workflow-checklist.md"
  "$ROOT/docs/knowledgebase/10-architecture-runtime/reference-pipeline.md"
  "$ROOT/docs/knowledgebase/10-architecture-runtime/workspace-contract.md"
)

failures=0
record_fail() { failures=1; }

for file in "${required_files[@]}"; do
  rel="${file#$ROOT/}"
  check_log_read_file "$file" "$rel" "workspace surface" || record_fail
done

check_log_grep_file "$ROOT/.cursor/hooks.json" "$GUIDE_HOOK_PATTERN" "match-claim guard hook" || record_fail

mcp_file="$ROOT/.cursor/mcp.json"
for server in "${GUIDE_MCP_SERVERS[@]}"; do
  check_log_mcp_server "$mcp_file" "$server" || record_fail
done

agents_file="$ROOT/AGENTS.md"
for link in "${GUIDE_AGENTS_LINKS[@]}"; do
  check_log_grep_file "$agents_file" "$link" "AGENTS research link" || record_fail
done

for cmd in "${GUIDE_SLASH_COMMANDS[@]}"; do
  check_log_grep_file "$agents_file" "$cmd" "AGENTS slash command" || record_fail
done

sub_quiet=()
[[ "$quiet" -eq 1 ]] && sub_quiet=(--quiet)

check_log_trace "run   scripts/validate-prompt-status.sh ${sub_quiet[*]:-}"
prompt_status="$("$ROOT/scripts/validate-prompt-status.sh" "${sub_quiet[@]}")"
if [[ "$prompt_status" == "PROMPT_STATUS_OK" ]]; then
  check_log_pass "validate-prompt-status.sh"
else
  check_log_fail "validate-prompt-status.sh returned: $prompt_status"
  record_fail
fi

check_log_trace "run   scripts/validate-case-manifests.sh ${sub_quiet[*]:-}"
case_manifest_status="$("$ROOT/scripts/validate-case-manifests.sh" "${sub_quiet[@]}")"
if [[ "$case_manifest_status" == "CASE_MANIFESTS_OK" ]]; then
  check_log_pass "validate-case-manifests.sh"
else
  check_log_fail "validate-case-manifests.sh returned: $case_manifest_status"
  record_fail
fi

check_log_trace "run   scripts/validate-guide-coverage.sh ${sub_quiet[*]:-}"
guide_status="$("$ROOT/scripts/validate-guide-coverage.sh" "${sub_quiet[@]}")"
if [[ "$guide_status" == "GUIDE_COVERAGE_OK" ]]; then
  check_log_pass "validate-guide-coverage.sh"
else
  check_log_fail "validate-guide-coverage.sh returned: $guide_status"
  record_fail
fi

check_log_trace "run   scripts/validate-capability-parity.sh ${sub_quiet[*]:-}"
capability_status="$("$ROOT/scripts/validate-capability-parity.sh" "${sub_quiet[@]}")"
if [[ "$capability_status" == "CAPABILITY_PARITY_OK" ]]; then
  check_log_pass "validate-capability-parity.sh"
else
  check_log_fail "validate-capability-parity.sh returned: $capability_status"
  record_fail
fi

if [[ "$failures" -ne 0 ]]; then
  check_log_summary "WORKSPACE_SURFACE_FAIL"
  exit 1
fi

check_log_summary "WORKSPACE_SURFACE_OK"
echo "WORKSPACE_SURFACE_OK"
