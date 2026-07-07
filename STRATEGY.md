---
last_updated: 2026-07-06
---

# Strategy — Mizuchi source parity recovery

## Target problem

KOTOR and related compiled binaries need **provable source parity**, not just
readable decompiler output. The real target is high-level C/C++ that rebuilds
the original executable code through the original-equivalent compiler, flags,
ABI, object boundaries, and linker behavior. Readable pseudocode, byte replay,
and whole-file hash roundtrips are useful support lanes, but they are not
source recovery.

## Our approach

**Acquisition first, compiler forensics second, matching third.**

1. **Acquire the real code target.** For packed PE targets such as
   `swkotor.exe`, operate on the unpacked analysis image and maintain an
   executable coverage map.
2. **Recover boundaries and context without heavyweight decompiler services.**
   Prefer binary inventory, symbol/map data, disassembly, existing unmatched
   assembly, upstream Mizuchi prompts, m2c, decomp-permuter, and compiler
   feedback. These are candidate-generation inputs, not proof.
3. **Build a compiler-profile corpus.** Identify compiler family, version,
   flags, ABI, and object granularity through diverse small probes before
   spending effort on large functions.
4. **Match one function or one object at a time.** Generate high-level source
   candidates automatically, compile with a recorded toolchain profile, and
   accept only `objdiff 0` against a relocation-aware target object or an
   equivalent stronger gate.
5. **Promote upward only after proof.** Move from function matches to
   translation units, then to linked executable-code parity, while keeping
   data/resources/debug metadata explicitly separate.

This workspace exists to make that loop operational and agent-usable. It is not
a claim that arbitrary whole-program source recovery is already solved.

## Who it's for

- Contributors recovering source parity for game binaries such as KOTOR
- AI agents operating under strict proof and claim-boundary rules
- Reverse engineers who need a simple bridge from binary/disassembly context to
  compiler-in-the-loop verification

## Key metrics

| Metric | Where | What good looks like |
|--------|-------|----------------------|
| Function parity | Target object or relocation-aware slice compare | **objdiff 0** or equivalent strong object parity |
| Compiler forensics | Profile corpus receipts | Multiple idiom classes agree on one profile |
| Source coverage | `recovered-source/coverage.json` | More verified high-level functions, not just generated candidates |
| Translation-unit parity | Source-built object compare | Full object match, not isolated byte snippets |
| Link parity | Executable-code compare | Rebuilt code ranges match unpacked target code |

## Tracks

1. **Executable acquisition and coverage**
   - Unpack, inventory, and map executable ranges, thunks, imports, globals,
     RTTI/vtables, jump tables, and undecoded regions.
2. **Compiler-profile corpus**
   - Sweep candidate toolchains and flags across diverse small probes; persist
     positive and negative evidence as first-class artifacts.
3. **Relocation-aware function/object verification**
   - Promote from weak code-slice byte checks toward true target-object
     reconstruction and `objdiff` parity.
4. **Automatic candidate generation**
   - Use matched-example retrieval, source-shape synthesis, one-shot generation,
     m2c/decomp-permuter style programmatic tools, and local repair loops to
     propose C/C++ automatically.
5. **Translation-unit and link parity**
   - Reassemble verified functions, data objects, and libraries into source
     shards, then into linked executable-code parity.

## Not working on

- Claiming semantic source recovery from byte emitters, `.incbin`, inline
  assembly, or copied target bytes
- Treating decompiler pseudocode as proof rather than context
- Trusting current LLM decompiler output without compiler-in-the-loop
  verification
- Pretending whole-executable parity is already available before function,
  object, translation-unit, and linker evidence exists

## Marketing (optional)

Workflow grounded in Bruno Macabeus' matching-decompilation methodology, Chris
Lewis' one-shot throughput workflow, objdiff/decomp.dev style proof gates, and
compiler-in-the-loop repair. This workspace aims to operationalize the real
source-parity path, not to market semantic decompilation as already solved.
