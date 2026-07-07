# Source Parity Strategy

Research date: 2026-06-29

This is the practical strategy for recovering C/C++ source that can be proved
against `swkotor.exe`. It is intentionally stricter than semantic
decompilation. The target is source code that compiles into matching executable
code, not pseudocode that merely explains behavior.

## Ground Truth From Current Practice

- Matching decompilation is a compile-and-diff discipline. decomp.me describes
  a scratch as a single-function playground with target assembly, context, and
  selectable compiler options.
- objdiff's project model compares target objects against base objects built
  from current source. The local source tree is only as proven as the object
  differences say it is.
- decomp.dev's integration model requires target objects for the matched binary
  and base objects from source-built code. Full progress is object coverage, not
  whole-file hashing.
- ReconstructKit/Kappa-style AI flows are orchestration: Get Context -> m2c ->
  compiler -> objdiff -> permuter -> AI loop. The AI proposes source; the
  compiler and objdiff accept or reject it.
- LLM decompiler research such as LLM4Decompile and recompilable-decompilation
  work improves readability, compilability, and re-executability. That is useful
  for hypotheses, but it is not enough for source parity because it does not
  optimize for byte-identical MSVC object output.

## The Real Problem

For an old x86 C/C++ game, behavior is only half the problem. A behaviorally
correct function can still fail objdiff because of:

- wrong compiler family or exact version,
- wrong optimization/debug flags,
- wrong calling convention or prototype,
- wrong struct layout or signedness,
- different source expression shape,
- different temporary variable lifetime,
- different inlining boundary,
- different object, library, or link order.

This is why a decompiler cannot reverse the whole executable back to original
source in one pass. The source has to be reconstructed as a sequence of
evidence-backed compiler experiments.

## SWKOTOR Work Plan

1. Use the Steamless-unpacked analysis image as the code source. The packed
   Steam `.text` view is loader/packed bytes and is not a source-parity target.
2. Maintain an executable coverage map: `.textV` functions, `.bindV` loader
   functions, undecoded ranges, thunks, jump tables, imports, globals, and
   relocation sites.
3. Build target objects from function slices, reconstructing symbolic
   relocations where the linked image has direct call/jump displacements.
4. Expand the compiler-profile corpus before attacking large functions. The
   corpus must include trivial accessors, stack-frame functions, wrappers,
   loops, switches, integer arithmetic, floating point, constructors,
   destructors, and exception-adjacent code.
5. Sweep historical toolchains and flags mechanically. For a February 2004
   Win32 game, keep MSVC 6.0, VC7.0, VC7.1, VC8.0, era Intel C++, and MinGW as
   candidate or negative-control families.
6. Record every profile run: compiler banner, root path, environment,
   include/lib paths, command line, source hash, target hash, object hash,
   disassembly, relocation table, objdiff result, and mismatch class.
7. Use Ghidra, AgentDecompile, m2c, LLM4Decompile-like models, and AI agents
   only to propose source hypotheses. No hypothesis counts until compiled C/C++
   reaches objdiff zero for its target slice.
8. Use decomp-permuter only when the candidate is already close enough that
   register allocation, temporary placement, or expression ordering is the main
   remaining mismatch.
9. Promote matched functions into source shards only after the per-function
   proof is stored. Promote shards into translation units only after prototypes,
   globals, static data, and object-boundary conflicts are reconciled.
10. Attempt whole-executable code parity only after every executable byte range
    is covered by accepted high-level source or explicitly scoped non-C code.

## Compiler Flags To Treat As First-Class Variables

For x86 MSVC matching, these are not incidental:

| Area | Flags / Inputs | Why It Matters |
| --- | --- | --- |
| Optimization | `/Od`, `/O1`, `/O2`, `/Ox`, `/Ob*`, `/Oi`, `/Ot`, `/Os` | Changes scheduling, inlining, instruction selection, and local layout. |
| Frame pointer | `/Oy`, `/Oy-` | Changes EBP-based stack frames across most non-leaf functions. |
| Security/runtime checks | `/GS`, `/GS-`, `/RTC*`, `/GZ`, `/GsN` | Inserts cookies, local initialization, stack verification, and probes. |
| Calling convention | `/Gd`, `/Gr`, `/Gz`, explicit `__cdecl`, `__fastcall`, `__stdcall` | Changes register arguments, symbol names, and caller/callee stack cleanup. |
| Object granularity | `/Gy`, `/Gy-`, `/GL`, `/GL-` | Changes COMDATs, link-time optimization, object contents, and progress accounting. |
| C++ model | `/GR`, `/GR-`, `/EH*`, old `/GX` | Changes RTTI, unwind/destructor scaffolding, and class metadata. |
| Runtime/data | `/MD`, `/MT`, old `/ML`, `/GF`, `/GF-` | Changes inline CRT behavior, default libraries, strings, and data references. |
| Architecture | `/G6`, `/G7`, `/arch:*` where supported | Changes instruction choices and scheduling assumptions. |

The local wrapper may set defaults, so the recorded profile must include the
effective command line, not just the command line typed by the agent.

## Current Local Evidence Boundary

- Function inventory: `8621` total entries, including `8616` `.textV`
  functions and `5` `.bindV` loader functions.
- Verified high-level C/C++ source matches: `574` functions.
- Current lanes: `256` simple no-relocation functions plus `318`
  relocation-aware wrappers/thunks.
- Current split source shard: `574` C files compiled with MSVC8 `/O2 /GS- /Oy`
  and `0` compile failures.
- Remaining inventory entries: `8047`.

This is real progress, but it is not whole-executable source parity. The next
hard unlock is nontrivial function matching under a better compiler-profile and
source-shape search process.

## Sources

- objdiff: https://github.com/encounter/objdiff
- decomp.me FAQ: https://www.decomp.me/faq
- decomp.dev integration guide: https://decomp.wiki/tools/decomp-dev
- Macabeus, ReconstructKit matching-decompilation benchmark:
  https://macabeus.medium.com/can-llms-really-do-matching-decompilation-i-tested-60-functions-to-find-out-4e39b0ae4288
- Chris Lewis one-shot workflow mirror:
  https://simonwillison.net/2025/Dec/6/one-shot-decompilation/
- Microsoft `/O1` and `/O2` docs:
  https://learn.microsoft.com/en-us/cpp/build/reference/o1-o2-minimize-size-maximize-speed
- Microsoft `/GS` docs:
  https://learn.microsoft.com/en-us/cpp/build/reference/gs-buffer-security-check
- Microsoft calling convention docs:
  https://learn.microsoft.com/en-us/cpp/build/reference/gd-gr-gv-gz-calling-convention
- LLM4Decompile paper: https://arxiv.org/html/2403.05286v3
- decomp-permuter: https://github.com/simonlindholm/decomp-permuter
