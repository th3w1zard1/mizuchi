#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

required_files=(
  "$ROOT/scripts/decomp-cli.sh"
  "$ROOT/scripts/lfg-smoke.sh"
  "$ROOT/scripts/verify-workspace-surface.sh"
  "$ROOT/scripts/validate-prompt-status.sh"
  "$ROOT/scripts/validate-guide-coverage.sh"
  "$ROOT/scripts/validate-capability-parity.sh"
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
)

missing=0
for file in "${required_files[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "missing: ${file#$ROOT/}" >&2
    missing=1
  fi
done

if [[ "$missing" -ne 0 ]]; then
  exit 1
fi

required_agdec_hook='/hooks/decomp-match-claim-guard.sh'
if ! grep -q "$required_agdec_hook" "$ROOT/.cursor/hooks.json"; then
  echo "invalid: .cursor/hooks.json missing match-claim guard hook" >&2
  exit 1
fi

if ! grep -q '"agdec-http"' "$ROOT/.cursor/mcp.json"; then
  echo "invalid: .cursor/mcp.json missing agdec-http MCP server" >&2
  exit 1
fi

if ! grep -q '"mizuchi"' "$ROOT/.cursor/mcp.json"; then
  echo "invalid: .cursor/mcp.json missing mizuchi MCP server" >&2
  exit 1
fi

if ! grep -q 'one-shot-decompilation-with-claude' "$ROOT/AGENTS.md"; then
  echo "invalid: AGENTS.md missing Chris Lewis guide reference" >&2
  exit 1
fi

if ! grep -q 'macabeus.medium.com/can-llms-really-do-matching-decompilation' "$ROOT/AGENTS.md"; then
  echo "invalid: AGENTS.md missing Macabeus guide reference" >&2
  exit 1
fi

if ! grep -q '/ghidra-scout' "$ROOT/AGENTS.md"; then
  echo "invalid: AGENTS.md missing slash-command entries" >&2
  exit 1
fi

if ! grep -q '/decomp-integrate' "$ROOT/AGENTS.md"; then
  echo "invalid: AGENTS.md missing slash-command entries" >&2
  exit 1
fi

prompt_status="$("$ROOT/scripts/validate-prompt-status.sh" --quiet)"
if [[ "$prompt_status" != "PROMPT_STATUS_OK" ]]; then
  echo "invalid: validate-prompt-status.sh failed" >&2
  exit 1
fi

guide_status="$("$ROOT/scripts/validate-guide-coverage.sh")"
if [[ "$guide_status" != "GUIDE_COVERAGE_OK" ]]; then
  echo "invalid: validate-guide-coverage.sh failed" >&2
  exit 1
fi

capability_status="$("$ROOT/scripts/validate-capability-parity.sh")"
if [[ "$capability_status" != "CAPABILITY_PARITY_OK" ]]; then
  echo "invalid: validate-capability-parity.sh failed" >&2
  exit 1
fi

echo "WORKSPACE_SURFACE_OK"
