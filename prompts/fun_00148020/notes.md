# FUN_00148020 — scaffold notes

**status: matched against local asm scaffold**

## Provenance

| Field | Value |
|-------|--------|
| Binary | `/TSL/k2_xbox_default.xbe` (not present locally) |
| Address | `0x00148020` |
| Size | 12 bytes |
| Ghidra | Disassembly OK; decompiler failed (shared server) |
| Local proof target | `target.S` assembled from exported Ghidra asm |

## Local scaffold status

This prompt can be verified locally against the asm-derived scaffold target.
That removes the local pipeline blocker, but it is not a substitute for an
object extracted from an original Xbox build. When the real build artifact is
available, replace `targetSourcePath`/`targetObjectPath` with the extracted
object and keep the same objdiff gate.

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
