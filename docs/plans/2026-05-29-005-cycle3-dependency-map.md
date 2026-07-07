---
title: "Cycle 3 Dependency & Execution Map"
description: "Visual dependency graph, testing strategy, and one-turn execution checklist"
created: 2026-05-29
---

# Cycle 3 Dependency & Execution Map

This document provides a visual dependency graph, testing strategy per phase, and a one-turn execution checklist for Cycle 3 implementation.

## Dependency Graph

```
┌─────────────────────────────────────────────────────────────────────┐
│ U1: Persistent Queue Schema & State Management                      │
│ (base layer: JSON schema, atomic load/save)                         │
└────────────────┬────────────────────────────────────────────────────┘
                 │
        ┌────────┴────────┬──────────────────┬──────────────────┐
        ↓                 ↓                  ↓                  ↓
┌──────────────┐  ┌──────────────┐  ┌───────────────┐  ┌──────────────┐
│ U2: Scorer   │  │ U3: Matcher  │  │ U4: Build &   │  │ U5: Vacuum   │
│ (heuristic + │  │ (one-shot    │  │ Verify        │  │ (orchestrator)
│ ML hooks)    │  │ Claude call) │  │ (compile, obj │  │              │
└──────┬───────┘  └──────┬───────┘  │ diff, commit) │  └──────┬───────┘
       │                 │          └───────┬───────┘        │
       │                 │                  │                │
       └─────────────────┴──────────────────┴────────────────┘
                        ↓
                ┌───────────────────┐
                │ U6: CLI & Init    │
                │ (vacuum-cli.sh)   │
                └─────────┬─────────┘
                          ↓
                ┌───────────────────┐
                │ U7: Tests & Docs  │
                │ (integration test)│
                └───────────────────┘
```

## Testing Strategy by Phase

### Phase 1: Foundation (U1)

**Goal:** Verify queue state is atomic and round-trips correctly.

```bash
# Unit test: queue_schema_test.sh
scripts/lib/queue-state.sh (load_queue, save_queue, move_function, get_next_pending)

Test matrix:
  - Load missing queue.json (create empty)
  - Load valid queue (correct structure)
  - Load corrupted JSON (error handling)
  - Save queue atomically (temp → mv)
  - Move function: pending → matched (verify JSON updated)
  - Get next: respects score order (sorted pending list)
  - Multiple round-trips: no data loss

Expected: All tests pass, queue state survives restart
```

**Acceptance:**

- ✅ `load_queue` returns correct array sizes
- ✅ `save_queue` writes atomic JSON
- ✅ `move_function` updates queue correctly
- ✅ Round-trip: load → modify → save → load yields same data

---

### Phase 2: Scorer (U2)

**Goal:** Verify functions scored by complexity; easiest processed first.

```bash
# Unit test: scorer_test.sh
scripts/scorer.sh

Test matrix:
  - Score single function (instruction count, branches, labels)
  - Score multiple functions (verify ordering, highest score first)
  - Empty pending queue (no crash)
  - Prompt folder changes (cache invalidates)
  - Scorer output JSON parseable

Mock assembly samples:
  - Simple (10 instrs, 0 branches) → high score (~95+)
  - Medium (50 instrs, 5 branches) → mid score (~50-70)
  - Complex (200+ instrs, 20+ branches) → low score (<30)

Expected: Scores correlate with complexity; easiest first
```

**Acceptance:**

- ✅ `scorer.sh` generates valid JSON
- ✅ Score order correct (high → low)
- ✅ Heuristic weights produce reasonable ordering
- ✅ ML hooks callable without breaking heuristic

---

### Phase 3: Matcher (U3)

**Goal:** Verify one-shot Claude invocation; code block parsing.

