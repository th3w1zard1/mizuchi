# Vacuum CLI

The vacuum loop packages the article-style matching workflow into resumable
state files. It does not replace per-function proof: a match still requires a
successful verifier report.

## Quick Start

```bash
./scripts/decomp-cli.sh import-one-shot-tasks --package target/<app>/one-shot-source --prompts-dir prompts
./scripts/decomp-cli.sh one-shot-task-coverage --package target/<app>/one-shot-source --prompts-dir prompts --queue state/queue.json
./scripts/decomp-cli.sh vacuum init --queue state/queue.json --prompts-dir prompts
./scripts/decomp-cli.sh vacuum start --queue state/queue.json --max-functions 1 --timeout 30m
./scripts/decomp-cli.sh vacuum status --queue state/queue.json
```

`import-one-shot-tasks` is optional for hand-authored prompts, but it is the
bridge from a `one-shot-source` package into the vacuum loop. It reads
`FUNCTION_RECONSTRUCTION_TASKS.json` and creates prompt folders whose
`case.yaml` uses a task-local byte-slice verifier command.

`one-shot-task-coverage` audits that bridge. It classifies each package task as
not imported, pending, missing a candidate, unverified, matched, blocked, or
integrated. Its `semanticReady` field remains false unless every package task is
verified and the package semantic-readiness evidence also allows promotion.

`vacuum init` creates `state/`, `logs/`, `queue.json`, `scores.json`, and
`vacuum-session.json`. It classifies prompt manifests into pending, matched,
and integrated queue states. Blocked prompts stay out of the runtime queue and
are counted in the init receipt.

## Operating The Loop

Use `resume` after an interruption or quota backoff:

```bash
./scripts/decomp-cli.sh vacuum resume --queue state/queue.json --max-functions 1
```

Use `reset-queue` to move a failed or difficult function back to pending:

```bash
./scripts/decomp-cli.sh vacuum reset-queue --queue state/queue.json --name FUN_00148020
```

Use `--commit-after-match` only when you want the loop to call the verified
commit helper after a runner succeeds. The helper re-runs verification and
stages only the candidate, verifier receipts/logs, commit receipt, and explicit
`--commit-path` entries.

```bash
./scripts/decomp-cli.sh vacuum start --queue state/queue.json --commit-after-match --max-functions 1
```

## Receipts

- `state/queue.json`: pending/matched/integrated/failed/difficult plus attempts.
- `state/scores.json`: deterministic easiest-first scoring for pending prompts.
- `state/vacuum-session.json`: last loop state, backoff, timeout, or signal.
- `logs/vacuum-progress.log`: human-readable progress.
- `logs/vacuum-<name>-*.log`: per-runner debug output.

Quota/rate-limit output leaves the function pending, records a `quota` attempt,
updates the session receipt, and sleeps with capped exponential backoff unless
`--no-sleep` is used for tests.
