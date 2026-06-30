#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

required_files=(
  "$ROOT/scripts/decomp-cli.sh"
  "$ROOT/scripts/build-and-verify.sh"
  "$ROOT/scripts/compile-and-view-assembly.sh"
  "$ROOT/scripts/decomp-readiness.sh"
  "$ROOT/scripts/integrate-verified-match.sh"
  "$ROOT/scripts/init-vacuum-state.sh"
  "$ROOT/scripts/import-one-shot-tasks.py"
  "$ROOT/scripts/one-shot-task-coverage.py"
  "$ROOT/scripts/binary-source-roundtrip.py"
  "$ROOT/scripts/elf-auto-trivial.py"
  "$ROOT/scripts/elf-function-slice.py"
  "$ROOT/scripts/one-shot-source.py"
  "$ROOT/scripts/one-shot-source-archive-verify.py"
  "$ROOT/scripts/one-shot-source-claims.py"
  "$ROOT/scripts/one-shot-source-clean.py"
  "$ROOT/scripts/one-shot-source-deliverable-verify.py"
  "$ROOT/scripts/one-shot-source-proof.py"
  "$ROOT/scripts/one-shot-source-validate.py"
  "$ROOT/scripts/one-shot-source-verify.py"
  "$ROOT/scripts/pe-auto-trivial.py"
  "$ROOT/scripts/source-authority-report.py"
  "$ROOT/scripts/steam-roundtrip-inventory.py"
  "$ROOT/scripts/steam-roundtrip-progress.py"
  "$ROOT/scripts/steam-roundtrip-run.py"
  "$ROOT/scripts/steam-roundtrip-verify-manifest.py"
  "$ROOT/scripts/validate-case-manifests.sh"
  "$ROOT/tests/decomp_function_cli_test.sh"
  "$ROOT/tests/init_vacuum_test.sh"
  "$ROOT/tests/import_one_shot_tasks_test.sh"
  "$ROOT/tests/one_shot_task_coverage_test.sh"
  "$ROOT/docs/vacuum-cli.md"
  "$ROOT/.cursor/hooks.json"
  "$ROOT/.cursor/mcp.json"
  "$ROOT/.cursor/rules/matching-decompilation-core.mdc"
  "$ROOT/.cursor/agents/decomp-prompt-architect.md"
  "$ROOT/.cursor/agents/decomp-function-agent.md"
  "$ROOT/.cursor/commands/decomp-prompt.md"
  "$ROOT/.cursor/commands/decomp-atlas.md"
  "$ROOT/.cursor/commands/decomp-function.md"
  "$ROOT/.cursor/commands/decomp-integrate.md"
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

required_match_claim_hook='/hooks/decomp-match-claim-guard.sh'
if ! grep -q "$required_match_claim_hook" "$ROOT/.cursor/hooks.json"; then
  echo "invalid: .cursor/hooks.json missing match-claim guard hook" >&2
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

if ! grep -q '/decomp-function' "$ROOT/AGENTS.md" || ! grep -q '/decomp-integrate' "$ROOT/AGENTS.md"; then
  echo "invalid: AGENTS.md missing slash-command entries" >&2
  exit 1
fi

if ! grep -q 'decomp-validate <prompt-name|--all>' "$ROOT/scripts/decomp-cli.sh"; then
  echo "invalid: decomp-cli missing prompt validation command" >&2
  exit 1
fi

if ! grep -q 'decomp-readiness <prompt-name|--all>' "$ROOT/scripts/decomp-cli.sh"; then
  echo "invalid: decomp-cli missing prompt readiness command" >&2
  exit 1
fi

if ! grep -q 'decomp-function.json' "$ROOT/.cursor/commands/decomp-function.md"; then
  echo "invalid: /decomp-function command missing orchestration receipt contract" >&2
  exit 1
fi

if ! grep -q 'mizuchi.decomp-function.v1' "$ROOT/docs/knowledgebase/50-execution/playbook.md"; then
  echo "invalid: playbook missing decomp-function receipt schema" >&2
  exit 1
fi

"$ROOT/scripts/validate-case-manifests.sh" "$ROOT/prompts" >/dev/null

echo "WORKSPACE_SURFACE_OK"
