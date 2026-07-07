# Genuine one-shot semantic decompilation — proof + honest limits

This demonstrates the goal of *authoritative, accurate source code that recompiles into
more or less the same app* — **real readable C, not a byte array** — and measures exactly
how far it goes on real DAW binaries.

## What "authoritative source" means here vs. the byte-roundtrip path

`scripts/binary-source-roundtrip.py` / `one-shot-source.py` produce a file like:

```c
static const uint8_t mizuchi_image[773736] = { 0x7f,0x45,0x4c,0x46, ... };
int main(void){ return fwrite(mizuchi_image,1,sizeof(mizuchi_image),stdout)...; }
```

That recompiles to *byte-identical* output but is the original binary pasted into an
array — the tool honestly labels it `semanticDecompilation: false`. It is **not** source code.

This proof produces the opposite: genuine recovered logic.

## The capability proof (reproducible, automatically checked)

`scripts/decompile-verify-equivalence.sh`:

1. compile `orig.c` → `-O2` **stripped** shared object (the decompiler's only input;
   `orig.c` is never shown to Ghidra),
2. Ghidra 12.1 headless decompiles the stripped binary → readable C
   (`process_sample.recovered.c`),
3. recompile the recovered C — **one shot**, the only transformation is prepending
   `scripts/ghidra/ghidra_types.h` (defines Ghidra-isms like `uint`; no logic edits),
4. differential test: 2,000,000 random inputs through original vs. recovered.

Result: `tested=2000000 mismatches=0 -> BEHAVIORALLY IDENTICAL`.

The recovered source is real logic (clamp + Q16 scale + branchless abs), recovered purely
from machine code — see `process_sample.recovered.c`.

## Honest limits on real DAW code

`scripts/decompile-recompile-verify.sh` ran on a real library,
`reaper773_linux_x86_64/REAPER/Plugins/rubberband.so` (optimized C++):

| metric | value |
|---|---|
| functions recovered as readable C | 429 |
| recompiled **one shot** (type-shim only) | **59 / 429 (~14%)** |

The other ~86% are recovered as readable C++ *pseudocode* (`vector<int,std::allocator<int>>`,
`ostream`, SIMD `undefined1 auVar[16]`) that needs real type/STL/ABI reconstruction before it
recompiles — that is **not** a one-shot step, and not something any current tool does
automatically for whole programs.

### Bottom line

- One-shot decompilation to recompilable, behaviorally-equivalent C is **real and proven**
  for self-contained functions (leaf math/DSP/codec-style code).
- For a **whole stripped, optimized C++ DAW** (Ableton, Pro Tools, the `reaper` 12 MB binary):
  not achievable in one shot by this or any current technology. The tractable unit is the
  **function**, with honest per-function recompile/equivalence metrics — not the app.
