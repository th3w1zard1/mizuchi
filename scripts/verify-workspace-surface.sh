#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

required_files=(
  "$ROOT/scripts/decomp-cli.sh"
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

if ! grep -q '/ghidra-scout' "$ROOT/AGENTS.md" || ! grep -q '/decomp-integrate' "$ROOT/AGENTS.md"; then
  echo "invalid: AGENTS.md missing slash-command entries" >&2
  exit 1
fi

echo "WORKSPACE_SURFACE_OK"
