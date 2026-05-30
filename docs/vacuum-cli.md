# Vacuum CLI

`scripts/vacuum-cli.sh` is the entry point for autonomous matching.

## Commands

- `init` — initialize queue from prompt folders and score pending items
- `status` — show pending/matched/failed counts
- `next` — print highest-scored pending function name
- `score` — rescore pending functions and write `state/scores.json`
- `inspect-queue` — print queue JSON
- `reset-queue --function <name>` — move failed/difficult item back to pending
