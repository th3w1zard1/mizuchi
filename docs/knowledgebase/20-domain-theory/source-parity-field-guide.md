# Source Parity Field Guide

Research date: 2026-06-29

This document is the operating model for turning a stripped C/C++ executable
into a source tree that can be proved against the original code. It deliberately
separates matching decompilation from byte-source packaging and from semantic
decompiler output.

Read `source-parity-strategy.md` first for the current high-level plan and
`source-parity-compiler-forensics.md` for the detailed swkotor evidence log.

## Definition Of Done

Source parity means high-level C/C++ source that, when compiled with the
original-equivalent toolchain and linked with the original-equivalent object
order, libraries, sections, and relocations, reproduces the target executable
code.

For this workspace, the acceptance ladder is:

1. Function parity: a C/C++ candidate compiles to an object whose code and
   relocations objdiff reports as zero differences against the target slice.
2. Translation-unit parity: recovered functions and data compile together as a
   source-built object that matches a full target object, not just isolated raw
   bytes.
3. Link parity: all source-built objects link into an image whose executable
   ranges match the unpacked target executable, with data/resources/debug/PE
   metadata scoped separately.

Byte emitters, inline bytes, `.incbin`, generated C byte arrays, and full-file
roundtrips can prove acquisition and replay plumbing. They do not prove source
parity.

## What Current Practice Actually Does

Matching-decompilation projects do not ask a model to emit a full program and
trust it. They build a measured loop around one function or one object at a
time:

- decomp.me describes matching as writing high-level code that compiles to
  assembly identical to the original, usually requiring the same compiler,
  assembler, and flags. It also works on individual functions, not whole
  binaries.
- objdiff compares relocatable object files. Its normal project model has
  target objects, base objects built from current source, and an `objdiff.json`
  unit list that represents every object in the linked binary.
- decomp.dev's integration guide treats target objects as the matched state of
  the binary and base objects as source-built objects. A complete progress view
  requires every linked object, including objects without source yet.
- Chris Lewis' one-shot workflow is a throughput wrapper: score likely-easy
  functions, create a narrow matching environment, let the agent attempt that
  one function, build/diff, commit only verified matches, and log hard cases.
- Mizuchi's published benchmark uses a plugin pipeline:
  Get Context -> m2c -> Compiler -> Objdiff -> Permuter -> AI loop. The AI
  phase still submits C to a compiler and objdiff gate; it is not a replacement
  for the gate.
- LLM4Decompile-style research optimizes readable and re-executable decompiled
  C. That is useful for hypotheses, naming, and control-flow recovery, but it
  does not target byte-identical MSVC output and should not be used as a match
  claim by itself.

## Compiler Profile Comes First

If the compiler profile is wrong, every nontrivial function becomes noise.
Before asking an agent to match large functions, build a local compiler-profile
corpus:

1. Pick target slices that are small, varied, and semantically obvious:
   getters/setters, zero returns, stack-frame functions, calls, virtual calls,
   loops, floating-point operations, switch/jump-table code, and C++ destructors.
2. For each slice, write the simplest plausible C/C++ source without byte
   emitters or inline assembly.
3. Compile the same source across candidate toolchains and flags.
4. Diff code and relocation output, not whole-file PE metadata.
5. Persist the exact `cl.exe` banner, root path, include/lib paths, command
   line, source SHA256, object SHA256, and objdiff result.
6. Promote a compiler profile only when multiple independent probe categories
   match. One trivial function does not identify a compiler.

For x86 MSVC parity, these controls are especially byte-sensitive:

