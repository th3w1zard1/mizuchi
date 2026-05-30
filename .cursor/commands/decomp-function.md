---
description: Run function matching pipeline (programmatic first, then AI loop if needed).
---

Execute one function through the full workspace pipeline.

## Required input

- Prompt folder path: `prompts/<function-name>/`

## Procedure

1. Validate prompt settings:

```bash
./scripts/validate-prompt-settings.sh prompts/<function-name>/
```

2. Run programmatic phase first:

```bash
./scripts/run-programmatic-phase.sh --prompt prompts/<function-name>/
```

3. If still not matched, run iterative compile loop:

```bash
./scripts/compile-and-view-assembly.sh --prompt prompts/<function-name>/ --code-file trial.c
```

4. Verify match gate:

```bash
./scripts/objdiff-gate.sh <target.o> prompts/<function-name>/build/candidate.o
```

Only report `matched` when objdiff is 0.
