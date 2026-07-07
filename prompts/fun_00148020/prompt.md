# Objective

Decompile `FUN_00148020` from **KOTOR 2 Xbox** (`/TSL/k2_xbox_default.xbe`) so recompiled C produces **byte-identical** object code to:

`build/xbox/fun_00148020.o`

Success = **0 objdiff differences**. Functional equivalence is not enough.

# Context

<!-- Wire getContextScript in reconkit.yaml after extracting ctx from the Xbox decomp tree -->
<!-- Pointer at ECX+0 is likely a vtable or member pointer; fallback global at 0x0040e180 -->

# Similar matched functions

<!-- Search Decomp Atlas for other ECX-deref getters returning a default pointer -->

# Callers

- Heavily called (300+ xrefs). Example callers: `FUN_00107e20`, `FUN_001576a0`, `FUN_00040a10`, `FUN_000d1eb0`.
- Treat as a small **thiscall** helper: `this` in **ECX**, return value in **EAX**.

# Declaration

```c
/* __thiscall — adjust type once project vtable/context is known */
void *FUN_00148020(void *this);
```

# Callees

None (leaf function, 12 bytes).

# Types

```c
/* Reuse project types for whatever lives at offset 0 of `this` */
```

# Rules

- **12 bytes total** — do not add branches or calls Ghidra did not show
- `this` is **ECX** (Microsoft x86 thiscall)
- First dword at `this` tested; if zero, return immediate `0x0040e180`
- Test with `compile_and_view_assembly` before final submission

# Target assembly

```asm
movl    (%ecx), %eax
testl   %eax, %eax
jnz     .Lret
movl    $0x0040e180, %eax
.Lret:
ret
```

Source: Ghidra disassembly (`get-function` on `/TSL/k2_xbox_default.xbe`). Decompiler unavailable on shared server — asm is authoritative for this prompt.