| Control | Why It Matters |
| --- | --- |
| Compiler version | MSVC 6, 7.0, 7.1, 8.0, Intel C++, and MinGW produce different idioms. |
| `/O1`, `/O2`, `/Ox`, `/Od` | Optimization level controls scheduling, inlining, stack allocation, and instruction selection. |
| `/Oy` and `/Oy-` | Frame-pointer omission changes nearly every stack-using optimized function. Microsoft docs state `/O1` and `/O2` imply `/Oy` on x86 in current MSVC documentation. |
| `/GS` and `/GS-` | Stack cookie prologues/epilogues appear only for qualifying functions and change local layout. |
| `/GsN` | Stack-probe threshold changes `_chkstk` insertion. |
| `/Gd`, `/Gz`, `/Gr` | Default cdecl/stdcall/fastcall changes decoration, argument registers, and stack cleanup. |
| `/Ob0`, `/Ob1`, `/Ob2` | Inline expansion changes call boundaries and object contents. |
| `/Gy` and `/Gy-` | Function-level linking changes COMDAT/object granularity and linker behavior. |
| `/GR` and `/GR-` | RTTI changes C++ metadata and may affect class-related code. |
| `/EH*` or old `/GX` | Exception handling model changes unwind/destructor scaffolding. |
| `/fp:*` | Floating-point model allows or forbids reordering/contraction and changes x87/SSE codegen choices. |
| `/MD`, `/MT`, `/ML` | Runtime-library model changes defines and inline CRT behavior, and can imply static vs dynamic runtime linkage. |
| `/GF` and `/GF-` | String pooling changes data layout and code references to strings. |

The profile also includes linker behavior: object order, library order,
`/OPT:REF`, `/OPT:ICF`, `/ORDER`, section alignment, subsystem version, and
whether incremental/debug metadata was present. Whole-executable parity cannot
be proved from function objects alone.

## SWKOTOR-Specific Evidence So Far

Facts established locally:

- The Steam file is PE32 x86 and SteamStub-packed. The static packed `.text`
  is not original compiler output.
- Steamless produced an unpacked analysis image with normal `.textV` game code.
- The unpacked image header reports timestamp `Thu Feb 12 12:15:53 2004`,
  linker version `7.0`, stripped relocations/symbols, PE32 GUI subsystem 4.0,
  and no Rich header marker in the first `0x400` bytes.
- The unpacked import table does not show an MSVCR/MSVCP DLL import in the
  inspected import list; strings include the Microsoft Visual C++ runtime error
  text, so a statically linked MSVC runtime is plausible but not sufficient
  proof of the exact compiler.
- The current verified high-level coverage is partial: 574 `.textV` functions
  out of 8621 inventory entries, split across 256 simple no-relocation
  functions and 318 relocation-aware wrappers/thunks.
- The current compiler-profile sweep over `FUN_0086d201` and `FUN_0086d266`
  found no perfect match across VC7.1/VC8 and the tested `/Od`, `/O1`, `/O2`,
  `/GZ`, `/RTC1`, `/G7`, `/Oi`, `/Oy`, and `/GS-` combinations. That means
  either the source hypothesis is wrong, the flag space is incomplete, the
  function is not representative, or the compiler/toolchain differs.

Implication: the useful path is not "run one-shot on the whole exe." The useful
path is to expand the compiler-profile corpus and recovered function set while
preserving strict evidence boundaries.

## Recommended Work Order

1. Build the target map from the unpacked image:
   executable ranges, function boundaries, undecoded bytes, imports, thunks,
   jump tables, RTTI/vtables, exception data, strings, and globals.
2. Decode compiler provenance:
   PE headers, import/runtime model, static CRT strings, possible PDB paths,
   RTTI style, prologue/epilogue idioms, call conventions, and a probe corpus.
3. Maintain several candidate compiler profiles instead of one guess. Keep
   negative evidence, because mismatches explain which source or flag hypothesis
   is wrong.
4. Use Ghidra, LLM4Decompile-like models, and m2c-like tools only for semantic
   hypotheses. They feed the candidate C, not the acceptance decision.
5. Recover easiest functions first:
   accessors, constants, thunks, wrappers, simple math, string calls, and
   constructors/destructors with clear patterns.
6. Use matched examples to seed harder prompts:
   same compiler profile, same class/namespace if known, same call convention,
   same data-access style, and same local idioms.
