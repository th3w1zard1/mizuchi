# Architecture & Runtime

## Dual-surface design

```
┌─────────────────────┐         ┌──────────────────────────┐
│ Ghidra + AgentDecomp│         │ Mizuchi / Cursor pipeline │
│ (exploration)       │         │ (verification loop)       │
├─────────────────────┤         ├──────────────────────────┤
│ search-everything   │         │ m2ctx / Get Context      │
│ get-function        │ ─asm──► │ m2c → compile → objdiff    │
│ manage-structures │ ─types► │ permuter (background)    │
│ match-function      │         │ AI agent (sandboxed)       │
└─────────────────────┘         │ integrator (post-match)    │
                              └──────────────────────────┘
```

## Runtime components

| Component | Location | Role |
|-----------|----------|------|
| Cursor plugin | `~/.cursor/plugins/local/matching-decompilation-re/` | Skills, agents, rules, hooks |
| Ghidra MCP | `agdec-http` | Binary analysis |
| Mizuchi (optional) | Separate install / `mizuchi run` | Full plugin orchestration |
| objdiff | Project PATH | Match verification |
| m2c / permuter | Project tools | Programmatic phase |

## User Ghidra environment

- [REPO] Programs e.g. `/K1/k1_win_gog_swkotor.exe`, `/TSL/k2_win_gog_aspyr_swkotor2.exe`
- [REPO] Shared server `170.9.241.140:13100/Odyssey` (when configured)

## Sandbox boundary

| Allowed | Forbidden |
|---------|-----------|
| Writes under `prompts/` (per `promptsDir`) | Direct edits to matched source tree during AI phase |
| Shell compile to temp `.o` | Claiming match without objdiff |
| Ghidra metadata (names, types) | Treating Ghidra decomp as proof |

## Hook wiring

Project `.cursor/hooks.json` may reference plugin `decomp-match-claim-guard.sh` on `stop` to flag unverified match claims.

## Cursor-native bridge

Without `mizuchi run`, workspace scripts mirror compile + objdiff + assembly view:

- `scripts/compile-and-view-assembly.sh` — AI sandbox (`compile_and_view_assembly` parity)
- `scripts/compile-trial.sh` — programmatic compile + gate
- `scripts/objdiff-gate.sh` — verification wrapper

Details: `docs/knowledgebase/50-execution/cursor-native-bridge.md`

## Canonical runtime docs

- `docs/knowledgebase/10-architecture-runtime/reference-pipeline.md` — article-faithful
  reference runtime
- `docs/knowledgebase/10-architecture-runtime/workspace-contract.md` — stable case and
  artifact contract

## [OPEN]

Mizuchi daemon not required for exploration; full plugin orchestration (m2c/permuter/integrator) still needs upstream Mizuchi or manual tool invocation. Golden `.o` files not yet wired for example prompt `fun_00148020`.
