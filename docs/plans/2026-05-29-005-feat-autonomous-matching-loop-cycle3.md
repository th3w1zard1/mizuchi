---
title: "feat: Autonomous matching-decompilation loop (Cycle 3 Priority 1)"
status: planning
type: feat
created: 2026-05-29
depends_on: "2026-05-29-004-feat-agent-native-foundation-plan.md"
---

## Summary

Implement the core autonomous loop for matching-decompilation following the Chris Lewis workflow. Build scorer, queue manager, vacuum orchestrator, and defensive tooling so agents can run unattended for 8+ hours, processing functions one-shot and committing verified matches automatically.

## Problem Frame

Cycles 1-2 completed agent-native foundation (3 MCP tools, /help discovery, context injection). Agents can now understand workspace state and discover capabilities. What's missing: autonomous function selection, persistent queue management, one-shot processing loop, and defensive error handling for long-running operations.

This cycle bridges agent capabilities → autonomous orchestration by implementing the vacuum loop (continuous processor with backoff), scorer (function selection by complexity), persistent queue, and commit-after-verify workflow.

## Scope Boundaries

### In Scope (Cycle 3 Priority 1)

**Autonomous Loop Core:**
- Scorer: heuristic-based function selection (instruction count, branches, jumps, labels) + ml-ready hooks
- Vacuum orchestrator (bash driver calling Claude one-shot per function)
- Persistent function queue (pending, matched, integrated, failed, difficult)
- Attempt tracking: retry up to ~10 attempts, then mark difficult
- Backoff strategy for quota exhaustion (exponential, 5-min base interval)

**Defensive Tooling & Token Efficiency:**
- Build output limiting (truncate to stderr + first 5KB stdout on error)
- Prompt optimization for long-running: fixed context, no in-prompt history
- One-shot headless mode per function (no interactive loop)
- Commit after each verified match (prevents loss during crashes)
- Difficult function log (persistent tracking for triage)

**Operational & Monitoring:**
- Progress tracking (timestamped function log: name, status, objdiff result)
- Session state persistence (survives Claude crashes, quota hits)
- Comprehensive logging (all Claude output to debug file)

### Out of Scope (Defer to Cycle 4+)

