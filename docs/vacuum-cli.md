# Vacuum CLI

`scripts/vacuum-cli.sh` is the entry point for autonomous matching.

## Commands

- `start` — initialize queue and run the loop
- `resume` — continue from saved queue state
- `status` — show pending/matched/failed counts
- `inspect-queue` — print queue JSON
- `reset-queue --function <name>` — move failed/difficult item back to pending
