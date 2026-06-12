# Matching Decompilation — Intent

## Target problem

Reverse engineers need **verifiable** recovery for existing applications and binaries
where "looks right" output is insufficient. In Mizuchi's current strongest workflow,
that means recovering C that recompiles to **byte-identical object code**.

## Our approach

Use a **phased pipeline** inside one runtime: intake and normalize a case, gather
types/asm/context, run programmatic tooling, then use sandboxed AI iteration only when
deterministic passes stall. In the matching-decompilation path, **objdiff 0** remains
the sole match gate.

## Who it's for

- Decomp project contributors matching functions in C
- AI agents assisting matching under sandbox + verification rules
- Reverse-engineering workflows that need one proof-aware entrypoint rather than a
  manually driven stack of tools

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
- Treating Odyssey/KOTOR as the long-term product boundary

## Plugin anchor

Cursor plugin: `~/.cursor/plugins/local/matching-decompilation-re/`

Commands: `/decomp-function`, `/decomp-prompt`, `/ghidra-scout`

## Evidence

- [OFFICIAL] Macabeus article + Mizuchi README — see `matching-decompilation-re/docs/research-brief.md`
- [SYNTH] This workspace uses AgentDecompile MCP + optional Mizuchi CLI when installed
- [SYNTH] The broader product/runtime boundary lives in `docs/knowledgebase/10-architecture-runtime/universal-entrypoint-architecture.md`
