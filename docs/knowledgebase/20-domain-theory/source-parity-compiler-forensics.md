# Source Parity And Compiler Forensics

Research date: 2026-06-28
Updated: 2026-06-29

For the current operating model, read this together with
`source-parity-field-guide.md`. This file records the compiler-forensics
details; the field guide records the source-parity workflow and claim boundary.

## Claim Boundary

Source parity means C/C++ source that, when built with the original-equivalent
compiler, flags, assembler, libraries, and link layout, reproduces the target
machine code. Byte emitters, inline `.byte`, copied assembly, or whole-file hash
roundtrips are useful acquisition fixtures, but they are not source recovery.

For `swkotor.exe`, the acceptable near-term proof is function-level objdiff with
zero differences on executable code. Full-app proof is only valid after all
executable code is covered and a rebuilt image matches executable code sections
or mapped function ranges while explicitly ignoring resource/data/debug/metadata.

## External Methodology

The Chris Lewis one-shot workflow is a throughput layer on top of a normal
matching-decomp project:

1. Score functions by likely difficulty.
2. Create a narrow function matching environment.
3. Let the agent write plausible C for that one function.
4. Compile with the project compiler and compare against the target.
5. Commit only when the match gate passes; otherwise mark the function hard.

It does not bypass compiler identification, function slicing, types, or objdiff.
The Macabeus/Mizuchi framing is the same: programmatic tooling and LLMs both
feed a compile-and-diff loop, and the benchmark is whether candidate C reaches a
byte-for-byte assembly match. Academic LLM decompiler work such as
LLM4Decompile is useful for semantic hypotheses and readability, but its
published metric is re-executability/readability rather than object-code parity.

## Source-Parity Best Practices

### 1. Prove The Target Is Matchable Code

- Do not start from an encrypted, compressed, or loader-wrapped `.text` section
  and call the bytes source. First identify the real executed code.
- In PE files, treat executable section flags as acquisition hints, not proof of
  compiler output. Packers and launchers can put loader code in executable
  sections and keep original code compressed or transformed.
- Build a coverage map: executable sections, discovered functions, undecoded
  regions, jump tables, thunks, import stubs, and hand-written assembly.

### 2. Infer Compiler Family Before Tuning C

Compiler choice dominates syntax tweaks. For x86 MSVC-era binaries, fingerprint
with small functions before trying large source recovery:

| Fingerprint | What It Usually Tests |
| --- | --- |
| `push ebp; mov ebp, esp` vs frame-pointer omission | `/Oy`, `/Oy-`, optimization level |
| `leave` vs `mov esp, ebp; pop ebp` | compiler version/codegen style |
| `and dword ptr [ebp-X], 0` vs `mov [ebp-X], 0` | compiler version and debug-codegen idiom |
| `inc`/`dec` vs `add/sub reg, 1` | compiler version and optimization/codegen era |
| `push imm; pop reg` vs `mov reg, imm` | old MSVC codegen idiom |
| callee `ret N` vs caller stack adjustment | `__stdcall` vs `__cdecl` |
| stack cookie prologue/epilogue | `/GS` on/off and buffer classification |
| `_chkstk` calls | `/Gs` threshold and local stack size |
| COMDAT/function sections | `/Gy`, link-time layout, inlining boundaries |
| string pooling and const placement | `/GF`, `/Gf`, data-section layout |

PE `MajorLinkerVersion` is useful but not definitive. It fingerprints the
linker field in the image, not necessarily every compiler that produced object
members. Use it to rank candidate toolchains, then verify by generated code.

### 3. Sweep Toolchains And Flags Mechanically

Do not guess flags by hand. Build a compiler-profile corpus from short target
functions and synthetic probes, then run a matrix:

- Toolchain candidates for a February 2004 Win32 game: MSVC 6.0, VC7.0
  (Visual Studio .NET 2002), VC7.1 (Visual Studio .NET 2003 / Visual C++
  Toolkit 2003), VC8.0, Intel C++ of the era, and MinGW only as a negative
  control.
