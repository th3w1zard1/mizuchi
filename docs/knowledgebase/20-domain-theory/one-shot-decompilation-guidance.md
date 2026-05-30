# One-Shot Decompilation Guidance

## External observations (dated 2026-05-29)

- [OFFICIAL] Chris Lewis, *The Unexpected Effectiveness of One-Shot Decompilation with Claude*:
  - https://blog.chrislewis.au/the-unexpected-effectiveness-of-one-shot-decompilation-with-claude/
- [OFFICIAL] Macabeus, *Can LLMs Really Do Matching Decompilation?*:
  - https://macabeus.medium.com/can-llms-really-do-matching-decompilation-i-tested-60-functions-to-find-out-4e39b0ae4288

## Synthesized takeaways

- [SYNTH] One-shot attempts can work on simple functions, but reliability still depends on immediate compile + diff verification.
- [SYNTH] Prompt quality and context completeness (types, calling convention, exact asm) matter more than raw verbosity.
- [SYNTH] Programmatic baselines (m2c/permuter) should run first; one-shot is a fallback accelerator, not a replacement for verification.

## Workspace implications

- [REPO] Keep strict `objdiff 0` gate (`scripts/objdiff-gate.sh`) for all match claims.
- [REPO] Favor a "fast first shot" only after prompt settings validate (`scripts/validate-prompt-settings.sh`).
- [REPO] Preserve sandbox rule: no direct target-source edits during matching (`.cursor/rules/matching-decompilation-core.mdc`).
