---
description: Integrate verified (objdiff 0) function candidates into source trees.
---

Integration is post-verification only.

## Preconditions

- Candidate has verified objdiff 0.
- Match evidence recorded in `prompts/<fn>/notes.md`.
- User explicitly requested integration.

## Procedure

1. Re-run gate:

```bash
./scripts/integrate-verified-match.sh --prompt prompts/<function-name> --source-out <path/to/source.c>
```

2. If gate passes, the script copies candidate code into the intended project source location.
3. Run project-native checks for the destination repository.
4. Record integration summary (target path, verification evidence, residual risks).

If gate fails, stop and return to `/decomp-function`.
