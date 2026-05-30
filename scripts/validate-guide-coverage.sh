#!/usr/bin/env bash
set -euo pipefail

ROOT="${MIZUCHI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

required_files=(
  "$ROOT/AGENTS.md"
  "$ROOT/mizuchi.example.yaml"
  "$ROOT/.cursor/hooks.json"
  "$ROOT/.cursor/mcp.json"
  "$ROOT/.cursor/commands/ghidra-scout.md"
  "$ROOT/.cursor/commands/decomp-prompt.md"
  "$ROOT/.cursor/commands/decomp-atlas.md"
  "$ROOT/.cursor/commands/decomp-function.md"
  "$ROOT/.cursor/commands/decomp-integrate.md"
  "$ROOT/.cursor/commands/help.md"
  "$ROOT/.cursor/skills/ghidra-re-workflow.md"
  "$ROOT/.cursor/skills/decomp-context-builder.md"
  "$ROOT/.cursor/skills/decomp-programmatic-tools.md"
  "$ROOT/.cursor/skills/decomp-pipeline.md"
  "$ROOT/.cursor/skills/decomp-prompt-builder.md"
  "$ROOT/.cursor/skills/decomp-atlas-index.md"
  "$ROOT/.cursor/skills/decomp-verify-match.md"
  "$ROOT/.cursor/skills/decomp-integrator.md"
  "$ROOT/.cursor/skills/decomp-workflow-checklist.md"
  "$ROOT/.cursor/agents/ghidra-binary-scout.md"
  "$ROOT/.cursor/agents/decomp-prompt-architect.md"
  "$ROOT/.cursor/agents/decomp-function-agent.md"
  "$ROOT/scripts/decomp-cli.sh"
  "$ROOT/scripts/help-command.sh"
  "$ROOT/scripts/compile-and-view-assembly.sh"
  "$ROOT/scripts/run-programmatic-phase.sh"
  "$ROOT/scripts/objdiff-gate.sh"
  "$ROOT/scripts/validate-prompt-settings.sh"
  "$ROOT/docs/knowledgebase/00-intent/matching-decompilation.md"
  "$ROOT/docs/knowledgebase/10-architecture-runtime/pipeline-bridge.md"
  "$ROOT/docs/knowledgebase/20-domain-theory/matching-decompilation-theory.md"
  "$ROOT/docs/knowledgebase/50-execution/playbook.md"
  "$ROOT/docs/knowledgebase/50-execution/cursor-native-bridge.md"
  "$ROOT/docs/knowledgebase/90-meta/evidence-caveats.md"
)

missing=0
for file in "${required_files[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "missing guide artifact: ${file#$ROOT/}" >&2
    missing=1
  fi
done

if [[ "$missing" -ne 0 ]]; then
  exit 1
fi

required_directories=(
  "$ROOT/docs/knowledgebase/00-intent"
  "$ROOT/docs/knowledgebase/10-architecture-runtime"
  "$ROOT/docs/knowledgebase/20-domain-theory"
  "$ROOT/docs/knowledgebase/50-execution"
  "$ROOT/docs/knowledgebase/90-meta"
)

for dir in "${required_directories[@]}"; do
  if [[ ! -d "$dir" ]]; then
    echo "missing knowledgebase layer: ${dir#$ROOT/}" >&2
    exit 1
  fi
done

if ! grep -q '/hooks/decomp-match-claim-guard.sh' "$ROOT/.cursor/hooks.json"; then
  echo "invalid hooks: missing decomp match-claim guard" >&2
  exit 1
fi

for server in '"agdec-http"' '"mizuchi"'; do
  if ! grep -q "$server" "$ROOT/.cursor/mcp.json"; then
    echo "invalid mcp config: missing server $server" >&2
    exit 1
  fi
done

for cmd in "/ghidra-scout" "/decomp-prompt" "/decomp-atlas" "/decomp-function" "/decomp-integrate"; do
  if ! grep -q "$cmd" "$ROOT/AGENTS.md"; then
    echo "invalid AGENTS guide: missing command $cmd" >&2
    exit 1
  fi
done

if ! grep -q 'one-shot-decompilation-with-claude' "$ROOT/AGENTS.md"; then
  echo "invalid AGENTS guide: missing Chris Lewis source link" >&2
  exit 1
fi

if ! grep -q 'macabeus.medium.com/can-llms-really-do-matching-decompilation' "$ROOT/AGENTS.md"; then
  echo "invalid AGENTS guide: missing Macabeus source link" >&2
  exit 1
fi

for invariant in \
  'Never claim match without objdiff 0' \
  'Programmatic phase before AI; stop on perfect match' \
  'No direct source edits during AI matching loop' \
  'Ghidra decomp is exploration only'
do
  if ! grep -q "$invariant" "$ROOT/AGENTS.md"; then
    echo "invalid AGENTS invariants: missing \"$invariant\"" >&2
    exit 1
  fi
done

for cli_token in \
  'ghidra-scout' \
  'decomp-prompt' \
  'decomp-atlas' \
  'decomp-function' \
  'decomp-integrate' \
  'list-prompts' \
  'inject-context' \
  'run-objdiff' \
  'programmatic-phase' \
  'verify-surface'
do
  if ! grep -q "^[[:space:]]\\+$cli_token\\([[:space:]]\\|$\\)" "$ROOT/scripts/decomp-cli.sh"; then
    echo "invalid decomp-cli usage: missing $cli_token" >&2
    exit 1
  fi
done

echo "GUIDE_COVERAGE_OK"
