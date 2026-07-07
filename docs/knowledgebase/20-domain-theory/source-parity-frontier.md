# Source Parity Frontier

Research date: 2026-06-29

This note records the practical frontier for getting from a compiled C/C++
program to source-code parity. It is scoped to `swkotor.exe`, but the strategy
matches current game-decomp practice: recover source by measured compiler
experiments, not by trusting a decompiler transcript.

## Core Principle

Every proposed source line is a compiler hypothesis. The compiler, flags,
prototypes, type layout, statement order, temporary lifetimes, object boundary,
and linker model jointly determine the emitted code. A behaviorally correct C
function can still fail source parity if any of those variables differ.

Therefore the loop is:

1. Choose a small target function or object.
2. Reconstruct a target object with symbols and relocations.
3. Propose high-level C/C++ from decompiler output, matched examples, and type
   hypotheses.
4. Compile with a recorded compiler profile.
5. Compare code and relocations with objdiff.
6. Promote only zero-diff results; classify every mismatch.

## Frontier Techniques Worth Using

| Technique | Best Use | Failure Mode |
| --- | --- | --- |
| Compiler-profile corpus | Identify compiler family/flags from many tiny probes | Trivial functions are too insensitive; use diverse probes. |
| Matched-example retrieval | Give agents local examples with the same idiom and compiler profile | Similar behavior can still require different source shape. |
| Assembly/decompiler embeddings | Find analogues by control flow, calls, constants, and memory access | Similarity is not a proof metric. |
| Ghidra BSim | Find semantically similar known functions despite compiler noise | BSim is tolerant by design; still diff the compiled result. |
| m2c/decompiler seeding | Quickly get behavior and data-flow scaffolding | Output is usually not idiomatic enough to match MSVC. |
| decomp-permuter | Search local expression/temporary/order variants once close | Inefficient when the candidate is structurally wrong. |
| AI one-shot | Generate plausible C for simple ranked functions | Requires known compiler/profile and immediate verifier feedback. |
| AI diff repair | Explain one mismatch cluster and change one source-shape hypothesis | Random rewrites destroy near-matches. |
| Runtime unpack/OEP capture | Recover the real executed code when static bytes are packed | Acquisition only; not source recovery by itself. |

## Compiler Variables To Measure, Not Assume

For early-2000s x86 MSVC-family binaries, the high-impact variables are:

- Compiler family/version: MSVC 6.0, VC7.0, VC7.1, VC8.0, era Intel C++, and
  MinGW as a negative control.
- Optimization: `/Od`, `/O1`, `/O2`, `/Ox`, `/Ob0`, `/Ob1`, `/Ob2`, `/Oi`,
  `/Ot`, `/Os`.
- Frame pointer: `/Oy` and `/Oy-`.
- Security/runtime checks: `/GS`, `/GS-`, `/GsN`, `/GZ`, `/RTC1`.
- Calling convention: `/Gd`, `/Gr`, `/Gz`, explicit `__cdecl`,
  `__fastcall`, `__stdcall`, and member-function `thiscall` modeling.
- Object/link granularity: `/Gy`, `/Gy-`, `/GL`, linker `/OPT:REF`,
  `/OPT:ICF`, `/ORDER`, object order, and library order.
- C++ ABI features: RTTI `/GR`, exception handling `/EH*` or old `/GX`,
  constructor/destructor emission, vtables, scalar/vector deleting destructors,
  and static initialization.
- Runtime/data model: `/ML`, `/MT`, `/MD`, `/GF`, static CRT inlining, string
  pooling, import/static-library resolution.
- Floating point: x87 versus SSE choices, `/fp:*` where available, and
  expression grouping.

The command line typed by the agent is not enough. Persist the effective
command line, compiler banner, environment, include/lib roots, source hash,
target object hash, candidate object hash, disassembly, relocation table, and
objdiff result.

## Source-Shape Variables To Search

When the compiler family is plausible but objdiff fails, source shape becomes
the main variable:

- Prototype and calling convention, including hidden `this` and return slots.
- Signedness and width of every load/store.
- Struct field offsets and aliasing shape.
- Whether expressions are written as casts, temporaries, pointer increments, or
  array indexing.
- Statement order and lifetime of locals.
- Local initialization style: aggregate init, explicit stores, `memset`, or
  constructor calls.
- Loop form: `for`, `while`, pre/post increment, compare direction, sentinel
  layout.
- Switch shape: jump table, if-ladder, lookup table, or compiler-generated
  range check.
- Inline boundary: helper function, macro, template, intrinsic, or inlined CRT.
- C++ object model: constructor chaining, base offsets, virtual calls, deleting
  destructor thunks, and exception cleanup.

## SWKOTOR-Specific Next Best Moves

1. Keep the accepted source shard honest: 574 verified high-level C functions,
   574/574 compile, 8047 inventory entries remaining.
2. Expand the compiler-profile corpus beyond current trivial/wrapper lanes:
   stack-frame functions, local initialization, calls with locals, loops,
   switches, virtual calls, floating point, constructors, destructors, and CRT
   wrappers.
3. Build a source-shape matrix for the two current nontrivial mismatches
   (`FUN_0086d201`, `FUN_0086d266`) instead of widening AI prompts first.
4. Add queue classes that group functions by idiom and required evidence:
   `stack-frame-probe`, `loop-probe`, `switch-probe`, `virtual-call-probe`,
   `ctor-dtor-probe`, `crt-wrapper-probe`, and `library-candidate`.
5. Index matched C/asm pairs from the 574 accepted functions and retrieve them
   by byte pattern, mnemonic n-grams, call targets, memory-offset shape, and
   calling convention.
6. Use AgentDecompile/Ghidra for semantics and type hypotheses, but keep the
   headless inventory as the authoritative function map until label/provider
   routing is synchronized.
7. Do not attempt whole-exe rebuild parity until function coverage, data
   objects, translation-unit boundaries, libraries, and linker order are modeled.

## Explicit Non-Strategies

- Whole-binary hash replay is not decompilation.
- `.incbin`, inline assembly, `.byte`, generated byte arrays, and copied target
  bytes are not recovered source.
- Ghidra pseudocode is not a match candidate until rewritten as C/C++ and
  compiled through the target compiler profile.
- A current LLM decompiler model producing recompilable or readable code is not
  evidence of code parity unless objdiff says zero differences.
- A PE linker version or one trivial match does not identify the compiler.

## Sources

- Chris Lewis, one-shot decompilation workflow:
  https://blog.chrislewis.au/the-unexpected-effectiveness-of-one-shot-decompilation-with-claude/
- Simon Willison summary with implementation links:
  https://simonwillison.net/2025/Dec/6/one-shot-decompilation/
- Bruno Macabeus, Mizuchi matching-decompilation benchmark:
  https://macabeus.medium.com/can-llms-really-do-matching-decompilation-i-tested-60-functions-to-find-out-4e39b0ae4288
- objdiff:
  https://github.com/encounter/objdiff
- decomp.me FAQ:
  https://www.decomp.me/faq
- decomp-permuter:
  https://github.com/simonlindholm/decomp-permuter
- Microsoft compiler options:
  https://learn.microsoft.com/en-us/cpp/build/reference/compiler-options
- Microsoft `/O1` and `/O2`:
  https://learn.microsoft.com/en-us/cpp/build/reference/o1-o2-minimize-size-maximize-speed
- Microsoft `/GS`:
  https://learn.microsoft.com/en-us/cpp/build/reference/gs-buffer-security-check
- Ghidra BSim:
  https://github.com/NationalSecurityAgency/ghidra
- LLM4Decompile:
  https://arxiv.org/html/2403.05286v3
