# Cursor-Native Bridge (without Mizuchi daemon)

When Mizuchi is not running, use workspace scripts to replicate the **compile → objdiff** loop and the AI sandbox tool **`compile_and_view_assembly`**.

## Scripts

| Script | Role |
|--------|------|
| `scripts/validate-prompt-settings.sh` | Validate `settings.yaml` (3 fields) + `prompt.md` |
| `scripts/run-programmatic-phase.sh` | Orchestrate get-context → m2c → compile/objdiff → permuter |
| `scripts/lib/queue-state.sh` | Persistent queue JSON helpers for pending/matched/integrated/failed/difficult and attempts |
| `scripts/scorer.sh` | Deterministic easiest-first prompt scorer with ML-ready metadata hooks |
| `scripts/init-vacuum-state.sh` | Initialize state/log directories, queue, scores, and session receipts from prompt manifests |
| `scripts/import-one-shot-tasks.py` | Convert one-shot-source reconstruction tasks into prompt folders with byte-slice verifier commands |
| `scripts/one-shot-task-coverage.py` | Audit one-shot package task import, candidate, queue, match, and semantic-readiness coverage |
| `scripts/vacuum.sh` | Serial autonomous orchestrator over queue/scorer/decomp-function with persistent progress logs |
| `scripts/lib/vacuum-backoff.sh` | Quota/rate-limit detection plus capped exponential backoff helpers |
| `scripts/commit-verified-match.sh` | Optional commit-after-verify helper with narrow staging and proof receipt |
| `scripts/get-context.sh` | Run `global.getContextScript` (m2ctx) into `context/` |
| `scripts/run-m2c.sh` | m2c pass → `build/m2c.c` |
| `scripts/run-permuter.sh` | decomp-permuter → `build/permuter-best.c` |
| `scripts/matcher.sh` | Build fixed one-shot prompt, run configured headless response source, parse `trial.c` |
| `scripts/lib/matcher-prompt.sh` | Deterministic one-shot prompt builder from `prompt.md`, `settings.yaml`, and matched examples |
| `scripts/lib/matcher-parse.sh` | Extract first fenced C block from a one-shot response into candidate source |
| `scripts/compile-trial.sh` | Compile C → `build/candidate.o`; objdiff if golden `.o` exists |
| `scripts/compile-and-view-assembly.sh` | Prepend `context/ctx.h`, compile, `objdump`, objdiff summary |
| `scripts/objdiff-gate.sh` | Exit 0 only when objdiff reports **0 differences** |
| `scripts/lib/verify-objdiff.sh` | Shared objdiff runner/parser; emits normalized JSON for gates and MCP wrappers |
| `scripts/lib/build-defensive.sh` | Build wrapper that keeps full logs and emits capped failure summaries for AI loops |
| `scripts/lib/prompt-settings.sh` | Shared YAML field reader (ruby or PyYAML) |
| `scripts/lib/mizuchi-config.sh` | Read `mizuchi.yaml` templates and plugin paths |
| `scripts/lib/permuter-run.py` | Permuter workdir setup (used by `run-permuter.sh`) |

## AI matching loop (Cursor agent)

One-shot mode:

```bash
./scripts/decomp-cli.sh matcher fun_00148020 --response-file /tmp/response.txt
```

For a headless runner, set `MIZUCHI_MATCHER_COMMAND` to a command that reads
`{{promptFile}}` and writes `{{responseFile}}`; `run-ai-phase.sh` will then run
`matcher.sh` and verify the produced `trial.c`.

Equivalent to Mizuchi `compile_and_view_assembly`:

```bash
./scripts/compile-and-view-assembly.sh \
  --prompt prompts/fun_00148020/ \
  --code-file /tmp/trial.c
```

Or stdin:

```bash
cat /tmp/trial.c | ./scripts/compile-and-view-assembly.sh \
  --prompt prompts/fun_00148020/ --code-stdin
```

**Agent rules during loop:**

