# Objective

Recover `roundtrip_identity` as C that compiles to a byte-identical object file.

This prompt is a local proof fixture: the golden object is rebuilt from
`target.c`, and the candidate is rebuilt from `candidate.c` with the same
deterministic compiler command. It keeps the verification plumbing honest even
when the Xbox/MSVC target toolchain is not available on this host.

# Target assembly

```asm
roundtrip_identity:
    leal    7(%rdi,%rdi,2), %eax
    ret
```
