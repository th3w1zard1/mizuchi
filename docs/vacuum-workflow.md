# Vacuum workflow (Cycle 3 U1)

Unit 1 covers persistent queue state and scoring — not the full autonomous loop (U5).

## Initialize queue from prompt folders

```bash
./scripts/init-vacuum-state.sh
# or
./scripts/vacuum-cli.sh init
```

Creates `state/queue.json`, seeds pending entries from `prompts/*` (except `_template`), and runs the scorer.

## Inspect and select

```bash
./scripts/vacuum-cli.sh status
./scripts/vacuum-cli.sh inspect-queue
./scripts/vacuum-cli.sh next
./scripts/vacuum-cli.sh score
```

## Retry a difficult function (when queue exists)

```bash
./scripts/vacuum-cli.sh reset-queue --function fun_00148020
```

## Follow-up (Cycle 3 U5+)

`start` and `resume` will run the full vacuum loop once matcher and build-and-verify land in later units.
