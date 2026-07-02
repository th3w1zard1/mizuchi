# Behavioral Mismatch Analysis: `sub_807ECFC` (Attempt 1)

**Report file:** `run-results-2026-03-13T23-15-08.json`
**Result:** 97/100 scenarios failed (3 matched, 97 mismatched)

## Summary

All 97 mismatches share the same root cause: **the original and compiled code take different branches** because the branch condition (`unk16`) is read from memory outside the initialized test region. The two sides see different uninitialized memory, leading to different code paths.

There is also a secondary issue: the compiled code writes `0x28` to `unk16` where the C source says `entry->unk16 = 10` (i.e. `0x0A`), suggesting a **struct layout mismatch**.

## Mismatch Pattern

Every single failure follows this exact pattern (only addresses change):

|                    | Original               | Compiled              |
| ------------------ | ---------------------- | --------------------- |
| Write count        | 1                      | 4                     |
| entry+0x08 (unk8)  | -                      | `0x00000000` (32-bit) |
| entry+0x0C (unkC)  | -                      | `0xfffffd00` (32-bit) |
| entry+0x14 (unk14) | -                      | `0x0000` (16-bit)     |
| entry+0x16 (unk16) | varies (e.g. `0x1c4c`) | `0x0028` (16-bit)     |

- **Compiled** always takes the `state == 0` branch, writing 4 fields (unk8, unkC, unk14, unk16).
- **Original** always takes a different branch (likely `state == 10`), only writing 1 field to entry+0x16.

There are only 3 unique address patterns, corresponding to the 3 unique (r0, r1) input combinations:

| r0           | r1 (as u8) | Entry base   | Scenarios |
| ------------ | ---------- | ------------ | --------- |
| `0x02000100` | 0          | `0x02000244` | 1         |
| `0x03000100` | 10         | `0x030003D4` | 1         |
| `0x03000100` | 173 (0xAD) | `0x03001D4C` | 95        |

## Root Cause 1: Test Input Memory Region Too Small

The function computes the entry address as:

```
entry = r0 + 0x144 + r1 * 0x28
```

The field `unk16` (the branch condition) is at `entry + 0x16`. For every mismatched scenario, this address falls **outside** the initialized memory region:

| Scenario     | Memory region        | unk16 address | Offset from region start | Region size |
| ------------ | -------------------- | ------------- | ------------------------ | ----------- |
| #3 (r1=0)    | 256B at `0x02000100` | `0x0200025A`  | 346                      | 256         |
| #4 (r1=10)   | 128B at `0x03000100` | `0x030003EA`  | 746                      | 128         |
| #5+ (r1=173) | 199B at `0x03000100` | `0x03001D62`  | 7266                     | 199         |

Since `unk16` is always outside the test memory:

- The **original binary** reads whatever uninitialized data exists in the emulator's memory (likely non-zero garbage), so `state != 0`, taking a different branch.
- The **compiled binary** reads zero-initialized memory, so `state == 0`, entering the initialization branch.

This is a limitation of the test input generator: **the memory regions it creates are too small to cover the struct fields that the function actually accesses.** The function uses `r1` as an array index into a struct far past the allocated region, so the branch-controlling field ends up in uninitialized territory.

## Root Cause 2: Compiled Value Discrepancy (`0x28` vs `0x0A`)

The C code says:

```c
entry->unk16 = 10;  // 10 decimal = 0x0A
```

But the compiled binary writes `0x28` (40 decimal) to the same offset. Notably:

```
sizeof(Sub807ECFC_Entry) = 4+4+4+4+4+2+2+16 = 40 = 0x28
```

The compiled value `0x28` equals `sizeof(Sub807ECFC_Entry)`. This is suspicious and suggests one of:

1. **Wrong struct layout** — padding or field sizes differ from the original, causing misaligned field accesses. The write to `entry+0x16` might actually be targeting a different field.
2. **Wrong literal value** — the original code may use a different constant than `10`.
3. **Compiler artifact** — agbcc may be merging a constant that happens to equal the struct size.

## Why 3 Scenarios Matched

Scenarios 0-2 are not included in the failure array. These likely correspond to the input generator's initial edge cases (e.g., null pointer, minimal r1 values) where both sides happened to read the same memory state or hit matching early-return paths.

## Recommendations

1. **Increase test memory regions** — The input generator should allocate enough memory to cover the full struct access range. For functions that index into arrays (`r0 + offset + r1 * stride`), the region should extend to at least `offset + max_r1 * stride + entry_size`.

2. **Investigate the `0x28` value** — Disassemble the compiled `.o` to check whether the literal pool contains `0x28` or `0x0A`. If it's `0x28`, the struct layout needs revision (likely padding or field order is wrong). Compare against the original binary's disassembly to verify the expected constant.

3. **Consider pre-seeding `unk16`** — Since `unk16` controls the branch, test scenarios could explicitly initialize it to known values (0, 10, other states) to cover all code paths deterministically rather than relying on random memory.
