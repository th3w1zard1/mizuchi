---
description: Build or refine prompts/<fn>/prompt.md + settings.yaml for matching decompilation.
---

Use `decomp-prompt-architect` and `prompts/_template/` to assemble a valid prompt folder.

## Inputs

- Target function name (required).
- Optional target object path.
- Optional target assembly or object-slice metadata.

## Procedure

1. Ensure folder exists: `prompts/<function-name>/`.
2. If missing, copy scaffold from `prompts/_template/`.
3. Populate `settings.yaml` (three fields only):
   - `functionName`
   - `targetObjectPath`
   - `asm` block
4. Fill `prompt.md` using Atlas ordering:
   - objective
   - platform
   - context
   - target
   - m2c seed
   - similar examples
   - call graph
   - constraints
5. Validate:

```bash
./scripts/validate-prompt-settings.sh prompts/<function-name>/
```

Output path(s) updated and any missing required data.