- Baseline codegen flags: `/Od`, `/O1`, `/O2`, `/Ox`, `/Ob0`, `/Ob1`, `/Ob2`,
  `/Oi-`, `/Oy-`, `/Oy`, `/GS-`, `/GS`, `/Gd`, `/Gz`, `/Gr`, `/GX-`, `/EHsc`,
  `/GR-`, `/GR`, `/Gy-`, `/Gy`, `/GF-`, `/GF`, `/Z7`, `/Zi`.
- Historical/debug flags to test when old idioms appear: `/GZ` and `/RTC1`
  where supported; `/ML`, `/MT`, `/MD` for pre-VC8 runtime models; `/G6`,
  `/G7`, and `/arch:IA32` or SSE-family switches where supported.
- Linker/layout flags are a later phase: `/ORDER`, `/OPT:REF`, `/OPT:ICF`,
  section alignment, default libraries, subsystem version, and object order.

Score each candidate on code-only object diff. Persist the winning compiler
profile with the exact `cl.exe` banner, environment, include/lib paths, command
line, and objdiff evidence.

### 4. Keep C Plausible

Good matches should look like code a contemporary developer could have written.
Avoid source shapes that only exist to trick the compiler unless they are
quarantined as temporary experiments. Inline asm and byte emitters are probes,
not accepted recovered source.

Use decompiler output for behavior, not allocation. Ghidra pseudocode helps
identify loops, parameters, constants, and memory access. It does not preserve
register allocation, stack-slot assignment, statement order, or compiler quirks.

### 5. Use LLMs Where They Actually Help

Agents are best at proposing behavior-preserving C shapes and recognizing
patterns from matched examples. They are weak at exact compiler scheduling and
register allocation. Provide only the function-local target assembly, current
candidate assembly, type declarations, call graph, and a few matched examples
from the same compiler profile. Let tools provide diff output on demand.

Use permuters only after a candidate is already close. Low-percentage permuter
search tends to find unnatural local optima.

## Current Advanced Strategies

These are useful accelerators, not proof gates:

| Strategy | How To Use It | Why It Matters |
| --- | --- | --- |
| Matched-example retrieval | Index already matched C/asm pairs and retrieve examples with similar calls, control flow, and data access | Teaches the agent project idioms and compiler-specific source shapes |
| Ghidra BSim | Use decompiler-derived function feature vectors to find similar behavior across binaries or project revisions | Finds library code, cloned helpers, and neighbor functions even across compiler/architecture noise |
| Code/assembly embeddings | Embed function asm/decompiler text and search by vector similarity | Helps pick examples when call-graph heuristics are sparse |
| AST cleanup transforms | Automatically fix predictable AI errors such as mid-block C89 declarations or project offset comments | Keeps prompts smaller and prevents repetitive syntax churn |
| Diff-guided small steps | Ask the agent to explain one mismatch cluster, change one hypothesis, and rebuild | Prevents random rewrites from destroying a near-match |
| Compiler-profile corpus | Compile a fixed probe suite across toolchains/flags and compare code-only output | Converts compiler guessing into measured evidence |
| OEP/runtime capture | Break on `VirtualAlloc`, `VirtualProtect`, `WriteProcessMemory`, unpacker tail jumps, or original-entry transfer | Required when static `.text` is packed/encrypted or Ghidra cannot find real functions |

BSim and embeddings should feed the prompt builder and triage queue. They should
not replace objdiff, because similarity scores are intentionally tolerant of
compiler and source differences.

## MSVC Flags That Matter For x86 Matching

