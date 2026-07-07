# Vacuum Architecture

Technical overview of the autonomous matching loop.

## Components

| Piece | Role |
|-------|------|
| `scripts/lib/queue-state.sh` | Load/save/move queue JSON atomically |
| `scripts/lib/queue-schema.sh` | Schema version and defaults |
| `scripts/scorer.sh` | Heuristic easiest-first ordering |
| `scripts/matcher.sh` | One-shot prompt build + response parse |
| `scripts/build-and-verify.sh` | Compile, verify, optional commit |
| `scripts/vacuum.sh` | Main orchestrator loop |
| `scripts/init-vacuum-state.sh` | Bootstrap `state/` and `logs/` |
| `scripts/decomp-cli.sh vacuum` | CLI front door |

## State Machine

```
prompts/<name>/case.yaml status
         │
         ▼
    [pending] ── scorer ──► ordered pending[]
         │
         ▼
    matcher / decomp-function (one shot)
         │
         ▼
    build-and-verify
         ├─ verify pass ──► [matched] (+ optional git commit)
         └─ verify fail ──► increment attempts
                ├─ attempts < max ──► stay [pending]
                └─ attempts ≥ max ──► [difficult]
```

Quota exhaustion triggers exponential backoff without losing queue state.

## State Files

```json
{
  "schema": "reconkit.vacuum-queue.v1",
  "pending": [{"name": "fun_x", "score": 98.8, "reason": "..."}],
  "matched": [],
  "integrated": [],
  "failed": [],
  "difficult": [],
  "attempts": {"fun_x": 2}
}
```

Writes use temp file + `mv` for atomicity.

## Token Efficiency

- Build stderr capped via `scripts/lib/build-defensive.sh` (first error + tail).
- Matcher uses fixed prompt template; no in-prompt history.
- One function per vacuum iteration by default (`--max-functions 1`).

## Signal Handling

`vacuum.sh` traps INT/TERM, persists queue + session JSON, then exits cleanly.

## Extension Points

- `--runner-command` — swap matcher backend (test stub, headless agent, API).
- `scripts/lib/scorer-ml-hooks.sh` — ML scorer hook (disabled by default).
- `--commit-after-match` — wire verified commits into the loop.

See `docs/scorer-extension.md` for ML integration notes.
