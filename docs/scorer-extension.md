# Scorer Extension Guide

How to extend function selection beyond the default heuristic scorer.

## Current Heuristic

`scripts/lib/scorer-heuristic.sh` scores pending prompts from assembly in
`prompt.md`:

- Fewer instructions → higher score (easier first).
- Fewer branches → bonus.
- Label density adjusts tie-breaking.

Output: `state/scores.json`, optionally merged into `state/queue.json` via
`--update-queue`.

```bash
./scripts/decomp-cli.sh scorer --update-queue --queue state/queue.json
```

## ML Hooks (Prepared, Not Active)

`scripts/lib/scorer-ml-hooks.sh` defines:

- `scorer_ml_enabled` — reads `SCORER_ML_ENABLED` (default `false`).
- `scorer_ml_predict(name, asm_block)` — falls back to heuristic when disabled.

To enable a trained model later:

1. Implement `scorer_ml_predict` to return a numeric score.
2. Set `SCORER_ML_ENABLED=true` in the environment.
3. Keep heuristic as fallback when the model returns empty.

## Training Data (Cycle 4+)

Collect features from matched vs difficult functions:

- Instruction count, branch count, call count.
- Has indirect calls, stack frame size proxy, loop depth proxy.
- Objdiff attempt count before match.

Train offline; do not block the vacuum loop on training.

## Validation

- Scoring must be deterministic for the same `prompt.md` input.
- Re-run `tests/scorer_test.sh` after hook changes.
- Compare ordering against a manual “easy functions” list before enabling ML in production runs.