| Option | Source-Parity Impact |
| --- | --- |
| `/Od` | Disables optimization; common for debug/loader code and keeps stack-heavy idioms. |
| `/O1`, `/O2` | Size/speed release modes; current MSVC docs state both imply `/Oy` on x86. |
| `/Oy`, `/Oy-` | Omits or preserves EBP frame pointer; critical when target uses EBP locals. |
| `/GS`, `/GS-` | Adds or removes buffer-security cookie code in qualifying functions. |
| `/GsN` | Controls stack probing threshold and `_chkstk` insertion. |
| `/RTC1`, `/GZ` | Debug runtime checks; can inject extra initialization/checking code. |
| `/Gd`, `/Gz`, `/Gr` | Selects cdecl/stdcall/fastcall defaults; changes stack cleanup and decoration. |
| `/Gy`, `/Gy-` | Function-level linking/COMDAT; affects object layout and link granularity. |
| `/GF`, `/GF-` | String pooling; affects data layout and sometimes references from code. |
| `/GR`, `/GR-` | RTTI on/off; important for C++ class code and vtable metadata. |
| `/EH*`, `/GX` | Exception model; important for C++ functions with destructors/try/catch. |
| `/MD`, `/MT`, `/ML` | Runtime library model; mostly link/data behavior, but defines macros that can change inline CRT calls. |
| `/Z7`, `/Zi` | Debug info format; should not be a code match gate, but affects object metadata. |
| `/FA`, `/Fa` | Emit assembly listings; useful for compiler-profile corpus inspection. |

## Current `swkotor.exe` Findings

These are evidence, not completion claims:

- Target: PE32 x86, timestamp `Thu Feb 12 12:15:53 2004`, entry
  `0x0086d2ed`, `MajorLinkerVersion 7`, `MinorLinkerVersion 0`.
- Executable sections: `.text` at RVA `0x1000` size `0x33c000`, `.bind` at
  RVA `0x46d000` size `0x56000`.
- The original `.text` is SteamStub-packed and is not original compiler output.
- Steamless produces an unpacked analysis image with a real `.textV` code
  section. Ghidra inventory currently finds `8616` `.textV` functions and `5`
  `.bindV` loader functions in that unpacked image.
- Existing `swkotor_*` byte-slice prompts are not source recovery. They should
  remain acquisition fixtures or be clearly marked non-semantic.
- `prompts/swkotor_FUN_0086d000_real/candidate.c` is a real high-level C match
  for one five-byte loader stub only: code bytes `55 8b ec 5d c3`, objdiff
  code differences `0`.
- `FUN_0086d201` and `FUN_0086d266` are behavioral C candidates but not matches.
  Their target code uses old/debug idioms (`AND [ebp-X],0`, `INC/DEC`,
  `PUSH 0xa; POP ECX`, `LEAVE`) that current MSVC8 candidates do not reproduce.
- The verified high-level source-match set is still small relative to the
  executable: `574` `.textV` functions out of `8621` total function entries.
  Current lanes are `256` simple no-relocation functions plus `318`
  relocation-aware wrappers/thunks.
- The current compiler-profile sweep over two nontrivial loader-adjacent cases
  found no perfect match across the tested VC7.1/VC8 flag matrix. Treat that as
  negative evidence: the source shape, flags, or compiler family remain
  underconstrained for nontrivial functions.

## Unpacked Code Acquisition Evidence

Update: `swkotor.exe` is SteamStub-packed. Static `.text` in the original file
has near-random entropy and disassembles as nonsense. The `.bind` loader decodes
a SteamStub table beginning at `0x0086f204`; decoded strings include
`VirtualProtect`, `VirtualAlloc`, `SteamStart_SharedMemFile`, `steam.exe`, and
`Application load error X:XXXXXXXXXX`.

Steamless was acquired under `target/Steamless-src` / `target/steamless-release`
and used as an analysis unpacker:

```bash
mono target/steamless-release/extracted/Steamless.CLI.exe \
  --quiet --keepbind --dumppayload --dumpdrmp \
  target/swkotor-unpack/swkotor.original.exe
```

Result:

- Packed original:
  `target/swkotor-unpack/swkotor.original.exe`
- Unpacked analysis image:
  `target/swkotor-unpack/swkotor.original.exe.unpacked.exe`
- Steamless identified: `SteamStub Variant 2.1`
- Unpacked `.textV` SHA256:
  `39bcd9f2c21de0e24aef77bcc2912f313fe9ed57803f505819d19a59ee0d4135`
- `.textV` now starts with normal x86 code:
  `6a ff 68 a6 3e 71 00 64 a1 00 00 00 00 ...`

Ghidra inventory command:

