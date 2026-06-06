# Reference Pipeline (Article-Faithful v1)

This document is the canonical runtime shape for Mizuchi's first implementation.

The goal is not "generic decompilation" on day one. The goal is to encode the exact
matching-decompilation loop described by the article and already reflected in this
workspace, then migrate that loop into a more self-contained product without weakening
the proof model.

## Product contract

Success means a verified match, not merely plausible C.

- **Reference proof:** compiled candidate passes `objdiff` against the golden object
- **Current workspace gate:** `scripts/objdiff-gate.sh` / `scripts/run-objdiff.sh`
- **Integration rule:** no source-tree landing before the proof gate passes

Anything weaker than proof may guide the next attempt, but it never changes the case
state to "matched".

## Canonical phases

### 1. Discovery

Use Ghidra / AgentDecompile to identify the target function, assembly, xrefs, types,
and decompiler hints.

Primary outputs:

- target binary identity
- function/symbol identity
- assembly excerpt
- context requirements (types, globals, callees, calling convention)

### 2. Case packaging

Create a prompt folder under `prompts/<case-id>/` with these identity artifacts:

- `case.yaml` — stable case identity and proof contract
- `settings.yaml` — Mizuchi tool contract (`functionName`, `targetObjectPath`, `asm`)
- `prompt.md` — agent-facing brief
- `notes.md` — operator notes and non-authoritative commentary

`case.yaml` is the cross-platform architecture contract. `settings.yaml` remains the
strict article/Mizuchi tool contract for the current workflow.

### 3. Context build

Gather compiler-visible context before trying AI:

- project headers / `m2ctx` / Get Context output
- struct and typedef information
- externs, callees, and relevant macros

Current bridge note: the workspace still uses the shared `context/ctx.h` path in some
flows. That is a legacy execution detail, not the long-term architecture boundary.

### 4. Programmatic pass

Run deterministic tooling before spending AI attempts:

- `m2c`
- compile trial C
- `objdiff`
- `decomp-permuter`

Stop early if a deterministic pass reaches a verified match.

### 5. Sandboxed AI loop

Only after the programmatic phase stalls:

- generate or refine trial C
- compile
- inspect assembly / `objdiff`
- iterate inside the prompt folder boundary

The AI loop may read broadly, but it only writes within the case workspace during the
matching phase.

### 6. Verification

Verification is a separate phase, not an optimistic interpretation of logs.

Required proof artifacts:

- golden object path
- candidate object path
- `objdiff` result
- machine-readable run outcome

### 7. Integration

Landing matched code into the target decompilation tree is post-proof work:

- replace or update the source stub
- rebuild the target tree
- confirm project-level integration still holds

## Generic core vs target adapters

The runtime must evolve toward this separation:

| Layer | Responsibility |
|------|-----------------|
| **Core orchestrator** | phase ordering, retries, run state, proof gating, artifact recording |
| **Target adapter** | binary layout, symbol lookup, toolchain selection, context extraction hooks |
| **Programmatic tools** | m2c, compile, objdiff, permuter, static validators |
| **AI runner** | compile-and-compare loop with sandbox limits |
| **Integrator** | target-tree landing after proof |

Odyssey/KOTOR is the first adapter family, not the architecture itself.

## Migration path to the cross-platform app

### Stage A — reference pipeline hardening

Keep the current scripts, but make the runtime contract explicit and machine-checkable.

### Stage B — app-owned orchestration

Move phase sequencing, state transitions, and artifact indexing out of ad hoc script
composition and into one orchestrator surface.

### Stage C — adapter-driven expansion

Add a second target family by implementing adapter contracts rather than rewriting the
orchestrator.

### Stage D — self-contained product shell

Wrap the orchestrator in one installable app with agent/UI parity over the same case
workspace.

## Invariants

1. Never claim a match without proof.
2. Run the programmatic phase before the AI phase.
3. Treat Ghidra output as exploration, not proof.
4. Keep target-specific assumptions behind adapter boundaries.
