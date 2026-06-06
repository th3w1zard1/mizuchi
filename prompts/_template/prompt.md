# Objective

Decompile `REPLACE_ME` so that compiled C produces **byte-identical** object code to:

`build/obj/REPLACE_ME.o`

Success = **0 objdiff differences**. Functional equivalence is not enough.

# Context

<!-- Paste m2ctx / typedef / extern declarations from Get Context -->

Case identity and proof metadata live in `case.yaml`. Keep `prompt.md` focused on the
working brief for the current run.

# Similar matched functions

<!-- From Decomp Atlas or prior matches in this project -->

# Callers

<!-- Who calls this function — Ghidra xrefs -->

# Declaration

```c
/* Prototype as used in the project */
```

# Callees

<!-- Functions this one calls -->

# Types

```c
/* Structs and typedefs referenced */
```

# Rules

- Match register/stack usage implied by assembly
- Reuse existing project types; search before defining new structs
- No `goto` unless assembly requires it
- Test with `compile_and_view_assembly` before final submission

# Target assembly

See `settings.yaml` `asm` block (same content repeated here for agent readability if desired).