```bash
/home/brunner56/.local/opt/ghidra/current/support/analyzeHeadless \
  target/ghidra-swkotor-unpacked-inventory-project swkotor_unpacked_inventory \
  -import target/swkotor-unpack/swkotor.original.exe.unpacked.exe \
  -scriptPath scripts/ghidra \
  -postScript ExportFunctionInventory.java \
  target/swkotor-unpack/facts/function-inventory.jsonl
```

Inventory result:

| Section | Functions | Body Bytes | Instructions |
| --- | ---: | ---: | ---: |
| `.textV` | `8616` | `2711359` | `809297` |
| `.bindV` | `5` | `8661` | `2039` |

This replaces the earlier six-function `.bind`-only view. Full source parity is
still not achieved, but there is now a real function inventory for the game code.

## First Real `.textV` Source Matches

`FUN_00401590` is a real unpacked `.textV` function:

```text
00401590: 33 c0        xor eax,eax
00401592: c2 10 00     ret 0x10
```

Target slice:

```bash
./scripts/swkotor-inventory-slice.py \
  --function FUN_00401590 \
  --symbol '_FUN_00401590@16' \
  --out-dir target/swkotor-match/FUN_00401590
```

High-level C candidate:

```c
int __stdcall FUN_00401590(int a, int b, int c, int d) {
    return 0;
}
```

Verifier:

```bash
VC_ROOT=/run/media/brunner56/MyBook/MizuchiSource/toolchains/msvc8.0-main \
WINEPREFIX=$PWD/target/toolchain-acquire/vctoolkit2003/wineprefix \
CL_OPT=/O2 \
  bash scripts/cl-compile.sh \
    target/swkotor-match/FUN_00401590/candidate.c \
    target/swkotor-match/FUN_00401590/candidate.obj \
    /GS- /Oy

./scripts/lib/verify-objdiff.sh \
  target/swkotor-match/FUN_00401590/target.obj \
  target/swkotor-match/FUN_00401590/candidate.obj \
  --out target/swkotor-match/FUN_00401590/verify.json
```

Result: `status=matched`, `differences=0`.

This proves the unpacked `.textV` acquisition path can feed the normal
high-level-C → MSVC → objdiff gate. It does not prove whole-app parity.

A simple-pattern lane now exists. It covers trivial returns, constant returns,
ECX-based field getters/setters, LEA accessors, OR-bit setters, and
address-literal global getters/setters. These are real high-level C candidates
compiled with MSVC and accepted only on objdiff zero:

```bash
./scripts/swkotor-match-trivial.py --limit 200
jq '{attempted,matched,mismatched,byKind}' \
  target/swkotor-trivial-matches/summary.json
```

Current result:

- Attempted: `256`
- Matched: `256`
- Mismatched: `0`
- Summary: `target/swkotor-trivial-matches/summary.jsonl`
- Rollup: `target/swkotor-trivial-matches/summary.json`
- Inventory summary: `target/swkotor-unpack/facts/inventory-summary.json`

Example field getter:

```c
unsigned int __fastcall FUN_00404dd0(void *self) {
    return *(unsigned int *)((char *)self + 0x30);
}
```

Target and candidate both disassemble to:

```text
00000000 <@FUN_00404dd0@4>:
   0: 8b 41 30    mov eax,DWORD PTR [ecx+0x30]
   3: c3          ret
```

A relocation-aware wrapper lane also exists for target code whose final linked
bytes contain direct `call` / `jmp` displacements. For those functions the
target object is reconstructed with symbolic relocations to the known Ghidra
function name, then compared against ordinary MSVC-generated C:

```bash
./scripts/swkotor-match-reloc-wrappers.py --limit 200
jq '{attempted,matched,mismatched,byKind}' \
  target/swkotor-reloc-wrapper-matches/summary.json
```

Current result:

- Attempted: `318`
- Matched: `318`
- Mismatched: `0`
- Summary: `target/swkotor-reloc-wrapper-matches/summary.jsonl`
- Rollup: `target/swkotor-reloc-wrapper-matches/summary.json`

Example wrapper:

```c
extern void __stdcall FUN_00406e20(int, int, int);

void __stdcall FUN_004087c0(int a0) {
    FUN_00406e20(a0, 4, 0);
}
```

