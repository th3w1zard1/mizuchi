---
last_updated: 2026-05-29
---

# Strategy — Mizuchi / Odyssey matching decompilation

## Target problem

KOTOR and related Odyssey binaries need **provably correct** C recovery: pseudocode that compiles and matches the **original object file byte-for-byte**. Readable decompilation alone does not ship; only **objdiff 0** counts as matched.

## Our approach

**Ghidra for discovery, Mizuchi-style pipeline for proof.**

1. **Explore** in Ghidra (AgentDecompile MCP): find functions, types, xrefs, assembly.
2. **Package** each target as `prompts/<fn>/` (`prompt.md` + strict `settings.yaml`).
3. **Run** setup → programmatic (m2c, compile, objdiff, permuter) → sandboxed AI (`compile_and_view_assembly` loop).
4. **Integrate** only after objdiff 0, via worktree or manual stub replacement.

Cursor plugin `matching-decompilation-re` encodes skills, agents, rules, and hooks so agents follow this discipline without benchmarks or scoring studies.

## Who it's for

- Contributors matching functions in an Odyssey/KOTOR decomp tree
- AI agents operating under sandbox + verification invariants
- Anyone bridging shared Ghidra (`Odyssey` server) with local Mizuchi runs

## Key metrics

| Metric | Where | What good looks like |
|--------|-------|----------------------|
| Match gate | objdiff per function | **0 differences** |
| Prompt queue | `prompts/*/notes.md` state | `matched` / `integrated` growth |
| Atlas coverage | `mizuchi-db.json` (when indexed) | More similar matched examples in new prompts |
| Integrator health | Post-match build | Green compare after stub swap |

## Tracks

1. **Ghidra scout → prompt** — Recon binaries (`/K1/...`, `/TSL/...`), export asm + types into prompt folders.
2. **Programmatic first** — m2c + permuter before spending AI tokens; stop on perfect match.
3. **Sandboxed AI** — Agent uses `compile_and_view_assembly` only; no direct source edits until integrate.
4. **Integrate & index** — Land verified C; refresh Decomp Atlas for the next function.

## Not working on

- 60-function benchmark methodology or model leaderboards (article evaluation only)
- Semantic-only decompilation without objdiff proof
- Unsandboxed agent writes to the main tree during matching loops

## Marketing (optional)

Workflow grounded in [Macabeus's matching decompilation article](https://gambiconf.substack.com/p/can-llms-really-do-matching-decompilation) and [Mizuchi](https://github.com/macabeus/mizuchi). This workspace packages the **operational RE path**, not the paper's scoring experiment.
