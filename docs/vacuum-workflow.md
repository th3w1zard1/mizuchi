# Vacuum Workflow

Operator guide for the autonomous matching-decompilation loop.

## What It Does

The vacuum loop selects pending prompt folders, runs one-shot matching per
function, verifies with compile + objdiff (or the case verifier), and updates
persistent queue state. It follows the Chris Lewis throughput pattern: easiest
functions first, one attempt per pass, commit only after proof.

## Quick Start

```bash
./scripts/decomp-cli.sh vacuum init --queue state/queue.json --prompts-dir prompts
./scripts/decomp-cli.sh vacuum start --queue state/queue.json --max-functions 1 --timeout 30m
./scripts/decomp-cli.sh vacuum status --queue state/queue.json
```

Optional one-shot package bridge:

```bash
./scripts/decomp-cli.sh import-one-shot-tasks --package target/<app>/one-shot-source --prompts-dir prompts
./scripts/decomp-cli.sh one-shot-task-coverage --package target/<app>/one-shot-source --queue state/queue.json
```

## Loop Behavior

1. Load `state/queue.json` and score pending prompts (easiest first).
2. Pick the highest-scored pending function.
3. Run the matcher runner (default: `./scripts/decomp-cli.sh decomp-function <name>`).
4. On success, optionally commit via `commit-verified-match` when
   `--commit-after-match` is set.
5. Move the function to `matched`, `failed`, or `difficult` (after max attempts).
6. Back off on quota errors (exponential, 5-minute base, 60-minute cap).
7. Repeat until the queue is empty, the function limit is hit, or timeout.

## Monitoring

- `logs/vacuum-progress.log` — human-readable timeline.
- `state/vacuum-session.json` — last loop outcome, backoff, timeout.
- `state/scores.json` — heuristic scores and reasons.
- Per-function build receipts under `prompts/<name>/build/`.

Inspect the queue:

```bash
./scripts/decomp-cli.sh vacuum inspect-queue --queue state/queue.json
```

## Resume and Recovery

```bash
./scripts/decomp-cli.sh vacuum resume --queue state/queue.json --max-functions 5
./scripts/decomp-cli.sh vacuum reset-queue --queue state/queue.json --name <fn>
```

Ctrl-C is safe: the loop traps signals and writes queue state before exit.

## Troubleshooting

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| No pending functions | All processed or blocked | `vacuum inspect-queue`; check `case.yaml` status |
| Repeated BACKOFF lines | API quota / rate limit | Wait; use `resume` after recovery |
| DIFFICULT after 10 attempts | Hard function or bad context | Triage manually; `reset-queue` to retry |
| Build output huge in logs | Compile failure | Check `build-and-verify.compile.summary.txt` (capped) |
| Matcher never runs | Wrong runner command | Pass `--runner-command` or use default decomp-function |

## Proof Boundary

A vacuum `MATCHED` state means the configured verifier accepted the candidate
for that prompt. It does not by itself claim whole-program source parity. See
`STRATEGY.md` and `docs/knowledgebase/50-execution/source-parity-implementation-roadmap.md`.
