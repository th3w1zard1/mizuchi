---
title: "feat: check-log for compile-placeholder and help-command"
status: active
type: feat
created: 2026-05-30
origin: prior-session-optional-logging-gaps
---

## Summary

Close the remaining CLI logging gaps from plan 010: wire `compile-placeholder.sh` and `help-command.sh` into the shared `check-log.sh` library with verbose-by-default stderr traces and `changes:` summaries, while keeping `decomp-cli.sh` as a thin delegate that does not duplicate pipeline logging.

## Problem Frame

Plan 010 landed check-log across pipeline entry points and MCP-facing tools. Two leaf scripts still emit ad-hoc stderr only: the default compiler stub and the JSON help backend. Agents invoking them via `compile-trial.sh` or `decomp-cli.sh help` cannot see structured trace/summary blocks consistent with the rest of the toolchain.

## Scope Boundaries

### In Scope

- Integrate `check-log.sh` + `guide-manifest.sh` into `scripts/compile-placeholder.sh`
- Integrate `check-log.sh` + `guide-manifest.sh` into `scripts/help-command.sh`
- Add `--quiet` / `--help` to both scripts
- Extend `tests/test-pipeline-logging.sh` and `tests/test-help-command.sh` for trace/summary shape
- Confirm `decomp-cli.sh` remains delegate-only (no new logging layer)

### Out of Scope

- Real compiler wiring in `mizuchi.yaml`
- vacuum/matcher behavioral changes
- New decomp-cli subcommands

## Key Technical Decisions

1. **JSON on stdout, trace on stderr** — `help-command.sh` keeps machine-readable JSON on stdout; all check-log output goes to stderr so piping remains safe.
2. **Placeholder exit semantics unchanged** — still exit 1 with the same message; logging wraps without changing Mizuchi contract.
3. **decomp-cli stays thin** — delegates to underlying scripts; per-command help text only, no check-log in the router.

## Implementation Units

### U1. compile-placeholder check-log integration

**Goal:** Verbose trace when the default compiler stub runs; actionable usage error with summary token.

**Files:**
- `scripts/compile-placeholder.sh`

**Approach:** Source check-log and guide-manifest; parse `--quiet`/`--help`; log args, emit `COMPILE_PLACEHOLDER_FAIL` or `COMPILE_PLACEHOLDER_OK` summary before exit (stub always fails today except usage).

**Test scenarios:**
- Missing args: stderr contains `summary (COMPILE_PLACEHOLDER_FAIL)` and example usage
- `--quiet`: no trace lines but non-zero exit unchanged
- Two valid args: stderr includes `compile-placeholder:` message and fail summary

**Files (tests):** `tests/test-pipeline-logging.sh`

### U2. help-command check-log integration

**Goal:** Trace agent/command/MCP file reads; summary with `HELP_COMMAND_OK` before JSON stdout.

**Files:**
- `scripts/help-command.sh`

**Approach:** Source check-log; log reads of `.cursor/agents`, `.cursor/commands`, `.cursor/mcp.json`; `--quiet` suppresses trace; summary on stderr then JSON to stdout.

**Test scenarios:**
- Default run: stderr has `summary (HELP_COMMAND_OK)` and MCP server trace
- `--quiet`: JSON still valid; stderr has summary only (no `read` trace lines)
- `--help` documents `--quiet`

**Files (tests):** `tests/test-help-command.sh`, `tests/test-pipeline-logging.sh`

### U3. decomp-cli delegate audit

**Goal:** Verify router does not duplicate check-log; document delegation in a brief comment if missing.

**Files:**
- `scripts/decomp-cli.sh`

**Approach:** Read-only audit; add one-line comment near help delegate if helpful. No functional change unless a duplicate log layer is found.

**Test expectation:** none — audit-only unless duplicate logging found.

## Success Criteria

1. `./scripts/compile-placeholder.sh` emits check-log summary on stderr for all paths
2. `./scripts/help-command.sh --quiet` returns valid JSON on stdout with summary on stderr
3. `./scripts/decomp-cli.sh help` still delegates without duplicating traces
4. `./scripts/run-test-suite.sh` passes
