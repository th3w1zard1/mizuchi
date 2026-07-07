# Architecture & Runtime

## Runtime design

```
┌──────────────────────┐
│ ReconstructKit-style loop    │
├──────────────────────┤
│ prompt/context input  │
│ m2c → compile         │
│ objdiff gate          │
│ permuter / AI loop    │
│ integrator post-match │
└──────────────────────┘
```

## Runtime components

| Component | Location | Role |
|-----------|----------|------|
| Cursor plugin | `~/.cursor/plugins/local/matching-decompilation-re/` | Skills, agents, rules, hooks |
| ReconstructKit (optional) | Separate install / `reconkit run` | Full plugin orchestration |
| objdiff | Project PATH | Match verification |
| m2c / permuter | Project tools | Programmatic phase |

## Sandbox boundary

| Allowed | Forbidden |
|---------|-----------|
| Writes under `prompts/` (per `promptsDir`) | Direct edits to matched source tree during AI phase |
| Shell compile to temp `.o` | Claiming match without objdiff |
| Target assembly and object-slice metadata | Treating decompiler pseudocode as proof |

## Hook wiring

Project `.cursor/hooks.json` may reference plugin `decomp-match-claim-guard.sh` on `stop` to flag unverified match claims.

## Cursor-native bridge

Without `reconkit run`, workspace scripts mirror compile + objdiff + assembly view:

- `scripts/compile-and-view-assembly.sh` — AI sandbox (`compile_and_view_assembly` parity)
- `scripts/compile-trial.sh` — programmatic compile + gate
- `scripts/objdiff-gate.sh` — verification wrapper

Details: `docs/knowledgebase/50-execution/cursor-native-bridge.md`

## [OPEN]

ReconstructKit daemon is not required for the local loop; full upstream-style orchestration (m2c/permuter/integrator/reporting) still needs a tighter implementation here. Golden `.o` files are not yet wired for example prompt `fun_00148020`.
