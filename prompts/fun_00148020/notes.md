# FUN_00148020 — scaffold notes

**status: matched**

## Provenance

| Field | Value |
|-------|--------|
| Binary | `/TSL/k2_xbox_default.xbe` |
| Address | `0x00148020` |
| Size | 12 bytes |
| Ghidra | Disassembly OK; decompiler failed (shared server) |

## `[OPEN]` Before Mizuchi run

1. Extract golden `build/xbox/fun_00148020.o` from Xbox decomp build (same flags as game)
2. Wire `global.compilerScript` in `mizuchi.yaml` (placeholder → real Xbox/x86 toolchain)
3. Add m2ctx context for types at `this+0` and symbol at `0x0040e180`

## Expected C shape (hypothesis — verify with objdiff)

```c
void *FUN_00148020(void *this) {
    void *p = *(void **)this;
    if (p != NULL) {
        return p;
    }
    return (void *)0x0040e180;
}
```

Calling convention and typedefs may differ once project headers are wired.