Target and candidate both compare as:

```text
00000000 <_FUN_004087c0@4>:
   0: 8b 44 24 04        mov eax,DWORD PTR [esp+0x4]
   4: 6a 00              push 0x0
   6: 6a 04              push 0x4
   8: 50                 push eax
   9: e8 00 00 00 00     call _FUN_00406e20@12
   e: c2 04 00           ret 0x4
```

The verified candidates can be exported into a partial recovered-source shard.
The exporter writes a readable combined view plus a split source tree. Compile
the split tree because per-function extern prototypes are not globally
reconciled yet:

```bash
./scripts/swkotor-export-matched-source.py

VC_ROOT=/run/media/brunner56/MyBook/MizuchiSource/toolchains/msvc8.0-main \
WINEPREFIX=$PWD/target/toolchain-acquire/vctoolkit2003/wineprefix \
CL_OPT=/O2 \
  ./scripts/swkotor-compile-recovered-shard.py
```

Current shard:

- Source root: `target/swkotor-recovered/functions/`
- Combined readable view: `target/swkotor-recovered/simple_matches.c`
- Manifest: `target/swkotor-recovered/simple_matches.manifest.json`
- Coverage: `target/swkotor-recovered/coverage.json`
- Compile summary: `target/swkotor-recovered/compile-summary.json`
- Function count: `574`
- Compile proof: `574` source files compiled with MSVC8 `/O2 /GS- /Oy`, `0`
  compile failures.
- Scope: partial verified source only; not linkable whole-exe source.

Matched real `.textV` functions so far:

- `FUN_00401430`
- `FUN_00401590`
- `FUN_004015a0`
- `FUN_00406c60`
- `FUN_00605370`
- `FUN_006054e0`
- `FUN_0063e7a0`
- `FUN_0063e7c0`
- `FUN_0063e7f0`
- `FUN_0066b360`
- `FUN_00706a22`

## Compiler-Profile Matrix Evidence

Local profiler:

```bash
./scripts/swkotor-compiler-profile.sh --case FUN_0086d201 --case FUN_0086d266
```

Outputs:

- `target/swkotor-compiler-profile/summary.jsonl`
- `target/swkotor-compiler-profile/summary.tsv`
- `target/swkotor-compiler-profile/runs/<function>/<compiler>/<flags>/`

Observed toolchains:

| Profile | Compiler Banner |
| --- | --- |
| `vc71` | `Microsoft (R) 32-bit C/C++ Optimizing Compiler Version 13.10.3052 for 80x86` |
| `vc80` | `Microsoft (R) 32-bit C/C++ Optimizing Compiler Version 14.00.50727.42 for 80x86` |

VC7.1 acquisition:

- Source installer: `target/toolchain-acquire/vctoolkit2003/VCToolkitSetup.exe`
- SHA256: `03aad135c22e953e0928b118705338afdbd08abf8e4039038ef77945504e65fa`
- Extracted root:
  `target/toolchain-acquire/vctoolkit2003/msitools-extract/Program Files/Microsoft Visual C++ Toolkit 2003/`

Matrix result on the two current non-trivial candidates:

| Function | Best Flags Seen | Best Score | Status |
| --- | --- | ---: | --- |
| `FUN_0086d201` | `/Od /Oy- /GS-` family | `56.216217%` | mismatched |
| `FUN_0086d266` | `/Od /Oy- /GS-` family | `89.88095%` | mismatched |

Interpretation:

- VC7.1 now runs locally and produces objects, so missing VC7.1 was a real
  tooling gap and is now closed for compiler-profile experiments.
- VC7.1 alone does not make the current behavioral C match. VC7.1 and VC8
  produce equivalent scores for these current source shapes under the tested
  debug-style flags.
- `/GZ` and `/RTC1` reduce match quality on these candidates, so the observed
  old-looking code is not simply Visual C++ runtime-check instrumentation.
- `/O1` and `/O2` are poor fits for these two slices. The target shape is much
  closer to unoptimized frame-pointer code than release optimized code.
