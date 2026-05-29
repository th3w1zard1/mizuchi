# Matching Decompilation — Intent

## Target problem

Reverse engineers need **verifiable** C recovery for legacy binaries (game ports, embedded firmware) where "looks right" decompilation is insufficient — only **byte-identical object code** proves equivalence.

## Our approach

Use a **phased pipeline**: gather types/asm (Get Context + Ghidra) → programmatic m2c/compile/objdiff/permuter → sandboxed AI iteration → optional integrator. **objdiff 0** is the sole match gate.

## Who it's for

- Decomp project contributors matching functions in C
- AI agents assisting matching under sandbox + verification rules
- Odyssey/KOTOR-style workflows using shared Ghidra + Mizuchi patterns

## Success signals

| Signal | Meaning |
|--------|---------|
| objdiff 0 on target `.o` | Function matched |
| Build green after integrate | Landed safely |
| `mizuchi-db.json` growth | Decomp Atlas improving future prompts |

## Non-goals

- LLM benchmark leaderboards (excluded from this knowledgebase)
- Replacing human review for **semantic** correctness beyond object match
- Unsandboxed agent writes to main project tree

## Plugin anchor

Cursor plugin: `~/.cursor/plugins/local/matching-decompilation-re/`

Commands: `/decomp-function`, `/decomp-prompt`, `/ghidra-scout`

## Evidence

- [OFFICIAL] Macabeus article + Mizuchi README — see `matching-decompilation-re/docs/research-brief.md`
- [SYNTH] This workspace uses AgentDecompile MCP + optional Mizuchi CLI when installed
