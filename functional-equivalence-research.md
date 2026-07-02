# Behavioral Decompilation: Research Report

## 1. State of the Art

### Terminology

There is no single established name for this technique. The relevant terms, by community:

- **Translation validation** ([Pnueli, Siegel & Singerman, 1998](https://link.springer.com/chapter/10.1007/BFb0054170)) — the oldest formal term. Originally for verifying compiler output preserves source semantics. Used in the formal methods / PL community.
- **Behavioral equivalence** — used by [LLM4Decompile (2024)](https://arxiv.org/abs/2403.05286) and the ML/decompilation community. Defined as: decompiled code and original code produce the same outputs given the same inputs.
- **Semantic preservation** — used in the verified compiler community ([CompCert](https://compcert.org/); see [Leroy, "Formal verification of a realistic compiler", CACM 2009](https://xavierleroy.org/publi/compcert-CACM.pdf)). Formal proof that transformations preserve observable program behavior.
- **Observational equivalence** — from programming language theory ([Morris, 1968](https://dspace.mit.edu/handle/1721.1/64850); see also [Wikipedia overview](https://en.wikipedia.org/wiki/Observational_equivalence)). Black-box testing treating programs as systems with observable actions.
- **Re-executability** — practical metric coined by [LLM4Decompile](https://arxiv.org/abs/2403.05286): whether decompiled code passes the original program's test assertions.

We adopt the term **"behavioral decompilation"** — concise, immediately understood ("does it behave the same?"), and a clean parallel with the existing "matching decompilation" (bytes match) vs "behavioral decompilation" (behavior matches). The formal methods community would call it "translation validation applied in the reverse direction" (validating decompilation rather than compilation). The general problem of program equivalence checking is undecidable (Gupta et al., IIT Delhi, 2018), so we are necessarily doing testing (probabilistic), not proving (sound).

### Existing tools for decompilation verification

**All major decomp projects rely exclusively on byte-for-byte matching.** I examined the CI pipelines of pokeemerald, Zelda: Ocarina of Time, Paper Mario, and Super Mario 64. Every one uses assembly/ROM hash comparison only. The `NON_MATCHING` flag pattern (seen in OoT, pokeemerald) acknowledges functions that don't byte-match but are believed to be functionally equivalent — these are verified only by human review. No major decomp project uses emulator-based testing, unit tests, property-based testing, or any form of semantic equivalence checking. This is a significant gap.

The existing tooling ecosystem is entirely syntactic:

| Tool                                                | What it does                                               | Semantic?                    |
| --------------------------------------------------- | ---------------------------------------------------------- | ---------------------------- |
| **objdiff** (encounter/objdiff)                     | Compares compiled object files at instruction/symbol level | No                           |
| **asm-differ** (simonlindholm/asm-differ)           | Visual assembly diff (decomp.me default)                   | No                           |
| **decomp-permuter** (simonlindholm/decomp-permuter) | Randomly permutes C source to find closer assembly matches | No                           |
| **decomp.me**                                       | Collaborative decompilation website                        | No (uses objdiff/asm-differ) |

### Emulator-based / concrete execution approaches

No existing tool provides out-of-the-box behavioral decompilation for decompiled code on any architecture. However, the building blocks exist:

**Unicorn Engine** (unicorn-engine/unicorn) — CPU emulator framework based on QEMU. Supports ARM + Thumb mode (`UC_MODE_THUMB`). Provides: `uc_open`, `uc_reg_write/read`, `uc_mem_map`, `uc_mem_write/read`, `uc_emu_start`. Instruction and memory hooks. 8,800+ stars, actively maintained. The closest thing to what we need, but it's a CPU emulator — no built-in behavioral decompilation. GPL-2.0.

**angr** (angr.io) — binary analysis platform with symbolic/concolic execution. Supports ARM including Thumb mode (confirmed: address bit 0 encodes Thumb-ness). Lifts binaries to VEX IR, performs symbolic execution with Z3. Could potentially symbolically execute both original and recompiled ARM functions and compare symbolic outputs. But suffers from state explosion and has no built-in behavioral decompilation mode.

### Formal verification approaches

**Alive2** (AliveToolkit/alive2) — translation validation for LLVM IR optimizations using Z3 SMT solver. Gold standard for proving optimization correctness, but works at LLVM IR level, not binary.

**STOKE** (StanfordPL/stoke) — stochastic superoptimizer for x86-64 with multi-tier verification: test-case based → bounded verification → DDEC → full formal verification via Z3/CVC4. The **multi-tier pattern** is the most transferable design insight. x86-64 only.

**Gupta et al. "Effective Use of SMT Solvers for Program Equivalence Checking"** (IIT Delhi, 2018) — builds simulation relations between C source and x86 assembly locations, verifies with Z3. Reports 72-76% success rate on real-world functions. The theory is architecture-agnostic but would need ARM instruction semantics formalized for Z3.

**Validating Binary Decompilation** (sdasgup3/validating-binary-decompilation) — validates McSema's binary lifting (x86-64 to LLVM IR) using single-instruction formal validation + program-level graph isomorphism. Found 29 bugs in McSema. Deeply x86-64-specific.

**CMU SEI Decompilation Assurance** — uses SeaHorn to verify that Ghidra-decompiled functions preserve the sequence of side-effect operations. ~52% recompilability rate. The methodology (verify side-effect sequence equivalence) is directly relevant; the tooling is less so.

### The Crimsonland project

Source: https://banteg.xyz/posts/crimsonland/

A complete behavioral-fidelity rewrite of the game Crimsonland (x86 Windows). Verification techniques:

1. **Runtime capture to test fixtures** — convert runtime observations into deterministic test fixtures. Framebuffer generation from identical seeds must be byte-for-byte identical.
2. **Function hooking / call tracing** — Frida for JavaScript injection, hooking function calls, capturing arguments/return values, tracing execution paths, capturing framebuffers.
3. **Interactive debugger validation** — WinDbg breakpoints to examine memory states.
4. **Structural comparison** — continuously-updated function mapping document.

**Key takeaway for Mizuchi**: The "runtime capture into test fixtures" pattern is the most transferable technique. For GBA functions: run the original ROM in an emulator to capture function inputs/outputs, then replay those inputs against the recompiled function and compare.

### The RL-decompiler project

Source: https://hytopoulos.github.io/rl-decompiler/

An RL-based decompiler using Qwen2.5-Coder-7B for x86-64. The reward function IS an equivalence metric — normalized Levenshtein distance between target assembly and assembly from recompiling the model's output. This is syntactic comparison (same as objdiff), not semantic. 97% compilation success, +19.6% over GPT-4.1 baseline. Not directly relevant to behavioral decompilation.

### Summary: landscape position

| Approach                      | Soundness                                      | Effort       | GBA-applicable?       |
| ----------------------------- | ---------------------------------------------- | ------------ | --------------------- |
| Byte-matching (objdiff)       | Complete for matching, misses equivalent code  | Already done | Yes (current)         |
| Concrete execution testing    | Probabilistic (coverage-dependent)             | Medium       | **Yes — our target**  |
| Symbolic execution (angr)     | Can prove for all inputs, state explosion risk | Medium-High  | Possible, future tier |
| SMT-based formal verification | Sound but undecidable in general               | Very High    | Theoretical only      |

We are building the first tool in the decomp ecosystem to do behavioral decompilation. Nothing like this exists today.

---

## 2. GBA Emulator Selection

### Evaluation

| Criteria                | mGBA                                                  | NanoBoyAdvance                        | armv4t_emu (Rust)                                                                | gbajs                                                                                                                                       | Unicorn Engine                                                           |
| ----------------------- | ----------------------------------------------------- | ------------------------------------- | -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| **API accessibility**   | Rich C API (`mCore`), Lua scripting.                  | Tightly coupled to `Bus`/`Scheduler`. | `Cpu::new()`, `reg_set/get`, `step(&mut mem)`. Custom memory via `Memory` trait. | Direct property access: `gprs[i]`, `mmu.load32/store32`, `step()`. `freeze()`/`defrost()` for state serialization.                          | `uc_open`, `uc_reg_write/read`, `uc_mem_map/write/read`, `uc_emu_start`. |
| **Cycle accuracy**      | No (approximate)                                      | Yes (primary feature)                 | No (functional only)                                                             | Yes (GBA-specific wait states baked into every instruction)                                                                                 | No (QEMU-based)                                                          |
| **MMIO emulation**      | Full GBA hardware                                     | Full GBA hardware                     | None (you implement `Memory` trait)                                              | Full GBA hardware (tightly coupled `GameBoyAdvanceIO` class)                                                                                | Can hook memory regions                                                  |
| **Node.js integration** | No bindings. WASM fork exposes game-player APIs only. | C++ only. No bindings.                | **Pure Rust → WASM via `wasm-pack`**. Only dep is `log`.                         | **Pure JS (ES5)**. Zero build step. Runs natively in Node.js.                                                                               | No official Node.js bindings. `unicorn.js` partially maintained (2017).  |
| **Isolation**           | Requires loading a ROM.                               | CPU requires `Bus`/`Scheduler` deps.  | **Designed for this.** CPU + whatever memory you provide.                        | Requires full GBA system. CPU `step()` calls `irq.updateTimers()` on every instruction. Would need significant surgery to extract CPU-only. | Designed for it.                                                         |
| **Performance**         | Full GBA system overhead                              | Cycle accuracy overhead               | **Lightweight CPU-only. No overhead.**                                           | Closure-based instruction JIT (compile once, cache). Fast but carries unnecessary GBA overhead.                                             | JIT warmup overhead per code block.                                      |
| **Maturity**            | 6,865 stars, MPL-2.0                                  | 1,241 stars, GPL-3.0                  | 35 stars, MIT, last commit Jul 2025                                              | 1,080 stars, BSD-2-Clause, **archived 2017**                                                                                                | 8,812 stars, GPL-2.0                                                     |

### Deep audit: `daniel5151/armv4t_emu`

**Repository**: ~1,775 lines of Rust across 9 files. Originally extracted from `iburinoc/gba-rs`. MIT license. ~14,200 crate downloads.

#### ARM instruction coverage (all present)

| Category                                                                          | Status              | Notes                                                                                                                             |
| --------------------------------------------------------------------------------- | ------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Data Processing (AND/EOR/SUB/RSB/ADD/ADC/SBC/RSC/TST/TEQ/CMP/CMN/ORR/MOV/BIC/MVN) | **Complete**        | All 16 opcodes, all shift types (LSL/LSR/ASR/ROR/RRX), S-bit logic correct                                                        |
| Multiply (MUL, MLA)                                                               | **Complete**        | Flag updates match ARM7TDMI spec                                                                                                  |
| Multiply Long (UMULL, UMLAL, SMULL, SMLAL)                                        | **Complete**        | 64-bit results correctly split into RdHi/RdLo                                                                                     |
| Single Data Transfer (LDR, STR)                                                   | **Complete**        | Immediate + register offset, pre/post-indexed, writeback, byte/word                                                               |
| Halfword/Signed Transfer (LDRH, STRH, LDRSB, LDRSH)                               | **Complete**        | Known decoder edge-case bug (issue #10) on malformed instructions — not triggered by compiler output                              |
| Block Data Transfer (LDM, STM)                                                    | **Mostly complete** | S-bit "force User bank" path has a FIXME for writeback behavior. Common cases (no S-bit, or S-bit with PC in list) work correctly |
| Branch (B, BL)                                                                    | **Complete**        | 24-bit signed offset, pipeline-aware                                                                                              |
| Branch and Exchange (BX)                                                          | **Complete**        | T-bit mode switch                                                                                                                 |
| Software Interrupt (SWI)                                                          | **Complete**        | Correctly enters Supervisor mode, saves CPSR to SPSR_svc                                                                          |
| MRS/MSR                                                                           | **Complete**        | Field masks (f, c), User-mode restrictions                                                                                        |
| Single Data Swap (SWP, SWPB)                                                      | **Complete**        | Byte/word variants                                                                                                                |
| Coprocessor (CDP, LDC, STC, MCR, MRC)                                             | **Stub only**       | MCR/MRC logged, CDP/LDC/STC decode as Undefined. Irrelevant for ARM7TDMI/GBA                                                      |

#### Thumb instruction coverage (all present)

All 20 Thumb instruction classes are implemented and complete: Shifted, AddSub, ImmOp, AluOp (all 16 operations including MUL), HiRegBx, PcLoad, SingleXferR, HwSgnXfer, SingleXferI, HwXferI, SpXfer, LoadAddr, SpAdd, PushPop, BlockXfer, CondBranch, SoftwareInt, Branch, LongBranch (two-instruction BL sequence).

All 15 condition codes (EQ, NE, CS, CC, MI, PL, VS, VC, HI, LS, GE, LT, GT, LE, AL) are correctly evaluated.

#### Known issues

| Issue                                    | Severity for us                                                            | Details                                                                                        |
| ---------------------------------------- | -------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| LDM/STM S-bit user bank transfer (FIXME) | **Low** — compiler-emitted code doesn't use privileged LDM/STM variants    | The S=1 without R15-in-list path (forcing User-mode register access) may have writeback issues |
| CPSR panic on invalid mode bits (#9)     | **Low** — only triggers on malformed code                                  | `mode()` calls `expect()` which panics if mode bits are invalid                                |
| HwSgnXfer decoder overlap (#10)          | **Low** — only triggers on malformed instructions                          | `unreachable!()` panic on specific malformed encoding                                          |
| Unaligned access stubs (#5)              | **Medium** — GBA hardware has defined unaligned behavior                   | `alignment.rs` has TODO stubs for r8/r16/w8/w16 alignment                                      |
| Infallible Memory trait (#7)             | **Low** — we control the memory map                                        | No Data/Prefetch Abort support; fine since we map all regions the function accesses            |
| No `no_std` (#6)                         | **None** — `std` for wasm32 includes everything used (traits, collections) | Uses `fmt::Debug`, `Index/IndexMut`, `BTreeMap` (in example only)                              |

#### WASM compatibility assessment

The crate does not use `no_std`, but its `std` usage is limited to formatting traits (`fmt::Debug`), `Index/IndexMut`, and `BTreeMap` (only in `ExampleMem`, not in the core). All of these are available on `wasm32-unknown-unknown`. The only dependency is `log` (WASM-compatible). The optional `capstone` feature (C library wrapper) is NOT WASM-compatible, but is behind a feature flag we wouldn't enable.

**Verdict: should compile to WASM, but nobody has explicitly confirmed it.** This must be validated as the first implementation step.

#### Test coverage

9 ARM + 9 Thumb integration tests using hand-written assembly binaries. Covers happy paths for basic data processing, conditional execution, multiplication, load/store, block transfer, BL, SWP, and sign-extension. **Does not test**: RSB, RSC, ADC, TEQ, CMN, BIC, MVN (ARM), MLA, UMLAL, SMULL, SMLAL, ASR/ROR as data processing operands, all block transfer modes, SWI handler behavior, mode switching via MSR.

The `SingleStepTests/ARM7TDMI` repository (20,000+ test vectors from NanoBoyAdvance) can validate correctness more comprehensively before we rely on it.

### Deep audit: `endrift/gbajs` (vendoring candidate)

**Repository**: ~11,045 lines of pure JavaScript (ES5). Written by Jeffrey Pfau — also the author of mGBA. BSD-2-Clause. 1,080 stars, 444 forks. **Archived since January 2017.**

#### Architecture

gbajs uses a **compile-then-cache** instruction model: each instruction is decoded into a JavaScript closure on first encounter, then cached per memory page. This is a form of JIT that V8 can optimize well.

```javascript
// Example: ADD instruction (arm.js)
ARMCoreArm.prototype.constructADD = function (rd, rn, shiftOp, condOp) {
  var cpu = this.cpu;
  var gprs = cpu.gprs;
  return function () {
    cpu.mmu.waitPrefetch32(gprs[cpu.PC]);
    if (condOp && !condOp()) {
      return;
    }
    shiftOp();
    gprs[rd] = (gprs[rn] >>> 0) + (cpu.shifterOperand >>> 0);
  };
};
```

Registers are `Int32Array(16)`. CPSR flags are stored as individual booleans (`cpsrN`, `cpsrZ`, `cpsrC`, `cpsrV`) — good for performance, avoids bit-masking per flag check. Has complete banked register support (FIQ r8-r12, SP/LR for all modes).

#### Instruction completeness

**Full ARM7TDMI coverage**, including all instructions in our benchmark. Specific highlights:

- All 16 data processing ops with S/non-S variants
- All multiply variants including UMULL/UMULLS/UMLAL/UMLALS/SMULL/SMULLS/SMLAL/SMLALS (8 total)
- SWP/SWPB present
- All Thumb-1 instructions present
- Full SWI BIOS emulation (Div, Sqrt, ArcTan2, CpuSet, etc.) — a feature armv4t_emu lacks

#### Known issues

- **`switchMode` bug**: `if (newMode != this.MODE_USER || newMode != this.MODE_SYSTEM)` — this condition is always true (should be `&&`). Causes banked register swaps even when switching to User/System mode.
- **64-bit multiply precision**: uses `SHIFT_32 = 1/0x100000000` floating-point trick for high words, which may have precision edge cases.
- **No test suite whatsoever.**

#### What vendoring would require

The CPU core is `core.js` (1,477 lines) + `arm.js` (1,569 lines) + `thumb.js` (854 lines) = **3,900 lines**. To extract it:

1. Strip ~7,000 lines (video, audio, I/O, DMA, timers, savedata, GPIO)
2. Remove `irq.updateTimers()` from `step()` — called on every single instruction, touches video/audio/timers
3. Replace the GBA MMU (~814 lines) with a simplified flat memory (~100 lines)
4. Remove `Object.prototype.inherit` global prototype pollution
5. Fix the `switchMode` `||`/`&&` bug
6. Convert from ES5 globals to proper modules (ES modules or CommonJS)
7. Write tests from scratch (there are none)
8. Stub or remove wait state calculations baked into every instruction closure

**Estimated vendored size after trimming**: ~3,500-4,000 lines of JS.

### Option C: Custom TypeScript emulator (written from scratch)

A third option: write our own ARM7TDMI Thumb emulator in TypeScript, purpose-built for behavioral decompilation.

#### Feasibility

No native-TypeScript ARM7TDMI emulator exists today. The closest are [wthumb](https://github.com/FreddyJS/wthumb) (partial Thumb, educational) and [thumbulator.ts](https://www.npmjs.com/package/thumbulator.ts) (Emscripten-transpiled C, not readable TS). However, the ARM7TDMI Thumb instruction set is well-documented and small enough to implement from scratch with confidence.

The benchmark uses **35 Thumb mnemonics spanning 15 of 19 Thumb formats**. Most are trivial to implement:

| Complexity | Count | Instructions                                                                                                                                                                                                                     |
| ---------- | ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Trivial    | 22    | `lsls/lsrs/asrs` (imm), `adds/subs` (reg/imm3/imm8), `movs`, `cmp` (imm), `ldr` (PC-rel), `ldr/str/ldrb/strb` (reg/imm offset), `ldrh/strh` (imm offset), `ldr/str` (SP-rel), `sub` (SP), `b`, `beq/bne/bge/bgt/bhi/ble/bls/blt` |
| Moderate   | 11    | `ands/orrs/muls/rsbs`, `lsls/lsrs/asrs` (reg), `cmp` (reg), `add/mov/bx` (hi-reg), `ldrh/ldrsb/ldrsh/strh` (sign-ext), `push`, `pop`                                                                                             |
| Complex    | 2     | `bl` (two-part: first half sets LR, second half branches to LR+offset)                                                                                                                                                           |

Only BL requires special care: it's encoded as two consecutive 16-bit instructions. The first half writes an intermediate value to LR, the second half reads LR and performs the branch. They execute as separate `step()` calls, so no special "combine" logic is needed.

#### Size estimates

| Scope                                         | Estimated lines |
| --------------------------------------------- | --------------- |
| 35 Thumb instructions only (benchmark subset) | **600-800**     |
| Full Thumb instruction set (19 formats)       | **900-1,100**   |
| Full ARM + Thumb (equivalent to armv4t_emu)   | **3,000-3,500** |

Breakdown for the benchmark subset: CPU state + memory interface (~80), bit manipulation utilities (~100), instruction decode (~60), instruction execute (~350-450).

Reference: armv4t_emu's `thumb.rs` is 508 lines of Rust execution logic, `util.rs` is 260 lines of bit/flag utilities. TypeScript is moderately more verbose (~1.5-1.7x), but we'd skip the 4 unused formats.

#### TypeScript numeric considerations

JavaScript bitwise operators return signed 32-bit integers. The key patterns for a CPU emulator:

| Need                   | Pattern                               | Example                                                      |
| ---------------------- | ------------------------------------- | ------------------------------------------------------------ |
| Unsigned 32-bit        | `>>> 0`                               | `(addr + offset) >>> 0`                                      |
| Signed truncation      | `\| 0`                                | `(a + b) \| 0`                                               |
| Multiplication         | `Math.imul(a, b)`                     | Required for `muls` — regular `*` loses precision above 2^53 |
| Arithmetic right shift | `>>`                                  | Sign-extending (for ASR)                                     |
| Logical right shift    | `>>>`                                 | Zero-extending (for LSR)                                     |
| Sign extension         | `(val << (32 - bits)) >> (32 - bits)` | Uses arithmetic `>>`                                         |
| Carry detection (add)  | `(a >>> 0) + (b >>> 0) > 0xFFFFFFFF`  | Before truncation                                            |
| Little-endian memory   | `DataView.getUint32(addr, true)`      | V8-optimized since 2018                                      |

These are well-understood patterns. `DataView` is now near-TypedArray performance in V8 and provides explicit endianness control.

#### CPSR flag update rules

| Operation type                             | N             | Z         | C                                   | V               |
| ------------------------------------------ | ------------- | --------- | ----------------------------------- | --------------- |
| Arithmetic (`adds`, `subs`, `rsbs`, `cmp`) | bit31(result) | result==0 | unsigned overflow/borrow            | signed overflow |
| Logical (`ands`, `orrs`, `movs`)           | bit31(result) | result==0 | shift carry-out                     | unchanged       |
| Multiply (`muls`)                          | bit31(result) | result==0 | destroyed (unpredictable on ARMv4T) | unchanged       |
| Shifts (`lsls`, `lsrs`, `asrs` by imm/reg) | bit31(result) | result==0 | last bit shifted out                | unchanged       |
| Hi-reg ops (`add`, `mov` in Format 5)      | unchanged     | unchanged | unchanged                           | unchanged       |

#### Validation: SingleStepTests/ARM7TDMI

The [SingleStepTests/ARM7TDMI](https://github.com/SingleStepTests/ARM7TDMI) repository provides **1,250,000 Thumb-mode test vectors** across 25 files (50,000 tests per instruction class). Tests are stored in a custom binary format (`.json.bin`), convertible to JSON via a provided Python script.

Each test vector contains:

- **`initial`**: full CPU state (R0-R15, banked registers, CPSR, 5 SPSRs, pipeline)
- **`final`**: expected state after executing one instruction
- **`opcode`**: the instruction word
- **`base_addr`**: memory address of the instruction
- **`transactions`**: memory bus operations `[{kind, size, addr, data}]`

The binary format is simple (little-endian uint32 values) and can be read directly from TypeScript with `DataView` — no Python transcoding needed. Total download: ~500 MB for Thumb files.

Known issue: `thumb_undefined_bcc.json` tests are incorrect (upstream NanoBoyAdvance bug). All other test files are reliable.

This gives us extremely thorough validation — 50,000 test vectors per instruction class, far more than armv4t_emu's 9 Thumb test programs.

#### Key references for implementation

- [GBATEK Thumb Instruction Summary](http://problemkaputt.de/gbatek-thumb-instruction-summary.htm) — the single best encoding reference (all 19 formats with bit layouts and flag effects)
- [ARM7TDMI Technical Reference Manual (DDI0029G)](https://ww1.microchip.com/downloads/en/DeviceDoc/DDI0029G_7TDMI_R3_trm.pdf) — official spec
- [armv4t_emu `thumb.rs`](https://github.com/daniel5151/armv4t_emu) — cleanest implementation reference (508 lines)
- [gbajs `thumb.js`](https://github.com/endrift/gbajs) — JS-idiomatic reference (854 lines)
- [armv4t.dart](https://github.com/matanlurey/armv4t.dart) — Dart (syntactically close to TypeScript), well-documented
- [Decoding the ARM7TDMI Instruction Set (blog)](https://www.gregorygaines.com/blog/decoding-the-arm7tdmi-instruction-set-game-boy-advance/) — tutorial-style walkthrough

### Recommendation

**Write a custom TypeScript emulator** as the primary approach. Justification:

1. **Zero build friction.** No Rust toolchain, no `wasm-pack`, no WASM loading. Pure TypeScript, tested with Vitest, integrated into the existing codebase like any other module. This eliminates the highest-risk unknown from the armv4t_emu approach.

2. **Right-sized.** The 35-instruction subset is ~600-800 lines. Even full Thumb is ~900-1,100. This is smaller than armv4t_emu (2,200 lines) and much smaller than vendored gbajs (3,500-4,000 lines). We write exactly what we need.

3. **Fully understood code.** We own every line. No debugging into WASM blobs or untested 2017 JavaScript. When a test vector fails, we fix it directly in TypeScript with full IDE support.

4. **Superior test coverage.** SingleStepTests gives us 1,250,000 Thumb test vectors — orders of magnitude more than armv4t_emu's 9 test programs. We can validate every instruction class to exhaustive confidence before using the emulator for behavioral decompilation.

5. **Incremental implementation.** Start with the 35 instructions the benchmark needs. Add ARM mode or additional Thumb instructions later only if future datasets require them. The architecture doesn't change.

6. **Native debuggability.** Set breakpoints in the CPU loop, step through instruction execution, inspect registers — all in VS Code with standard TypeScript tooling. Invaluable when diagnosing equivalence failures.

**armv4t_emu and gbajs remain useful as implementation references**, not dependencies.

| Dimension               | Custom TypeScript                                       | armv4t_emu (WASM)                | gbajs (vendored JS)             |
| ----------------------- | ------------------------------------------------------- | -------------------------------- | ------------------------------- |
| Build step              | None                                                    | Needs wasm-pack + Rust toolchain | None                            |
| Lines to write/maintain | **600-800** (benchmark subset)                          | ~2,200 (external dep)            | ~3,500-4,000 (surgery from 11K) |
| Extraction effort       | None (built for our use case)                           | None (library by design)         | Significant                     |
| Test coverage           | **1.25M vectors** (SingleStepTests)                     | 9 test programs                  | Must write from scratch         |
| Debuggability           | **Full** (TypeScript, VS Code)                          | WASM (opaque)                    | JS (readable but no types)      |
| Maintained by           | Us                                                      | External (35 stars)              | Abandoned (2017)                |
| Risk                    | We must implement correctly (mitigated by test vectors) | WASM build may fail              | Legacy bugs, no tests           |
| License                 | N/A (our code)                                          | MIT                              | BSD-2-Clause                    |

---

## 3. Test Harness Design

### a) Function isolation

**Approach: load function bytes into emulated memory and jump to it.**

Given two object files (target `.o` and compiled `.o`), we:

1. **Extract the function's machine code** from each object file. We already have the function name (from `settings.yaml`). Use the same ELF parsing that objdiff-wasm provides (or a standalone ELF parser like `elftools` via Rust/WASM) to extract the `.text` section bytes for the named symbol.

2. **Set up the emulated memory map:**

   ```
   0x02000000 - 0x0203FFFF  EWRAM (256KB) — general-purpose RAM
   0x03000000 - 0x03007FFF  IWRAM (32KB) — fast RAM, stack lives here
   0x04000000 - 0x040003FE  MMIO (1KB) — trapped, writes recorded
   0x05000000 - 0x050003FF  Palette RAM (1KB)
   0x06000000 - 0x06017FFF  VRAM (96KB)
   0x07000000 - 0x070003FF  OAM (1KB)
   0x08000000 - 0x09FFFFFF  ROM (up to 32MB)
   ```

3. **Load the function code** at a known address in ROM space (e.g., `0x08100000`). Load any referenced data sections too.

4. **Place a sentinel return address** on the stack. When the function executes `bx lr` or `pop {pc}`, it returns to a known address where we place a trap instruction (e.g., `SWI` or an unmapped address). We detect this via a hook or exception handler.

5. **Set initial state** (registers, memory contents), execute, read final state.

**Handling function calls (callees):**

Three strategies, configurable per function:

- **Stub mode** (default): Replace `bl` targets with immediate returns (`bx lr`). The callee returns 0 in r0 and has no side effects. Fast, tests the function's own logic in isolation. Suitable when we're comparing both versions and they both call the same stubs.
- **Include mode**: Load callee code from the target object/ROM alongside the function under test. Both versions call the real callees. More realistic but increases scope.
- **Mock mode**: Replace `bl` targets with configurable mock functions that return specified values. Useful when a callee's behavior is known and we want to control it.

**Critical insight:** For equivalence checking, **we don't need perfect callee behavior**. Both the original and recompiled function will call the same stubs/callees. As long as both versions see the same callee behavior, any divergence must be in the function itself. Stub mode is therefore sufficient and the simplest approach.

**Handling global state:**

Functions that access hardcoded addresses (e.g., `gCurTask` at some IWRAM address, `gCamera`) need those memory locations populated. Two approaches:

- **Zero-initialized** (default): All RAM starts at zero. Simple, deterministic, catches many divergences.
- **Random-initialized**: Memory regions the function accesses are filled with random data (seeded). Better coverage for functions that branch based on memory contents.
- **Captured state**: Load memory snapshots from a real ROM execution. Most realistic but requires a capture infrastructure (future enhancement).

### b) Input generation

**Determining what to vary:**

The key challenge is knowing which registers and memory locations a function actually reads, so we can generate meaningful inputs rather than blindly randomizing everything.

**Static analysis approach (recommended for v1):**

1. **Register inputs**: Follow the ARM calling convention (AAPCS). Functions receive arguments in `r0`-`r3` and on the stack. The function's signature (if known from the prompt or deduced from register usage in the first few instructions) tells us which registers are live inputs. Conservative default: randomize `r0`-`r3` (all possible argument registers).

2. **Memory inputs**: Scan the function's assembly for `ldr` instructions with hardcoded addresses or register-relative loads. Build a set of "accessed addresses." For register-relative loads (e.g., `ldr r1, [r0, #8]`), the base register is an argument — so randomize the pointed-to memory region rather than the pointer itself (since the pointer must be valid).

3. **Stack inputs**: Some functions take stack arguments (visible as `ldr rN, [sp, #offset]` in the prologue). Randomize those stack slots.

**Input generation strategy:**

```
For each test case (seeded PRNG):
  1. Randomize r0-r3 within configured ranges
  2. For pointer arguments: allocate a memory region, fill with random data
  3. Randomize accessed global memory locations
  4. Set sp to a valid stack address (e.g., 0x03007F00)
  5. Set lr to the sentinel return address
  6. Set CPSR to Thumb mode (bit 5 = 1)
```

**Number of test cases:** Configurable in `mizuchi.yaml`. Default 100. More tests = higher confidence but slower. For the pipeline's retry loop (where speed matters), a lower count (e.g., 20) can be used, with a thorough check (e.g., 500) on the final pass.

**Fixed seed for reproducibility:** Same seed → same inputs → same results. A failing test case can be reproduced exactly.

### c) Comparison

After executing both the original and recompiled function with identical initial state:

**What we compare:**

| Aspect                     | How                                                                     | Priority                                                           |
| -------------------------- | ----------------------------------------------------------------------- | ------------------------------------------------------------------ |
| **Return value**           | `r0` after function returns                                             | Required                                                           |
| **Callee-saved registers** | `r4`-`r11`, `sp` after return                                           | Required (must be preserved or intentionally modified identically) |
| **Written memory**         | Diff all memory regions that were written to during execution           | Required                                                           |
| **MMIO writes**            | Trapped via the Memory trait — record all writes to `0x04000000`+ range | Required                                                           |
| **Cycle count**            | Count instructions or cycles during execution                           | Optional (configurable tolerance)                                  |

**What we explicitly don't compare:**

- Stack layout (internal detail; we only check `sp` is restored correctly)
- Scratch register values (`r0`-`r3`, `r12` — caller-saved, not meaningful at return)
- Instruction sequence (the whole point)
- Internal memory ordering of writes (only final state matters, unless MMIO where order matters)

**MMIO write ordering:** For memory-mapped I/O, write order matters (writing register A then B may have different hardware effects than B then A). We record MMIO writes as an ordered sequence `[(address, value, size)]` and compare the sequences.

**Memory comparison optimization:** Rather than comparing all 256KB+ of emulated memory, we track which addresses were written during execution (dirty tracking in the `Memory` trait implementation) and only compare those.

### d) Failure feedback

When a test case fails, we produce a structured diff that Claude can act on:

```
## Behavioral Decompilation Failure

**Test case #37** (seed: 42, case index: 37)

### Input State
- r0 = 0x02001000 (pointer to struct at EWRAM)
- r1 = 0x05
- r2 = 0x00
- r3 = 0x1A

### Divergence: Return Value
- Expected (original): r0 = 0x03
- Actual (compiled):   r0 = 0x01

### Divergence: Memory
Address 0x02001008:
  - Expected: 0x0000FF03
  - Actual:   0x0000FF01

### Divergence: MMIO Writes
Original wrote 3 MMIO values, compiled wrote 2:
  - Both: [0x04000010] = 0x0080 (BG0HOFS)
  - Both: [0x04000012] = 0x0040 (BG0VOFS)
  - Only original: [0x04000014] = 0x0000 (BG1HOFS)  ← MISSING in compiled

### Execution Summary
- Original: 47 instructions, returned via pop {pc}
- Compiled: 43 instructions, returned via pop {pc}

### 1 of 100 test cases failed.
```

This format gives Claude specific information: which input triggers the bug, which outputs differ, and what the expected values are. It's analogous to objdiff's assembly diff but at the behavioral level.

---

## 4. Configuration Design

```yaml
global:
  # Existing fields...
  maxRetries: 12
  target: gba
  compilerScript: |
    agbcc "{{cFilePath}}" -o asm.s ...

  # New: choose verification mode
  # "objdiff" = byte-matching (current behavior)
  # "behavioral-decompilation" = emulator-based testing (new)
  verificationMode: objdiff

plugins:
  # Existing objdiff config (used when verificationMode is "objdiff")
  objdiff:
    diffSettings:
      arm.archVersion: 'v4t'

  # New plugin config (used when verificationMode is "behavioral-decompilation")
  behavioral-decompilation:
    # Number of random test cases per function
    testCases: 100

    # PRNG seed for reproducible test inputs
    seed: 42

    # Whether to check cycle/instruction count
    cycleCheck: false

    # Maximum allowed cycle difference (only if cycleCheck is true)
    cycleTolerance: 10

    # How to handle function calls within the tested function
    # "stub" = replace callees with immediate return
    # "include" = load callee code from target binary
    calleeMode: stub

    # Memory initialization strategy
    # "zero" = all RAM starts at zero
    # "random" = randomize accessed memory regions
    memoryInit: random

    # Whether to compare MMIO write sequences (order-sensitive)
    checkMmioWrites: true

    # Register arguments to randomize (auto-detected if omitted)
    # registerInputs: [r0, r1, r2, r3]

    # Memory regions to randomize (auto-detected if omitted)
    # memoryInputs:
    #   - { address: 0x02001000, size: 256 }
```

**Zod schema:**

```typescript
export const behavioralDecompConfigSchema = z.object({
  testCases: z.number().int().positive().default(100),
  seed: z.number().int().default(42),
  cycleCheck: z.boolean().default(false),
  cycleTolerance: z.number().int().nonneg().default(10),
  calleeMode: z.enum(['stub', 'include']).default('stub'),
  memoryInit: z.enum(['zero', 'random']).default('random'),
  checkMmioWrites: z.boolean().default(true),
});
```

The `verificationMode` field goes in `pipelineConfigSchema` (global config), since it determines which plugin is registered in the pipeline. The plugin-specific options go in the plugin's own config.

---

## 5. Architecture

### Module structure

```
src/
  shared/
    arm-emulator/              # Isolated library — ARM7TDMI emulation (pure TypeScript)
      cpu.ts                   # CPU core: registers, step(), decode/dispatch
      thumb.ts                 # Thumb instruction execution (35+ instructions)
      arm.ts                   # ARM instruction execution (future, if needed)
      utils.ts                 # Bit ops, shifts, flag computation, sign extension
      memory.ts                # Memory interface + GBA memory map with region dispatch
      cpu.spec.ts              # Tests using SingleStepTests/ARM7TDMI vectors
      input-generator.ts       # Property-based test input generation
      comparator.ts            # State comparison and diff generation

  plugins/
    behavioral-decompilation/    # Plugin — drop-in replacement for objdiff
      behavioral-decompilation-plugin.ts
      behavioral-decompilation-plugin.spec.ts
      types.ts
```

**Name:** `arm-emulator` for the shared library (it's an ARM emulator, not limited to behavioral decompilation). `behavioral-decompilation` for the plugin. No WASM, no external dependencies — pure TypeScript throughout.

### Dependency boundary

The `arm-emulator` module is an **isolated shared library**. Only the `behavioral-decompilation` plugin can import from it. Add to `.dependency-cruiser.cjs`:

```javascript
// arm-emulator can only be imported by the behavioral-decompilation plugin
{
  name: 'arm-emulator-isolation',
  severity: 'error',
  comment: 'arm-emulator shared module can only be imported by the behavioral-decompilation plugin.',
  from: {
    path: '^src/',
    pathNot: [
      '^src/shared/arm-emulator/',       // Self-references
      '^src/plugins/behavioral-decompilation/',  // Allowed consumer
    ],
  },
  to: {
    path: '^src/shared/arm-emulator/',
  },
},
```

### Plugin integration

The `BehavioralDecompPlugin` is a **drop-in replacement for `ObjdiffPlugin`** in the pipeline:

```
Prompt Loader → Claude Runner → Compiler → BehavioralDecompPlugin
                     ↑_______________|________________|
                         (retry on failure)
```

In `src/commands/run.tsx`, the `verificationMode` config determines which plugin is instantiated:

```typescript
// Pseudocode for the registration logic
if (pipelineConfig.verificationMode === 'behavioral-decompilation') {
  const bdPlugin = new BehavioralDecompPlugin(bdConfig, targetObjectPath);
  manager.register(claudePlugin).register(compilerPlugin).register(bdPlugin);
} else {
  const objdiffPlugin = new ObjdiffPlugin(objdiffConfig);
  manager.register(claudePlugin).register(compilerPlugin).register(objdiffPlugin);
}
```

The plugin receives the same `PipelineContext` as objdiff:

- `context.compiledObjectPath` — the compiled `.o` from the Compiler plugin
- `context.targetObjectPath` — the target `.o` from settings
- `context.functionName` — which symbol to test

It returns the same shape:

- `PluginResult<BehavioralDecompResult>` with `status: 'success' | 'failure'`
- Report sections with failure details (for the retry loop and HTML report)

### Data flow

```
1. Extract function code from target .o and compiled .o (ELF parsing)
2. For each test case:
   a. Initialize emulator state (registers, memory) from seeded PRNG
   b. Load target function code → execute → capture final state
   c. Load compiled function code → execute → capture final state
   d. Compare final states → record pass/fail + diff
3. Aggregate results → return PluginResult
4. On failure: format diff as retry feedback (prepareRetry)
```

---

## 6. Risks and Open Questions

### False positives (passing tests despite behavioral differences)

Property-based testing is inherently probabilistic. A function could pass 1000 tests but fail on input 1001. The risk depends on the function's branching complexity:

- **Low risk**: Simple arithmetic functions, linear control flow. 100 random inputs likely covers all paths.
- **Medium risk**: Functions with conditional branches on input values. A specific threshold value might be missed.
- **High risk**: Functions with many nested conditions based on bit flags, lookup tables, or complex state.

**Mitigations:**

- Increase test count for high-complexity functions (configurable per-function).
- Use coverage-guided fuzzing in a future version: instrument the emulator to track which branches are taken, then generate inputs that explore untaken branches.
- Static analysis of the original assembly to identify branch conditions and generate targeted inputs that exercise each branch.
- For the pipeline's purposes, false positives are less dangerous than they sound: the decompiled code will also be reviewed by humans and tested in the full ROM build eventually. The equivalence checker is a filter, not the final word.

### Functions with side effects beyond memory

**DMA:** Some GBA functions trigger DMA transfers by writing to DMA control registers (`0x040000B0`+). The DMA controller copies memory regions asynchronously. Our emulator won't model this, but we _will_ detect the MMIO writes that trigger DMA. If both versions write the same DMA register values, the hardware would perform the same transfer.

**Timers:** Writing to timer registers (`0x04000100`+) affects hardware timers. Same approach: compare the MMIO write sequences.

**Interrupts:** Functions that enable/disable interrupts via `IME`/`IE`/`IF` registers (`0x04000200`+). We record these as MMIO writes. Functions that _handle_ interrupts (ISRs) are a harder case — they're invoked asynchronously by hardware. For v1, we don't test ISRs in isolation; they'd need a different harness.

**Software interrupts (SWI):** GBA BIOS calls via `swi` instruction (e.g., `swi 0x06` for division). **No SWI instructions appear in the current benchmark dataset**, so this is not a v1 blocker. If needed later, we'd implement handlers for common BIOS calls (Div, Sqrt, ArcTan2, CpuSet) — their behavior is well-documented. Note: gbajs includes full SWI BIOS emulation; armv4t_emu does not (we'd add our own).

### Non-termination

If a function enters an infinite loop (e.g., a spin-wait on a hardware register that never changes in emulation), execution will never return. We need an **instruction count limit** — a configurable maximum number of instructions per invocation (e.g., 100,000). If the limit is hit, the test case is marked as "timed out" rather than pass/fail. This is straightforward to implement: increment a counter in the step loop and break when it exceeds the limit.

### Functions that depend on execution history

Functions that behave differently based on prior game state (e.g., `gStageData` flags, `gLoadedSaveGame`) will produce different results depending on what memory state we initialize. This isn't really a false negative risk — both the original and recompiled function see the same initial state. If they diverge for any initial state, the function is not equivalent.

The real question is **coverage**: if we only test with zero-initialized globals, we might miss a code path that only activates when `gStageData.zone == 3`. Random initialization helps here by exploring more state space, but targeted initialization (from captured runtime state) would be even better as a future enhancement.

### Performance

**Back-of-envelope calculation:**

- Average function: ~100 Thumb instructions
- Emulator overhead per instruction: ~50ns (interpreted WASM, conservative)
- Per function invocation: ~5μs
- Per test case (2 invocations — original + compiled): ~10μs
- 100 test cases: ~1ms
- With setup/teardown overhead: ~5ms per function per attempt

This is negligible compared to Claude API latency (~5-30 seconds per attempt). Even 1000 test cases would take ~50ms. **Performance is not a concern.**

### ELF parsing

We need to extract function code from `.o` files. Options:

- Use objdiff-wasm's existing ELF parsing (it already does this for assembly extraction).
- Use a standalone ELF parser. There are Rust ELF crates (`goblin`, `elf`) that could be included in the WASM build.
- Parse the ELF in TypeScript (there are npm packages like `elfy`, or we parse the simple cases ourselves).

The simplest approach: use objdiff-wasm's existing `parseObjectFile` to get symbol addresses and sizes, then read the raw bytes from the `.o` file at those offsets.

### Relocation handling

Object files contain relocations — references to external symbols that the linker resolves. For example, a `bl sub_8069814` instruction in the `.o` file won't have the final target address; it'll have a relocation entry. We need to:

1. Parse relocations from both `.o` files.
2. For `stub` mode: resolve `bl` relocations to point at our stub function.
3. For `include` mode: resolve relocations to the actual callee code (which must also be loaded).
4. For data references (e.g., `ldr r0, =gCamera`): resolve to the appropriate address in our memory map and populate that address.

This is the most complex part of the implementation. Objdiff-wasm already handles relocations for diffing; we may be able to reuse some of that logic.

### Thumb interworking

**All 30 benchmark functions are Thumb-only.** No ARM-mode instructions appear in the dataset. The `bx` instruction (present in all 30 functions) is used solely as a return instruction (`bx lr`), not for ARM/Thumb interworking. Both emulator candidates handle Thumb mode correctly.

For future functions that mix ARM and Thumb, the `armv4t_emu` crate supports both modes and transitions via `bx`. We need to set bit 0 of the entry address to indicate Thumb mode.

### Custom emulator correctness

Writing our own emulator introduces the risk of instruction-level bugs. This is the primary risk of the custom TypeScript approach, but it is well-mitigated:

- **SingleStepTests/ARM7TDMI** provides 1,250,000 Thumb test vectors (50,000 per instruction class). We run all vectors for the 15 formats used by the benchmark before trusting the emulator. Any failure is caught during development, not at runtime.
- **Two high-quality reference implementations** (armv4t_emu's `thumb.rs` at 508 lines, gbajs's `thumb.js` at 854 lines) to cross-check our logic against.
- **The instruction set is small and well-documented.** The ARM7TDMI Thumb ISA has 19 formats total, and we implement 15. Each format has a clear bit-encoding spec in [GBATEK](http://problemkaputt.de/gbatek-thumb-instruction-summary.htm) and the [ARM7TDMI TRM](https://ww1.microchip.com/downloads/en/DeviceDoc/DDI0029G_7TDMI_R3_trm.pdf).
- **Incremental validation**: implement one format, run its 50,000 test vectors, fix any bugs, move to the next. No "big bang" integration risk.

---

## 7. Validation Target: `prompts-sa3-benchmark`

The benchmark contains **30 functions** across three difficulty tiers (10 each).

### Instruction analysis

All 30 functions were analyzed. They use exactly **35 unique Thumb mnemonics**. No ARM-mode instructions appear anywhere. No SWI instructions.

**Instruction frequency (all 30 functions combined):**

| Mnemonic | Count | Functions using | Category                |
| -------- | ----- | --------------- | ----------------------- |
| `adds`   | 354   | 30/30           | Data processing         |
| `ldr`    | 292   | 30/30           | Load/store              |
| `movs`   | 286   | 29/30           | Data processing         |
| `lsls`   | 225   | 27/30           | Data processing         |
| `strh`   | 147   | 18/30           | Load/store              |
| `str`    | 145   | 22/30           | Load/store              |
| `cmp`    | 109   | 23/30           | Data processing         |
| `mov`    | 105   | 8/30            | Hi register ops         |
| `strb`   | 76    | 16/30           | Load/store              |
| `asrs`   | 70    | 15/30           | Data processing         |
| `bl`     | 65    | 24/30           | Branch (long, two-part) |
| `ldrb`   | 59    | 17/30           | Load/store              |
| `pop`    | 58    | 29/30           | Stack                   |
| `lsrs`   | 50    | 19/30           | Data processing         |
| `ldrh`   | 48    | 19/30           | Load/store              |
| `ldrsh`  | 43    | 7/30            | Load/store (signed)     |
| `push`   | 36    | 29/30           | Stack                   |
| `b`      | 34    | 12/30           | Branch                  |
| `beq`    | 32    | 13/30           | Conditional branch      |
| `bx`     | 30    | 30/30           | Branch and exchange     |
| `subs`   | 25    | 13/30           | Data processing         |
| `bne`    | 25    | 10/30           | Conditional branch      |
| `add`    | 23    | 10/30           | Hi register ops         |
| `ands`   | 22    | 7/30            | Data processing         |
| `muls`   | 20    | 3/30            | Multiply                |
| `rsbs`   | 17    | 7/30            | Data processing         |
| `bls`    | 14    | 8/30            | Conditional branch      |
| `ble`    | 13    | 7/30            | Conditional branch      |
| `orrs`   | 11    | 6/30            | Data processing         |
| `sub`    | 10    | 10/30           | Data processing         |
| `blt`    | 9     | 6/30            | Conditional branch      |
| `bgt`    | 8     | 5/30            | Conditional branch      |
| `ldrsb`  | 5     | 2/30            | Load/store (signed)     |
| `bge`    | 4     | 4/30            | Conditional branch      |
| `bhi`    | 4     | 3/30            | Conditional branch      |

**Not used in the benchmark** (not needed for v1 but implemented by both emulator candidates): `swi`, `adcs`, `sbcs`, `eors`, `bics`, `mvns`, `tst`, `cmn`, `rors`, `negs`, `stmia`, `ldmia`, `nop`, `bcc`, `bcs`, `bmi`, `bpl`, `bvs`, `bvc`.

**Both `armv4t_emu` and gbajs implement all 35 instructions used in the benchmark.**

### Special instructions in the dataset

- **`bl`** (65 occurrences, 24/30 functions): Standard function call. Encoded as two 16-bit halfwords in Thumb mode. The emulator must handle the two-part BL sequence correctly.
- **`bx`** (30 occurrences, 30/30 functions): Used as the return instruction (`bx lr`). Every function uses it. Must support reading the T bit for mode determination.
- **`muls`** (20 occurrences, 3 functions): Thumb MUL. Only used in hard-tier functions (`sub_806D01C`, `sub_80720E4`, `sub_8078F74`).
- **`ldrsh`/`ldrsb`** (48 total): Signed halfword/byte loads. Important for correct sign-extension.

### Function characteristics

| Tier   | Function     | Instructions | Global Access                                   | Callee Calls             | Key Challenge                |
| ------ | ------------ | ------------ | ----------------------------------------------- | ------------------------ | ---------------------------- |
| Easy   | sub_8087590  | ~307         | 5 data tables                                   | 5× UpdateSpriteAnimation | Large loops, repetitive      |
| Easy   | sub_80C5FCC  | ~120         | None                                            | Recursive self-call      | Recursion, hitbox collision  |
| Easy   | sub_80219E8  | ~est. 50-100 | Likely minimal                                  | Likely few               | —                            |
| Medium | sub_8068748  | ~30          | gCamera                                         | 1 call                   | Short, global access         |
| Medium | sub_80AE1C8  | ~35          | 3 fn ptrs + 0xFFFFD800                          | TaskCreate               | Unusual address, task system |
| Medium | sub_0807F4F0 | ~est. 50-100 | Likely some                                     | Likely few               | —                            |
| Hard   | sub_8068C38  | ~97          | gCurTask, gStageData                            | 8 calls                  | Complex state machine        |
| Hard   | sub_80AC0C4  | ~150+        | gCurTask, gLoadedSaveGame, gInput, 6 queue vars | 4 calls                  | UI/credits, input processing |
| Hard   | sub_8087A48  | ~est. 100+   | Likely extensive                                | Likely many              | —                            |

### Challenge assessment

**Easily testable** (no special handling needed):

- `sub_80C5FCC` — no globals, pure recursive logic. Ideal first target.
- `sub_8068748` — one global (gCamera), one callee. Simple with stub mode.
- Simple easy-tier functions with few dependencies.

**Testable with global state setup:**

- `sub_8087590` — needs 5 data tables loaded at known addresses. The tables are in ROM, so we'd load them from the target `.o` or the ROM.
- `sub_80AE1C8` — needs function pointers resolved. The address `0xFFFFD800` may be a two's complement negative offset used in address calculation (`0xFFFFD800 = -10240`); needs investigation.

**Testable with stub mode:**

- `sub_8068C38` — 8 callee calls, but in stub mode they all return 0. Both versions call the same stubs, so divergences still reveal bugs.
- `sub_80AC0C4` — 4 callee calls + extensive globals. Requires careful memory setup but stub mode handles the calls.

**Potential issues:**

- Functions that branch differently based on return values of callees will have limited path coverage in stub mode (always seeing `r0=0` from callees). Random global memory initialization partially compensates.
- The `gInput` dependency in `sub_80AC0C4` means input-processing logic needs the input struct populated. With random memory initialization, this naturally happens.

### Recommendation

**All 30 functions are testable with the proposed approach.** The hard-tier functions will have lower path coverage in stub mode (because callee return values affect control flow), but the equivalence check is still valid: both versions see the same stub behavior, so any divergence is a real bug.

Priority for validation:

1. Start with `sub_80C5FCC` (easy, no globals, recursive — cleanest test case)
2. Then `sub_8068748` (easy, one global, one callee — tests global handling)
3. Then `sub_8087590` (easy, data tables — tests memory loading)
4. Then medium tier (tests callee stubbing)
5. Then hard tier (tests complex state setup)

---

## Appendix: Implementation Roadmap (for Phase 2)

If approved, the implementation would proceed in this order:

1. **ARM7TDMI Thumb emulator** (`src/shared/arm-emulator/`): Implement the 35 Thumb instructions used by the benchmark in TypeScript (~600-800 lines). Use `armv4t_emu` and gbajs as implementation references. Use `Uint32Array(16)` for registers, `DataView` over `ArrayBuffer` for memory, `Math.imul` for MUL.
2. **Emulator validation**: Download SingleStepTests/ARM7TDMI Thumb test vectors. Write a Vitest suite that reads the binary test format and runs all 50,000 vectors for each of the 15 instruction classes used by the benchmark. Fix any failures before proceeding.
3. **GBA memory map**: Implement the memory interface with region dispatch (EWRAM, IWRAM, ROM, MMIO trap), dirty tracking for written addresses, and MMIO write recording.
4. **ELF extraction**: Extract function code + relocations from `.o` files.
5. **Input generator**: Seeded PRNG, register randomization, memory initialization.
6. **Comparator**: State diffing, failure report generation.
7. **Plugin** (`src/plugins/behavioral-decompilation/`): Wire everything into the pipeline interface.
8. **Configuration**: Add `verificationMode` to global config, plugin-specific Zod schema.
9. **Dependency cruiser rule**: Enforce `arm-emulator` isolation.
10. **Test on `sub_80C5FCC`**: End-to-end validation on the simplest benchmark function.
11. **Test on remaining benchmark**: Validate all 30 functions.
