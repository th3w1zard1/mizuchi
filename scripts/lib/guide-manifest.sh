#!/usr/bin/env bash
# Single source of truth for Mizuchi guide coverage paths and tokens.

guide_manifest_root() {
  printf '%s\n' "${MIZUCHI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
}

guide_manifest_load() {
  local root="$1"

  GUIDE_REQUIRED_FILES=(
    "$root/AGENTS.md"
    "$root/mizuchi.example.yaml"
    "$root/.cursor/hooks.json"
    "$root/.cursor/mcp.json"
    "$root/.cursor/commands/ghidra-scout.md"
    "$root/.cursor/commands/decomp-prompt.md"
    "$root/.cursor/commands/decomp-atlas.md"
    "$root/.cursor/commands/decomp-function.md"
    "$root/.cursor/commands/decomp-integrate.md"
    "$root/.cursor/commands/help.md"
    "$root/.cursor/skills/ghidra-re-workflow.md"
    "$root/.cursor/skills/decomp-context-builder.md"
    "$root/.cursor/skills/decomp-programmatic-tools.md"
    "$root/.cursor/skills/decomp-pipeline.md"
    "$root/.cursor/skills/decomp-prompt-builder.md"
    "$root/.cursor/skills/decomp-atlas-index.md"
    "$root/.cursor/skills/decomp-verify-match.md"
    "$root/.cursor/skills/decomp-integrator.md"
    "$root/.cursor/skills/decomp-workflow-checklist.md"
    "$root/.cursor/agents/ghidra-binary-scout.md"
    "$root/.cursor/agents/decomp-prompt-architect.md"
    "$root/.cursor/agents/decomp-function-agent.md"
    "$root/scripts/decomp-cli.sh"
    "$root/scripts/help-command.sh"
    "$root/scripts/compile-and-view-assembly.sh"
    "$root/scripts/run-programmatic-phase.sh"
    "$root/scripts/objdiff-gate.sh"
    "$root/scripts/validate-prompt-settings.sh"
    "$root/docs/knowledgebase/00-intent/matching-decompilation.md"
    "$root/docs/knowledgebase/10-architecture-runtime/pipeline-bridge.md"
    "$root/docs/knowledgebase/20-domain-theory/matching-decompilation-theory.md"
    "$root/docs/knowledgebase/50-execution/playbook.md"
    "$root/docs/knowledgebase/50-execution/cursor-native-bridge.md"
    "$root/docs/knowledgebase/90-meta/evidence-caveats.md"
  )

  GUIDE_KB_LAYERS=(
    "$root/docs/knowledgebase/00-intent"
    "$root/docs/knowledgebase/10-architecture-runtime"
    "$root/docs/knowledgebase/20-domain-theory"
    "$root/docs/knowledgebase/50-execution"
    "$root/docs/knowledgebase/90-meta"
  )

  GUIDE_MCP_SERVERS=(
    agdec-http
    mizuchi
  )

  GUIDE_SLASH_COMMANDS=(
    /ghidra-scout
    /decomp-prompt
    /decomp-atlas
    /decomp-function
    /decomp-integrate
  )

  GUIDE_AGENTS_LINKS=(
    'one-shot-decompilation-with-claude'
    'macabeus.medium.com/can-llms-really-do-matching-decompilation'
  )

  GUIDE_INVARIANTS=(
    'Never claim match without objdiff 0'
    'Programmatic phase before AI; stop on perfect match'
    'No direct source edits during AI matching loop'
    'Ghidra decomp is exploration only'
  )

  GUIDE_CLI_TOKENS=(
    ghidra-scout
    decomp-prompt
    decomp-atlas
    decomp-function
    decomp-integrate
    list-prompts
    inject-context
    run-objdiff
    programmatic-phase
    verify-surface
  )

  GUIDE_HOOK_PATTERN='/hooks/decomp-match-claim-guard.sh'
}
