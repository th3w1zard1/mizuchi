---
title: "feat: Proof gate, baseline hygiene, and Cycle 3 U1 queue"
status: active
type: feat
created: 2026-05-29
origin: docs/ideation/2026-05-29-mizuchi-next-steps-ideation.md
---

## Summary

Ship a bounded LFG vertical slice from ideation survivors **#5, #1, #6, and #2**: commit baseline workspace assets, enforce fail-closed prompt `matched` status, harden LFG pre-flight gates, and land Cycle 3 **Unit 1** (queue schema, scorer, `vacuum-cli.sh status|next|score`).

## Problem Frame

`prompts/fun_00148020/notes.md` claims **matched** while OPEN blockers list missing golden `.o`, compiler, and m2ctx. Cycle 3 vacuum scripts are planned but were not on main. LFG automation and queue throughput amplify dishonest status on disk.

## Scope Boundaries

### In Scope

- Baseline hygiene: `.gitignore`, `.compound-engineering/config.local.example.yaml`, ideation doc
- `validate-prompt-status.sh` + integration into `verify-workspace-surface.sh`
- Reset `fun_00148020` notes to `blocked` with remediation list
- Extend `lfg-smoke.sh` to chain surface + prompt-status checks
- Cycle 3 U1: queue state libs, scorer, init, `vacuum-cli.sh` with `status|next|score`, unit tests, minimal docs

### Out of Scope

- Full vacuum loop (U5), Claude matcher (U3), integrator commits
- Marketplace plugin submission pass
- First verified objdiff-0 match (needs toolchain/golden assets)
- Committing `.compound-engineering/config.local.yaml` (local only)

### Deferred to Follow-Up Work

- CI workflow automation for parity gates
- U2–U7 from Cycle 3 plan
- CLI parity audit (`CAPABILITY_MATRIX` ↔ `decomp-cli.sh`)

## Key Technical Decisions

- **Fail-closed matched status:** `status: matched` requires golden `targetObjectPath` on disk; if `build/candidate.o` exists, `objdiff-gate.sh` must pass when `objdiff` is on PATH
- **Pre-flight chain:** `lfg-smoke.sh` runs surface verify + prompt-status after printing smoke marker
- **U1 CLI surface:** `vacuum-cli.sh next` prints highest-scored pending function; `score` runs scorer and prints summary
- **State location:** `state/queue.json` and `state/scores.json` under repo root (gitignored)

## Implementation Units

### U1. Baseline hygiene

**Files:** `.gitignore`, `.compound-engineering/config.local.example.yaml`, `docs/ideation/2026-05-29-mizuchi-next-steps-ideation.md`

**Verification:** Files tracked; `config.local.yaml` remains gitignored.

### U2. Proof integrity gate

**Files:** `scripts/validate-prompt-status.sh`, `tests/validate_prompt_status_test.sh`, `prompts/fun_00148020/notes.md`

**Verification:** Script exits 0 on honest statuses; fails on `matched` without golden `.o`.

### U3. Pre-flight hardening

**Files:** `scripts/verify-workspace-surface.sh`, `scripts/lfg-smoke.sh`, `docs/lfg-smoke.md`, `tests/lfg_smoke_test.sh`, `tests/verify_workspace_surface_test.sh`

**Verification:** Surface check lists core script inventory; smoke test asserts chained gates pass.

### U4. Cycle 3 Unit 1 queue + scorer CLI

**Files:** `scripts/lib/queue-*.sh`, `scripts/lib/scorer-*.sh`, `scripts/scorer.sh`, `scripts/init-vacuum-state.sh`, `scripts/vacuum-cli.sh`, `tests/test-queue-state.sh`, `tests/test-scorer.sh`, `tests/test-vacuum-cli.sh`, `docs/vacuum-cli.md`, `docs/vacuum-workflow.md`, `.gitignore` (`state/`)

**Verification:** All unit tests pass; `vacuum-cli.sh status|next|score` work in temp workspace.

## Test Plan

```bash
bash tests/validate_prompt_status_test.sh
bash tests/lfg_smoke_test.sh
bash tests/verify_workspace_surface_test.sh
bash tests/test-queue-state.sh
bash tests/test-scorer.sh
bash tests/test-vacuum-cli.sh
```