7. Use decomp-permuter only once a candidate is close enough that register
   allocation or statement ordering is the primary remaining mismatch.
8. Promote isolated function slices into translation units only after prototype,
   global, data, and relocation conflicts are resolved.
9. Track coverage by executable byte ranges and function count. A source file
   that compiles is not counted unless objdiff proves its target slice.
10. Defer whole-exe link parity until object layout, library order, data
    sections, and linker settings are explicitly modeled.

## Compiler-Profile Operating Rules

Treat compiler identification as a measured forensics problem, not a preference
setting. A usable profile is a versioned artifact with positive and negative
evidence:

1. Candidate toolchain root and `cl.exe` banner.
2. Environment variables, include roots, lib roots, and runtime-library model.
3. Full compile command, including default flags injected by wrappers.
4. Probe source and target slice hashes.
5. Object hash, disassembly, relocation table, and objdiff result.
6. Notes explaining which mismatch cluster each failed flag combination
   produced.

The first promoted profile should match several independent idiom classes:
simple returns, stack-frame code, calls, tail calls, local initialization,
integer arithmetic, floating point, switch/jump tables, constructors,
destructors, and exception-adjacent code. Trivial accessors are valuable for
coverage, but they are too insensitive to identify the compiler by themselves.

When the matrix fails, do not widen the AI prompt first. Classify the mismatch:

| Mismatch | First Hypothesis To Test |
| --- | --- |
| EBP frame present/missing everywhere | `/Oy`, `/Oy-`, optimization level |
| Stack cookies appear/disappear | `/GS`, buffer classification, local layout |
| Caller/callee stack cleanup differs | Calling convention or prototype |
| Same operations but different temporaries | Source shape and expression decomposition |
| `inc`/`dec`, `leave`, `push imm; pop reg` mismatch | Compiler era or optimization/debug mode |
| Extra stack checks | `/GsN`, local stack size, alloca-like code |
| Different call targets or relocations | Prototype, import/static library resolution, object boundaries |

This is the part that makes matching decompilation hard: compiler flags and
source shape are coupled. A correct behavior hypothesis can still be a bad
match if it induces different temporaries, different stack slots, or different
cleanup. The loop should therefore change one compiler-profile or source-shape
hypothesis at a time and preserve every failed result as future evidence.

## Agent Rules

- Never claim "source recovered" for byte emitters, assembly includes, `.byte`,
  `.incbin`, or copied original bytes.
- Never claim "compiler identified" from a PE linker version or one matched
  trivial function.
- Never let an LLM self-certify a match. The only function match gate is
  compiler output plus objdiff zero differences.
- When a function fails to match after several attempts, record the best
  mismatch pattern and move it into compiler-profile or type-hypothesis work.
- Prefer boring, repeatable logs over heroic attempts. A failed flag sweep is
  progress if it narrows the compiler/source search space.

## Sources Consulted

- objdiff README: https://github.com/encounter/objdiff
- decomp.me FAQ: https://www.decomp.me/faq
- decomp.dev integration guide: https://decomp.wiki/tools/decomp-dev
- Chris Lewis, one-shot decompilation workflow:
  https://blog.chrislewis.au/the-unexpected-effectiveness-of-one-shot-decompilation-with-claude/
- Bruno Macabeus, Mizuchi benchmark:
  https://macabeus.medium.com/can-llms-really-do-matching-decompilation-i-tested-60-functions-to-find-out-4e39b0ae4288
- Microsoft C++ compiler options:
  https://learn.microsoft.com/en-us/cpp/build/reference/compiler-options
- LLM4Decompile paper:
  https://aclanthology.org/2024.emnlp-main.203/
- LLM4Decompile repository:
  https://github.com/albertan017/LLM4Decompile
- m2c README: https://github.com/matt-kempster/m2c
- decomp-permuter README: https://github.com/simonlindholm/decomp-permuter
