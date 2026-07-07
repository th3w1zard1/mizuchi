---
description: Run function matching pipeline (programmatic first, then AI loop if needed).
---

Execute one function through the full workspace pipeline.

## Required input

- Prompt folder path: `prompts/<function-name>/`

## Procedure

1. Run the orchestrator:

```bash
./scripts/decomp-cli.sh decomp-function <function-name>
```

The orchestrator runs programmatic matching first, falls through to the AI phase
only when programmatic matching does not reach objdiff 0, and writes
`prompts/<function-name>/build/decomp-function.json`.

2. For phase-level debugging, validate prompt settings:

```bash
./scripts/validate-prompt-settings.sh prompts/<function-name>/
```

3. Run programmatic phase directly when isolating m2c/permuter behavior:

```bash
./scripts/run-programmatic-phase.sh --prompt prompts/<function-name>/
```

4. If still not matched, run iterative compile loop:

```bash
./scripts/compile-and-view-assembly.sh --prompt prompts/<function-name>/ --code-file trial.c
```

5. Verify match gate:

```bash
./scripts/objdiff-gate.sh <target.o> prompts/<function-name>/build/candidate.o
```

Only report `matched` when objdiff is 0. Treat
`build/decomp-function.json` as the command-level receipt and the phase receipts
(`programmatic-phase.json`, `ai-phase.json`) as supporting evidence.