```bash
# Unit test: matcher_test.sh
scripts/matcher.sh --prompt <mock_prompt>

Test matrix:
  - Call Claude with valid prompt (parse response)
  - Parse valid C code block (extract code)
  - Parse malformed code block (error handling)
  - Parse empty response (timeout)
  - Write trial.c to correct location
  - Context injection (function address visible in prompt)

Mock responses:
  - Valid: "```c\nvoid fun() { ... }\n```"
  - Malformed: "```c\nvoid fun() { ... " (missing closing)
  - Empty: "" or timeout

Expected: Matcher produces trial.c with valid C syntax
```

**Acceptance:**

- ✅ `matcher.sh` produces trial.c in correct location
- ✅ Code block parsing handles variations
- ✅ Error responses don't crash (graceful exit)
- ✅ Prompt context includes assembly and examples

---

### Phase 4: Build & Verify (U4)

**Goal:** Verify compile + objdiff + commit flow; output capping.

```bash
# Unit test: build_and_verify_test.sh
scripts/build-and-verify.sh --prompt <mock_prompt>

Test matrix:
  - Successful compile + objdiff 0 → commit (verify git commit made)
  - Compile error → cap output (first error + last 5KB stderr)
  - Objdiff mismatch → no commit (failed attempt tracked)
  - Output limiting: verify token cost (measure stderr size)
  - Atomic commit: git add/commit complete

Expected: Verified matches committed; failed attempts tracked
```

**Acceptance:**

- ✅ Build succeeds: git commit present, queue moved to matched
- ✅ Build fails: error capped, queue moved to failed, attempt count incremented
- ✅ Objdiff mismatch: no commit, attempt tracked
- ✅ Output size capped <10KB per failure

---

### Phase 5: Vacuum Loop (U5)

**Goal:** Verify autonomous processor loop; backoff; state persistence.

```bash
# Unit test: vacuum_test.sh (mock/short timeout)
# Integration test: vacuum_integration_test.sh (mock prompt end-to-end)

scripts/vacuum.sh [--timeout 5m] [--max-attempts 3]

Test matrix (unit):
  - Load queue (empty, single pending, multiple pending)
  - Select next: respects score order
  - Call matcher → build → verify pipeline
  - Attempt counting: increment on failure, reset on success
  - Difficult function: mark after N attempts (test with N=3)
  - Backoff: simulate 429, verify sleep + retry
  - Signal trap: Ctrl-C, verify state saved + graceful exit
  - Progress log: timestamps, function names, status visible

Integration test (mock):
  - Create mock prompts/ with 2 sample functions
  - Initialize queue
  - Run vacuum.sh for 5 min (--timeout 5m)
  - Verify: matcher called, build ran, objdiff checked
  - Verify: one or more functions processed
  - Verify: state files (queue.json, progress.log) updated
  - Clean up

Expected: Vacuum loop runs autonomously; state persists
```

**Acceptance:**

- ✅ Loop processes pending functions in score order
- ✅ Backoff triggers on 429 (mock/simulate)
- ✅ State survives Ctrl-C + resume
- ✅ Attempt tracking accurate (increment/reset)
- ✅ Integration test: at least one function processed correctly

---

### Phase 6: CLI & Init (U6)

**Goal:** Verify user-facing CLI works; initialization correct.

```bash
# Unit test: init_vacuum_test.sh
scripts/init-vacuum-state.sh
scripts/vacuum-cli.sh <subcommand>

Test matrix (init):
  - Create state/, logs/ directories
  - Scan prompts/ and initialize queue.json
  - Status counts accurate (N pending, M matched, ...)
  - Scorer runs during init (scores.json generated)

Test matrix (CLI):
  - `vacuum-cli.sh start` initializes and runs vacuum
  - `vacuum-cli.sh resume` skips init (loads saved state)
  - `vacuum-cli.sh status` shows counts and recent activity
  - `vacuum-cli.sh inspect-queue` dumps queue in readable format
  - `vacuum-cli.sh reset-queue --function <name>` moves function back to pending
  - Help text present for each command

Expected: User can run CLI commands successfully
```

**Acceptance:**

- ✅ `init-vacuum-state.sh` creates state/ and queue.json
- ✅ `vacuum-cli.sh start` initializes and enters vacuum loop
- ✅ `vacuum-cli.sh status` shows accurate counts
- ✅ All CLI subcommands have help text
- ✅ Resume works after state saved

---

### Phase 7: Tests & Docs (U7)

**Goal:** Comprehensive test coverage; user and developer documentation.

```bash
# Integration test: vacuum_integration_test.sh
# All unit tests: tests/vacuum_*_test.sh

Test matrix:
  - Run all unit tests (U1-U6)
  - Run integration test (end-to-end mock function)
  - Verify docs: commands runnable (e.g., init steps)
  - Verify docs: examples accurate

Expected: All tests pass; docs work
```

**Acceptance:**

- ✅ All tests pass locally (`./tests/vacuum_*_test.sh`)
- ✅ Integration test: mock function match succeeds
- ✅ Docs include working examples
- ✅ User can follow vacuum-workflow.md to start loop

---

## One-Turn Execution Checklist

Use this checklist to execute Cycle 3 in a single coherent pass:

### Pre-Flight (5 min)

- [ ] Read this dependency map + cycle 3 plan fully
- [ ] Verify `feat/lfg-remote-pipeline-complete` branch is current
- [ ] Check baseline: cycles 1-2 artifacts (MCP tools, CAPABILITY_MATRIX.md) present and working
- [ ] Plan which units to implement in order (U1 → U2 → ... → U7)

### Unit 1: Queue Schema (30-45 min)

- [ ] Create `scripts/lib/queue-schema.sh` (constants, schema reference)
- [ ] Create `scripts/lib/queue-state.sh` (load_queue, save_queue, move_function, etc.)
- [ ] Create `tests/queue_schema_test.sh`
- [ ] Run tests locally; verify all pass
- [ ] Commit: `feat(u1-vacuum): Add persistent queue schema and state management`

### Unit 2: Scorer (45-60 min)

- [ ] Create `scripts/scorer.sh` (main entry point)
- [ ] Create `scripts/lib/scorer-heuristic.sh` (complexity heuristic)
- [ ] Create `scripts/lib/scorer-ml-hooks.sh` (ML interface, no-op for now)
- [ ] Create `tests/scorer_test.sh`
- [ ] Run tests locally; verify scoring order correct
- [ ] Commit: `feat(u2-vacuum): Add heuristic scorer with ML hooks`

### Unit 3: Matcher (60-90 min)

- [ ] Create `scripts/lib/matcher-prompt.sh` (fixed prompt template)
- [ ] Create `scripts/lib/matcher-parse.sh` (parse Claude output)
- [ ] Create `scripts/matcher.sh` (main entry point, headless invocation)
- [ ] Create `tests/matcher_test.sh`
- [ ] Run tests locally; verify code block parsing
- [ ] Commit: `feat(u3-vacuum): Add one-shot matcher with Claude headless invocation`

### Unit 4: Build & Verify (60-90 min)

- [ ] Create `scripts/lib/build-defensive.sh` (compile with output limiting)
- [ ] Create `scripts/lib/verify-objdiff.sh` (run objdiff, parse result)
- [ ] Create `scripts/build-and-verify.sh` (orchestrate build → verify → commit)
- [ ] Create `tests/build_and_verify_test.sh`
- [ ] Run tests locally; verify output capping works
- [ ] Commit: `feat(u4-vacuum): Add build-and-verify with defensive output limiting`

### Unit 5: Vacuum Loop (90-120 min)

- [ ] Create `scripts/lib/vacuum-backoff.sh` (exponential backoff logic)
- [ ] Create `scripts/lib/vacuum-state.sh` (session persistence, signal traps)
- [ ] Create `scripts/vacuum.sh` (main orchestrator loop)
- [ ] Create `tests/vacuum_test.sh` (unit tests: loop logic, attempt tracking, backoff)
- [ ] Create `tests/vacuum_integration_test.sh` (mock prompt end-to-end)
- [ ] Run tests locally; verify loop processes functions correctly
- [ ] Run integration test; verify 1+ function processed
- [ ] Commit: `feat(u5-vacuum): Add vacuum loop orchestrator with backoff and persistence`

### Unit 6: CLI & Init (30-45 min)

- [ ] Create `scripts/init-vacuum-state.sh` (directory creation, queue init)
- [ ] Create `scripts/vacuum-cli.sh` (start, resume, status, inspect-queue subcommands)
- [ ] Create `tests/init_vacuum_test.sh`
- [ ] Run tests locally; verify CLI works
- [ ] Commit: `feat(u6-vacuum): Add CLI and initialization for vacuum loop`

### Unit 7: Tests & Docs (60-90 min)

- [ ] Create `docs/vacuum-workflow.md` (user guide: quick start, monitoring, troubleshooting)
- [ ] Create `docs/vacuum-architecture.md` (technical deep-dive: design, backoff, token efficiency)
- [ ] Create `docs/scorer-extension.md` (how to add ML-based scoring in cycle 4)
- [ ] Verify all docs include working examples
- [ ] Update `tests/README.md` with Cycle 3 test running instructions
- [ ] Run full test suite: `./tests/vacuum_*_test.sh` (all should pass)
- [ ] Commit: `feat(u7-vacuum): Add comprehensive tests and documentation`

### Integration & Smoke Testing (30-60 min)

- [ ] Run full integration test: `tests/vacuum_integration_test.sh`
- [ ] Expected: one or more mock functions processed correctly
- [ ] Manual smoke test on real prompts/ (if available): run `vacuum-cli.sh start --timeout 10m`
- [ ] Expected: progress log visible, at least one function processed
- [ ] Verify git commits made for matched functions
- [ ] Commit: `chore(vacuum): Integration testing complete`

### Documentation & Handoff (15-30 min)

- [ ] Review all docs for clarity and accuracy
- [ ] Update STRATEGY.md with Cycle 3 achievement
- [ ] Create checkpoint summary: what was built, what's next (Cycle 4)
- [ ] Commit: `docs(cycle3): Checkpoint and handoff summary`

### Final Verification (10-15 min)

- [ ] Branch ready for PR: all tests pass, all commits clean, docs complete
- [ ] Verify no stale WIP branches or debug files
- [ ] Create PR title: `feat(cycle3): Implement autonomous matching-decompilation loop`
- [ ] PR description: links to plan, cycle achievements, next steps (Cycle 4)

---

## Time Estimation

| Unit | Estimate | Actual |
|------|----------|--------|
| U1 Queue Schema | 30-45 min | — |
| U2 Scorer | 45-60 min | — |
| U3 Matcher | 60-90 min | — |
| U4 Build & Verify | 60-90 min | — |
| U5 Vacuum Loop | 90-120 min | — |
| U6 CLI & Init | 30-45 min | — |
| U7 Tests & Docs | 60-90 min | — |
| Integration & Smoke | 30-60 min | — |
| Docs & Handoff | 15-30 min | — |
| **Total** | **7-8 hours** | — |

**Target:** Single-day cycle (one focused work session).

---

## Risk Checkpoints

- **After U1:** Queue state atomic? Can recover from partial writes?
- **After U2:** Scorer weights reasonable? Easiest functions prioritized?
- **After U3:** Matcher prompt fixed? Code block parsing robust?
- **After U4:** Build output capped correctly? Commit messages clear?
- **After U5:** Vacuum loop stable for 30+ min? Backoff working?
- **After U6:** CLI intuitive? Help text clear?
- **After U7:** All tests passing? Docs runnable?

---

## Rollback Plan

If any unit fails:

1. Revert last commit: `git reset --hard HEAD~1`
2. Investigate test failure
3. Fix in current branch (add new commit)
4. Re-run test locally
5. Once passing, continue to next unit

**If full cycle blocked:** Revert to `main`, open issue for cycle 4, preserve learnings in docs/

---

## Appendix: Test Command Examples

```bash
# Unit tests (run individually or all at once)
./tests/queue_schema_test.sh
./tests/scorer_test.sh
./tests/matcher_test.sh
./tests/build_and_verify_test.sh
./tests/vacuum_test.sh
./tests/init_vacuum_test.sh

# All unit tests
for t in ./tests/vacuum_*_test.sh; do bash "$t" || exit 1; done

# Integration test
./tests/vacuum_integration_test.sh

# Full suite
./tests/README.md  # follow instructions
```

---

## See Also

- Cycle 3 plan: `docs/plans/2026-05-29-005-feat-autonomous-matching-loop-cycle3.md`
- Cycles 1-2 complete: `feat/lfg-remote-pipeline-complete` branch
- Chris Lewis blog: <https://blog.chrislewis.au/the-unexpected-effectiveness-of-one-shot-decompilation-with-claude/>