- The next blocker is source-shape parity: statement order, temporary variables,
  local initialization, calling convention, and expression decomposition must be
  iterated against objdiff. Compiler version/flag guessing is no longer enough
  for these functions.
- AgentDecompile local mode was validated against the Steamless-unpacked PE:
  import/open succeeds in `target/agentdecompile-swkotor-project`, but this
  checkout's `list-functions` / `get-functions` provider path currently fails
  with `Unknown tool: list_functions` / `get_functions`, and direct
  `get-function FUN_00401060` does not resolve the headless-inventory symbol.
  Use AgentDecompile for project import/open proof for now; keep the headless
  Ghidra inventory as the authoritative function map until function labels and
  provider routing are synchronized.

## Concrete Next Experiments

1. Promote the unpacked inventory into a queue keyed by `.textV` address,
   target bytes, size, and candidate status. Current queue:
   `target/swkotor-recovery-queue/queue.jsonl`.
2. Generate target objects with `scripts/swkotor-inventory-slice.py` for small
   real `.textV` functions, starting with returns/thunks/accessors and then
   expanding to call wrappers.
3. For each target object, try high-level C only; byte emitters and inline asm
   stay outside the semantic source count.
4. Use objdiff `0` as the only per-function acceptance gate. Partial percentages
   are triage evidence only.
5. Once enough `.textV` examples are matched, infer project-wide source style,
   calling conventions, class idioms, and compiler flags from those examples.
6. Full-app proof remains a later aggregate: every executable byte range must
   map to accepted C/C++ source and the final rebuilt executable must match code
   sections only.

## Proper Methodology For Source-Code Parity

The correct loop is deliberately narrow:

1. Acquire the real executed code bytes and prove function boundaries.
2. Create a target object/slice with only the bytes that should be matched.
3. Compile high-level C/C++ with a candidate historical compiler profile.
4. Compare object code with objdiff; ignore data/resource/debug noise only when
   the proof target explicitly says so.
5. Change one source-shape or compiler-profile hypothesis, rebuild, and keep the
   change only if the diff improves.
6. Commit a function only at objdiff code `0`; partial percentages are evidence,
   not success.

This is why whole-binary byte emitters are not acceptable recovered source:
they can reproduce bytes without explaining the program, and they provide no
compiler-profile evidence for future functions.

## Sources

- Chris Lewis, "The Unexpected Effectiveness of One-Shot Decompilation with Claude":
  https://blog.chrislewis.au/the-unexpected-effectiveness-of-one-shot-decompilation-with-claude/
- Chris Lewis, "Using Coding Agents to Decompile Nintendo 64 Games":
  https://blog.chrislewis.au/using-coding-agents-to-decompile-nintendo-64-games/
- Bruno Macabeus, "Can LLMs Really Do Matching Decompilation? I Tested 60 Functions to Find Out":
  https://macabeus.medium.com/can-llms-really-do-matching-decompilation-i-tested-60-functions-to-find-out-4e39b0ae4288
- Bruno Macabeus, "Building a VS Code Extension to Automate Matching Decompilation":
  https://macabeus.medium.com/development-journey-on-game-decompilation-using-ai-part-3-a0a322e0d274
- Microsoft C++ compiler options:
  https://learn.microsoft.com/en-us/cpp/build/reference/compiler-options-listed-alphabetically
- Microsoft `/O1`, `/O2`, and x86 `/Oy` behavior:
  https://learn.microsoft.com/en-us/cpp/build/reference/o1-o2-minimize-size-maximize-speed
- Microsoft `/GS` buffer security check:
  https://learn.microsoft.com/en-us/cpp/build/reference/gs-buffer-security-check
- objdiff:
  https://github.com/encounter/objdiff
- Microsoft PE/COFF format:
  https://learn.microsoft.com/en-us/windows/win32/debug/pe-format
- Ghidra BSim tutorial:
  https://ghidra.re/ghidra_docs/GhidraClass/BSim/BSimTutorial_Intro.html
- Quarkslab, "BSIM explained once and for all!":
  https://blog.quarkslab.com/bsim-explained-once-and-for-all.html