- Allowed: Read/Glob/Grep, run scripts above, write under `prompts/<fn>/` only
- Forbidden: edit matched source tree; claim match without `diff_count: 0` / objdiff gate pass

## Verification gate

```bash
./scripts/objdiff-gate.sh build/xbox/fun_00148020.o prompts/fun_00148020/build/candidate.o
```

Success = exit code **0** and output mentioning zero differences.

For machine-readable verification, use:

```bash
./scripts/lib/verify-objdiff.sh "$TARGET_O" "$CANDIDATE_O" --out prompts/<fn>/build/verify.json
```

`build-and-verify.sh` uses the same parser and records both the full compile log
and a capped `build-and-verify.compile.summary.txt` so one-shot agents see the
first compiler error without flooding the prompt context.

## Programmatic one-liner

```bash
./scripts/run-programmatic-phase.sh --prompt prompts/fun_00148020/
```

Stops when m2c or permuter output passes `objdiff-gate.sh`.

## Autonomous Queue State

```bash
./scripts/decomp-cli.sh import-one-shot-tasks --package target/<app>/one-shot-source --prompts-dir prompts
./scripts/decomp-cli.sh one-shot-task-coverage --package target/<app>/one-shot-source --prompts-dir prompts --queue state/queue.json
./scripts/decomp-cli.sh vacuum init --queue state/queue.json --prompts-dir prompts
./scripts/decomp-cli.sh scorer --queue state/queue.json --update-queue --out state/scores.json
./scripts/decomp-cli.sh vacuum start --queue state/queue.json --max-functions 1 --timeout 30m
./scripts/decomp-cli.sh commit-verified-match --prompt prompts/<name> --dry-run
./scripts/decomp-cli.sh vacuum resume --queue state/queue.json --max-functions 1
./scripts/decomp-cli.sh vacuum reset-queue --queue state/queue.json --name <fn>
./scripts/decomp-cli.sh queue summary --queue state/queue.json
./scripts/decomp-cli.sh queue next --queue state/queue.json
```

The queue file is an orchestration artifact, not a source-of-truth replacement
for prompt `case.yaml` manifests. `vacuum init` classifies prompt manifests into
pending/matched/integrated queue states, records blocked prompts in the init
receipt, and writes scores/session files. Runtime queue state tracks retry state
for the article-style vacuum loop and can be inspected or edited independently.
Quota/rate-limit runner output leaves the function pending, records a `quota`
attempt, writes `state/vacuum-session.json`, and sleeps with capped exponential
backoff unless `--no-sleep` is used for tests. `--timeout` accepts seconds, `m`,
or `h` units and stops the loop before starting another function after the
deadline.
Commit-after-match is opt-in: pass `--commit-after-match` to `vacuum start` or
run `commit-verified-match` directly. The helper re-runs verification and stages
only the candidate, verifier receipt/logs, commit receipt, and explicit
`--path` entries.

Full operator guide: `docs/vacuum-cli.md`.

## Compiler wiring

1. Copy `mizuchi.example.yaml` → `mizuchi.yaml`
2. Replace `global.compilerScript` with your real toolchain (MSVC/clang/gcc)
3. Until then, `scripts/compile-placeholder.sh` fails by design — documents the missing bridge

`compile-trial.sh` reads `compilerScript` from `mizuchi.yaml` when present.

## Context (m2ctx)

- Default header stub: `context/ctx.h`
- Mizuchi path: `global.getContextScript` in `mizuchi.yaml`
- `compile-and-view-assembly.sh` prepends context before compile (matches Mizuchi concat behavior)

## [OPEN]

- `prompts/fun_00148020/` is locally unblocked against an asm-derived scaffold target, not an original game build object
- Real `getContextScript` / m2ctx not wired — `context/ctx.h` is a stub until configured
- `vendor/m2c` and `vendor/decomp-permuter` optional — set paths in `mizuchi.yaml` or env

## Plugin parity

Skills `decomp-programmatic-tools` and `decomp-verify-match` reference these scripts. Hook `decomp-match-claim-guard.sh` still applies on agent `stop`.