- ML-based scorer training/integration (prepare interfaces; don't train)
- UI/dashboard for progress monitoring (text logs sufficient for now)
- Distributed agent coordination (single serial processor per workspace)
- Advanced retry strategies (exponential backoff is enough for now)
- Ghidra server integration for real-time function discovery (use static prompts/ queue)

### Deferred to Follow-Up Work

- Token efficiency: model-specific prompt templates
- Adaptive timeout tuning per function complexity
- Multi-workspace coordination
- Performance monitoring/profiling

## Key Technical Decisions

1. **Driver: bash orchestrator (vacuum.sh pattern)** — simple, debuggable, no extra runtime
2. **Scorer: heuristic first** — prepare ML hooks but run heuristic-only initially
3. **One-shot per function** — no in-prompt history; fresh context each attempt
4. **Commit after verify** — atomic units (compile → objdiff 0 → commit), prevents loss
5. **Backoff on quota** — exponential with 5-min base, max 60-min interval
6. **Difficult functions** — tracked separately for manual triage; stop after ~10 attempts
7. **Persistent state files** — JSON-based queue, easy to inspect/modify
8. **Logging: dual-channel** — human-readable progress log + debug file for detailed output

## Architecture

```
┌─ vacuum.sh (orchestrator loop) ────────────────────┐
│  1. Load queue state (JSON)                         │
│  2. Score pending functions                         │
│  3. Select next (highest score, earliest)           │
│  4. Call Claude one-shot (headless)                 │
│  5. If matched: verify → commit                     │
│  6. Update queue state                              │
│  7. Backoff on quota exhaustion                     │
│  8. Loop until queue empty or time limit            │
└─────────────────────────────────────────────────────┘
         ↓
    ┌─ matcher.sh (one-shot) ───────────────┐
    │ 1. Setup environment                  │
    │ 2. Load context (no history)          │
    │ 3. Call Claude with fixed prompt      │
    │ 4. Parse output: code + attempt log   │
    │ 5. Exit with status                   │
    └───────────────────────────────────────┘
         ↓
    ┌─ build-and-verify.sh ─────────────────┐
    │ 1. Compile (cap build output)          │
    │ 2. Run objdiff (return JSON)           │
    │ 3. Commit if verify passes             │
    │ 4. Update queue + log                  │
    └───────────────────────────────────────┘
```

**State Files:**
- `state/queue.json` — {pending, matched, integrated, failed, difficult} arrays
- `state/attempt_log.json` — {function_name, attempt_count, last_attempt, last_error}
- `logs/progress.log` — human-readable: `[2026-05-29T12:34:56] fun_00148020 MATCHED (2 attempts, 45s)`
- `logs/debug.log` — all Claude output, build logs, objdiff output

**Scorer Output:**
- `state/scores.json` — {function_name, score, reason} sorted by score desc

## System-Wide Impact

- **Agents:** decomp-function-agent invoked once per function, exits after attempt/success/failure
- **Queue:** moves through states as functions are processed; persistent across restarts
- **Build & Verify:** output capped and structured for token efficiency
- **User Experience:** progress visible in real-time log; can interrupt safely (state preserved)
- **Commits:** one commit per function (atomic, traceable, rollback-safe)

## Implementation Units

### U1. Design persistent queue schema and state management

**Goal:** Define schema for function queue and attempt tracking; implement load/save helpers.

**Requirements:**
- Queue state survives process restart
- Easy to inspect/modify by hand
- No race conditions (single-threaded orchestrator)
- Tracks attempts and failures for retry logic

**Dependencies:** None.

**Files:**
- `scripts/lib/queue-schema.sh` (schema definitions, constants)
- `scripts/lib/queue-state.sh` (load_queue, save_queue, move_function, get_next_pending)
- `tests/queue_schema_test.sh`

**Approach:**

Define JSON schema (stored in `state/queue.json`):
```json
{
  "pending": [
    {"name": "fun_00148020", "score": 45, "reason": "44 instrs, 3 branches"},
    {"name": "fun_0014a050", "score": 38, "reason": "38 instrs, 1 branch"}
  ],
  "matched": [
    {"name": "fun_00145020", "committed": "2026-05-29T10:00:00", "attempt_count": 2}
  ],
  "integrated": [],
  "failed": [
    {"name": "fun_001f0000", "reason": "objdiff mismatch after 10 attempts"}
  ],
  "difficult": [
    {"name": "fun_002a0000", "last_error": "context length exceeded"}
  ]
}
```

Implement helpers:
- `load_queue()` — parse JSON, export arrays
- `save_queue()` — write JSON atomically (write to temp, mv)
- `move_function(name, from_state, to_state)` — move entry between arrays
- `get_next_pending()` — return highest-scored pending function
- `update_attempt_count(name)` — increment attempt tracking

**Patterns to follow:**
- Existing `scripts/lib/*.sh` pattern (source-able, no main)
- Use `jq` for JSON manipulation (already in repo)
- Atomic write via temp file + mv

**Test scenarios:**
- Load empty queue (missing file)
- Load corrupted JSON (error handling)
- Move function: pending → matched
- Get next: respects score ordering
- Atomic write: no partial writes on crash
- Idempotent operations

**Verification:**
- Schema covers all function states
- Load/save round-trip preserves data
- Attempt tracking increments correctly

---

### U2. Implement scorer (heuristic-based function selection)

**Goal:** Score functions by complexity heuristic (instruction count, branch/jump count); prepare ML hooks.

**Requirements:**
- Score all pending functions deterministically
- Higher score = easier match (prioritize simple functions)
- Prepare for ML-based scoring in future cycle

**Dependencies:** U1 (queue schema).

**Files:**
- `scripts/scorer.sh` — main scorer entry point
- `scripts/lib/scorer-heuristic.sh` — heuristic scoring logic
- `scripts/lib/scorer-ml-hooks.sh` — placeholder for ML integration
- `state/scores.json` — output (cached, refreshed if prompt folder changes)
- `tests/scorer_test.sh`

**Approach:**

Implement heuristic scorer:
1. For each pending function, read `prompts/<name>/prompt.md` assembly block
2. Count instructions, branches, jumps, labels
3. Calculate score: higher = easier
   - Base: `instr_count / 10` (fewer instrs = easier)
   - Bonus: `100 - branch_count * 5` (fewer branches = easier)
   - Factor: label_density (more labels = more structure = easier)
4. Sort pending by score descending (greedy: easiest first)
5. Write `state/scores.json`

Example scoring:
```
fun_00148020: 44 instrs, 3 branches, 2 labels → score = 44/10 + (100 - 3*5) - 2 = 4.4 + 85 - 2 = 87.4
fun_0014a050: 38 instrs, 1 branch, 0 labels → score = 38/10 + (100 - 1*5) - 0 = 3.8 + 95 = 98.8 (highest)
fun_001f0000: 200 instrs, 15 branches, 1 label → score = 200/10 + (100 - 15*5) - 1 = 20 + 25 - 1 = 44 (lowest)
```

ML hooks (prepare interface, do not implement):
- `scorer_ml_predict(function_name, asm_block)` — returns score (default: call heuristic)
- Config flag: `SCORER_ML_ENABLED=false` (switch to true when trained model available)

**Patterns to follow:**
- `scripts/lib/scorer-*.sh` source-able
- Read assembly from prompt.md only (no Ghidra round-trip)
- Cache `state/scores.json` between runs; invalidate if any prompts change

**Test scenarios:**
- Score single function correctly
- Score multiple functions, verify ordering
- Empty pending queue (no crash)
- Prompt folder changes, cache invalidates
- ML hooks prepare but don't crash when called

**Verification:**
- Scores align with function complexity
- Easiest functions scored highest (processed first)
- Scoring deterministic (same input = same output)
- Cache works and invalidates correctly

---

### U3. Implement one-shot matcher (headless Claude invocation)

**Goal:** Invoke Claude once per function with fixed context; return code + attempt info; no interactive loop.

**Requirements:**
- Fixed prompt structure (no in-prompt history)
- Parse Claude output: extract code block, detect attempt status
- Exit cleanly with structured result
- Token-efficient: capped build output

**Dependencies:** U1, U2 (queue state + scoring context).

**Files:**
- `scripts/matcher.sh` — main entry point
- `scripts/lib/matcher-prompt.sh` — prompt template + context building
- `scripts/lib/matcher-parse.sh` — parse Claude output
- `tests/matcher_test.sh`

**Approach:**

Design fixed prompt template (stored in `scripts/lib/matcher-prompt.sh`):

```
You are matching a decompiled function from a binary. Your task: write C code that compiles to identical object code.

## Function Context
[Binary name, address, calling convention from prompt.md]

## Assembly Block
[Read from prompts/<name>/prompt.md, assembly section]

## Build & Verify Loop
You have ONE SHOT to match this function. Do not iterate.

1. Write C code that replicates the assembly behavior.
2. Keep code simple and direct; avoid over-optimization.
3. Match the exact register operations and calling convention.

## Constraints
- No interactive feedback loop
- One attempt only
- If matching seems impossible, explain why in a comment

## Output Format
Return ONLY valid C code in a single code block. Example:

\`\`\`c
void fun_00148020() {
  // Your matching implementation
}
\`\`\`

## Examples of Success
Previous matches in this binary:
[List 2-3 similar functions that were matched, from docs/atlas/ or similar-functions.md if available]

Go.
```

Implement matcher.sh:
1. Parse `--prompt <name>` argument
2. Build prompt: read assembly, inject context, examples
3. Call Claude (one-shot headless mode)
4. Parse output: extract code block
5. Write code to `prompts/<name>/trial.c`
6. Return status: success/parse_error/empty_response/timeout

**Patterns to follow:**
- Read from `prompts/<name>/prompt.md` for assembly
- Use Cursor native CLI or Claude API (project preference)
- No in-prompt history (always fresh context)
- Parse code block robustly (handle variations)

**Test scenarios:**
- Parse valid C code block
- Handle malformed code block (error response)
- Handle timeout (exit with error)
- Inject context correctly (function address, calling convention visible in prompt)
- Write trial.c to correct location

**Verification:**
- Matcher produces trial.c with valid C syntax
- Prompt context includes assembly and examples
- No interactive loop (headless mode confirmed)
- Handles malformed responses gracefully

---

### U4. Implement build-and-verify pipeline (defensive output limiting)

**Goal:** Compile code, run objdiff, commit if verified; cap output for token efficiency.

**Requirements:**
- Compile trial.c to object file
- Run objdiff, parse results
- Commit if objdiff returns 0
- Cap build output (first error + last 5KB stdout on failure)
- Structured error messages ("BUILD FAILED. Claude, treat this as...")

**Dependencies:** U1, U3 (queue state + trial.c from matcher).

**Files:**
- `scripts/build-and-verify.sh` — main orchestration
- `scripts/lib/build-defensive.sh` — compile with output limiting
- `scripts/lib/verify-objdiff.sh` — run objdiff, parse result
- `tests/build_and_verify_test.sh`

**Approach:**

Compile defensively:
1. Run compile command (e.g., `gcc -c trial.c`)
2. Capture stderr and stdout separately
3. On error: keep first error line + last 5KB of stderr; append "BUILD FAILED" marker
4. On success: run objdiff

Run objdiff:
1. Compare `prompts/<name>/build/candidate.o` vs target.o
2. Parse result: differences count
3. Return JSON: `{"status": "matched", "differences": 0}` or `{"status": "mismatched", "differences": N}`

Commit if verified:
1. If objdiff returns differences=0
2. Write `.verified.json` with verification timestamp
3. Git commit: `git add -A; git commit -m "Match: fun_00148020 (verified, 2 attempts)"`
4. Move function from pending → matched in queue
5. Log: `[timestamp] fun_00148020 MATCHED (2 attempts)`

**Output Limiting Strategy:**
```
# On compile success:
✅ Build succeeded: fun_00148020 (trial.c → build/candidate.o)

# On compile error:
❌ Build failed: fun_00148020
First error: /path/to/trial.c:42: error: undefined reference to `xyz'
[... last 5KB of stderr ...]
→ Treat as failed attempt; will retry or mark difficult
```

**Patterns to follow:**
- Reuse `scripts/objdiff-gate.sh` (don't reinvent verification)
- Atomic writes (temp file + mv for JSON)
- Clear error messages for Claude (if human reviews logs)

**Test scenarios:**
- Successful compile + objdiff 0 → commit
- Compile error → cap output, return error status
- Objdiff mismatch → log, move to failed attempt
- Commit with structured message
- Output capping: verify first error visible, last 5KB included

**Verification:**
- Verified matches committed with clear message
- Failed attempts tracked without committing
- Build output capped (no token waste)
- Error messages clear enough for triage

---

### U5. Implement vacuum loop orchestrator (autonomous processor)

**Goal:** Main loop that selects functions, runs matcher, builds/verifies, handles backoff, repeats.

**Requirements:**
- Run autonomously for 8+ hours
- Handle quota exhaustion (backoff exponential)
- Persist state across interrupts (Ctrl-C trap)
- Track attempts; mark difficult after ~10 attempts
- Visible progress log

**Dependencies:** U1-U4 (all components).

**Files:**
- `scripts/vacuum.sh` — main orchestrator
- `scripts/lib/vacuum-backoff.sh` — exponential backoff logic
- `scripts/lib/vacuum-state.sh` — session persistence
- `tests/vacuum_test.sh`
- `docs/vacuum-workflow.md` — usage guide

**Approach:**

Main loop (pseudocode):
```bash
while true; do
  load_queue
  next=$(get_next_pending)
  if [ -z "$next" ]; then
    log "Queue empty or all difficult; exiting"
    break
  fi

  log "[start] $next (attempt $(get_attempt_count $next))"

  # One-shot match
  matcher.sh --prompt "$next" || {
    log "[error] Matcher failed for $next"
    continue_on_error
  }

  # Build and verify
  build-and-verify.sh --prompt "$next" && {
    log "[matched] $next (committed)"
    move_function "$next" matched
    reset_attempt_count "$next"
  } || {
    count=$(increment_attempt_count "$next")
    if [ "$count" -ge 10 ]; then
      log "[difficult] $next (10+ attempts, marking for triage)"
      move_function "$next" difficult
    else
      log "[retry] $next (attempt $count, will retry)"
    fi
  }

  save_queue
done
```

Backoff on quota exhaustion:
1. Detect: Claude returns 429 or timeout
2. Calculate: backoff_seconds = min(5 * 2^retry_count, 3600) (5s, 10s, 20s, ..., 60min)
3. Wait and retry: sleep, then loop
4. Log: `[backoff] Quota hit; waiting 300s before retry`
5. Max interval: 60 minutes (then give up that session)

Session persistence:
- Save queue state before each loop iteration
- Trap Ctrl-C, SIGTERM: save state and exit cleanly
- On resume: load saved queue and continue from next pending

Progress log:
```
[2026-05-29T12:34:56] Starting vacuum loop
[2026-05-29T12:35:02] [start] fun_00148020 (attempt 1)
[2026-05-29T12:35:47] [matched] fun_00148020 (committed)
[2026-05-29T12:35:48] [start] fun_0014a050 (attempt 1)
[2026-05-29T12:36:15] [retry] fun_0014a050 (attempt 1, objdiff mismatch)
[2026-05-29T12:36:16] [start] fun_0014a050 (attempt 2)
[2026-05-29T12:37:02] [matched] fun_0014a050 (committed)
[2026-05-29T12:37:03] [backoff] Quota hit; waiting 300s before retry
[2026-05-29T12:42:03] [resume] Quota recovered; continuing
...
```

**Patterns to follow:**
- Trap signals for graceful shutdown
- Use timestamp prefixes for all log lines
- Atomic queue updates (load, modify, save)
- Exponential backoff with sensible max (60 min)

**Test scenarios:**
- Process single function: pending → matched → commit
- Process multiple: verify ordering by score
- Simulate quota hit: backoff triggers
- Interrupt (Ctrl-C): state preserved, resume works
- Difficult function: after 10 attempts, marked difficult
- Progress log visible in real-time

**Verification:**
- Vacuum loop runs unattended for 8+ hours
- Functions process in score order
- Backoff works correctly on quota
- State persists across restarts
- Difficult functions tracked for manual triage

---

### U6. Implement initialization and CLI entry point

**Goal:** Set up workspace for vacuum loop; provide user-friendly CLI to start/resume/inspect.

**Requirements:**
- Create state/ and logs/ directories
- Initialize queue from prompts/ folder
- Provide commands: start, resume, status, inspect-queue

**Dependencies:** U1-U5.

**Files:**
- `scripts/init-vacuum-state.sh` — initialize state/ and queue.json
- `scripts/vacuum-cli.sh` — CLI entry point (start/resume/status/inspect)
- `tests/init_vacuum_test.sh`
- `docs/vacuum-cli.md` — user guide

**Approach:**

`init-vacuum-state.sh`:
1. Create state/, logs/ directories
2. Scan prompts/ folder
3. For each prompt, check status (matched/integrated/failed/difficult)
4. Initialize queue.json with all prompts in appropriate state
5. Initialize scores.json (run scorer)
6. Log: `Queue initialized: N pending, M matched, ...`

`vacuum-cli.sh` with subcommands:
```bash
./scripts/vacuum-cli.sh start [--timeout 8h] [--max-attempts 10]
./scripts/vacuum-cli.sh resume
./scripts/vacuum-cli.sh status
./scripts/vacuum-cli.sh inspect-queue
./scripts/vacuum-cli.sh reset-queue [--function name]
```

- `start`: Initialize state, then call vacuum.sh
- `resume`: Skip init, call vacuum.sh (loads saved state)
- `status`: Show counts (pending, matched, integrated, failed, difficult); recent activity
- `inspect-queue`: Dump queue.json in readable format
- `reset-queue`: Move function from failed/difficult back to pending

**Patterns to follow:**
- Existing CLI script conventions in repo
- Clear help text for each command
- Sensible defaults (timeout 8h, max-attempts 10)

**Test scenarios:**
- Init creates state/ and queue.json
- Start initializes and runs vacuum
- Resume skips init, uses saved state
- Status shows accurate counts
- Inspect shows readable queue

**Verification:**
- User can run one command to start autonomous loop
- Status visible without deep debugging
- Queue can be inspected and manually adjusted

---

### U7. Create comprehensive documentation and test suite

**Goal:** Document workflow, provide runnable tests, ensure maintainability.

**Requirements:**
- User guide for running vacuum loop
- Developer guide for extending scorer/matcher
- Test coverage for all components
- Integration test (simulate end-to-end function match)

**Dependencies:** U1-U6.

**Files:**
- `docs/vacuum-workflow.md` — full user guide
- `docs/vacuum-architecture.md` — technical deep-dive
- `docs/scorer-extension.md` — how to add ML-based scoring
- `tests/vacuum_integration_test.sh` — end-to-end test
- `tests/README.md` — test running guide

**Approach:**

`vacuum-workflow.md`:
- Quick start: `./scripts/vacuum-cli.sh start`
- What happens: loop explanation, backoff strategy
- Monitoring: how to read logs, inspect queue
- Troubleshooting: common issues (quota, build errors, difficult functions)

`vacuum-architecture.md`:
- Design overview (ASCII diagram)
- State machine (pending → matched/failed → difficult)
- Backoff strategy details
- Token efficiency (output capping)

`scorer-extension.md`:
- Current heuristic algorithm
- ML-ready hooks (interface, config)
- How to train model in separate cycle
- Integration steps

`vacuum_integration_test.sh`:
- Create mock prompts/ with sample functions
- Run vacuum.sh for 1 function
- Verify: matcher called, build succeeded, objdiff passed, commit made
- Clean up state

**Patterns to follow:**
- Existing docs format in repo
- Runnable test commands
- Clear examples

**Test scenarios:**
- Integration: mock function → matched → committed
- All unit tests pass
- Docs commands runnable (e.g., initialization steps)

**Verification:**
- User can follow docs and run vacuum loop successfully
- Tests all pass locally
- Developer can understand and extend scorer/matcher

---

## Risks & Dependencies

| Risk | Mitigation |
|------|-----------|
| Claude quota exhaustion during long run | Exponential backoff + graceful persist on SIGTERM. User can resume after quota recovery. |
| Build output explosion (token waste) | Cap output to first error + last 5KB. Measure token cost before/after. |
| Function stuck in retry loop (attempt count not incremented) | Atomic attempt tracking; verify increment works in unit test. |
| State file corruption (half-written JSON during crash) | Atomic write (temp file + mv). Trap signals for clean shutdown. |
| Progress log grows unbounded | Implement log rotation (cycle 4+); for now, logs are append-only. |
| Difficult functions never triaged (stuck in queue) | Track in separate state; user can manually reset via `vacuum-cli.sh reset-queue`. |
| Scorer heuristic too aggressive or too conservative | A/B test heuristic vs. sample of actual matches in cycle 4. Adjust weights if needed. |
| ML hook breaks existing heuristic scorer | ML hooks are no-op initially (config flag); easy to disable if needed. |

## Deferred Implementation Notes

- **Scorer ML training**: Cycle 4, after collecting ~50 matched functions
- **Adaptive timeout**: Per-function timeout based on complexity (cycle 4)
- **Performance tuning**: Profile queue state operations once running (cycle 4+)
- **Log rotation**: Implement in cycle 4 if logs exceed size threshold
- **Multi-workspace coordination**: Out of scope; single workspace per user for now

## Verification Checklist

- [ ] **U1 Queue Schema:** Queue state survives restart; load/save atomic
- [ ] **U2 Scorer:** Functions scored by complexity; score order preserved
- [ ] **U3 Matcher:** One-shot Claude invocation; code block parsed correctly
- [ ] **U4 Build & Verify:** Build output capped; verified matches committed
- [ ] **U5 Vacuum Loop:** Runs autonomously; backoff works; state persists
- [ ] **U6 CLI:** User can start/resume/inspect with clear CLI
- [ ] **U7 Tests & Docs:** All units tested; docs runnable
- [ ] **Integration Test:** End-to-end mock function match succeeds
- [ ] **Manual Smoke Test:** Run vacuum on real prompts for 30 min; verify progress log + queue state

## Acceptance Criteria for Cycle 3 Complete

1. **Autonomous Loop Functional:**
   - Can run unattended for at least 1 hour on real prompts/
   - Processes functions in score order
   - Commits verified matches
   - Tracks attempts and difficult functions

2. **Defensive & Observable:**
   - Build output capped (no token waste visible in logs)
   - Progress log visible in real-time
   - Clear error messages on failure
   - State persists and resumes correctly

3. **Tested & Documented:**
   - All units have passing tests
   - Integration test passes (mock function match)
   - User can follow vacuum-cli.md to start loop
   - Developer can understand scorer/matcher for extensions

4. **Ready for Scaling (Cycle 4):**
   - ML scorer hooks prepared (no implementation yet)
   - Log rotation prepared (not implemented)
   - Performance profiling data collected (optional)

## See Also

- Chris Lewis blog: https://blog.chrislewis.au/the-unexpected-effectiveness-of-one-shot-decompilation-with-claude/
- Macabeus benchmark: https://macabeus.medium.com/can-llms-really-do-matching-decompilation-i-tested-60-functions-to-find-out-4e39b0ae4288
- Cycles 1-2 plans: `docs/plans/2026-05-29-004-feat-agent-native-foundation-plan.md`
- Current repo status: Branch `feat/lfg-remote-pipeline-complete`, cycles 1-2 implementation merged
