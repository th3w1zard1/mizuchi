# Domain Theory: Matching Decompilation

## Core terms

| Term | Definition |
|------|------------|
| **Matching decompilation** | Recover C whose compiled object is byte-identical to original |
| **Golden object** | `targetObjectPath` — reference `.o` from original build |
| **Candidate object** | Output of compiling trial C |
| **objdiff** | Diff tool; **0 differences** = match |
| **m2c** | Asm → C decompiler; seed only |
| **m2ctx / Get Context** | Extracts types/macros/decls for a function |
| **decomp-permuter** | Mutates C to chase diff reduction |
| **Decomp Atlas** | Indexed DB of matched functions for prompt retrieval |

## Why object match?

[OFFICIAL] Decomp communities (e.g. Zelda OoT, game ports) use matching to prove behavioral equivalence at instruction level without executing original binary in CI.

[SYNTH] Ghidra pseudocode optimizes for readability, not register allocation — it cannot substitute for objdiff.

## Programmatic vs AI

| Phase | When it wins |
|-------|----------------|
| m2c + permuter | Small functions, clean asm, local permuter wins |
| AI | Register pressure, unusual idioms, compiler-specific patterns |

[OFFICIAL] Pipeline runs programmatic **first**, one-way, then AI — cheaper and often sufficient.

## Prompt engineering (Decomp Atlas)

Similar **matched** examples teach:

- Register variable naming patterns
- `(& 0xFF)` vs cast styles
- Helper call conventions

Include **call graph hints** so agent respects caller/callee ABI.

## Integrator theory

Matching proves **object** equivalence for one translation unit. Integrator proves **project** still builds and links.

## Mizuchi prompt contract

### settings.yaml (strict)

[REPO] Mizuchi validates exactly three keys (`src/shared/prompt-builder/prompt-settings.ts`):

| Field | Role |
|-------|------|
| `functionName` | Linker symbol in golden object |
| `targetObjectPath` | Golden `.o` for objdiff |
| `asm` | Full function assembly (GAS) from same build |

Optional provenance (tier, program, Ghidra address) belongs in **`notes.md`** or `prompt.md` — not extra YAML keys.

### compile_and_view_assembly

[REPO] MCP tool `mcp__mizuchi__compile_and_view_assembly` (`claude-runner-plugin.ts`):

- **Input:** `code` (C source), `function_name`
- **Output:** Compiled assembly + objdiff summary
- **0 differences:** message includes `PERFECT MATCH — submit this code`
- **Limit:** per-attempt tool call budget (`maxCompileToolCallsPerTurn`)

[SYNTH] In Cursor without Mizuchi daemon, emulate with Shell compile script + objdiff; same 0-diff gate.

### notes.md lifecycle

Track per-prompt state: `queued` → `in_progress` → `matched` (objdiff 0 logged) → `integrated` or `blocked`.

## Out of scope

- Benchmarking which LLM scores highest on N functions
- Semantic equivalence without object match
