---
title: "feat: agent-friendly CLI logging for pipeline scripts"
status: active
type: feat
created: 2026-05-29
origin: user-request-cli-for-agents-logging
---

## Summary

Extend verbose-by-default logging and guide-manifest defaults from validation scripts to the decomp pipeline CLI surface. Add agent-friendly `--help` with copy-paste examples, actionable missing-arg errors, and end-of-run change summaries listing every file/server touched.

## Problem Frame

Plan 009 hardened validators but pipeline entry points (`decomp-cli.sh`, `run-programmatic-phase.sh`, `objdiff-gate.sh`, `get-context.sh`) still use ad-hoc echo logging, sparse errors (`missing target`), and no consolidated change block. Agents cannot see which MCP servers, output folders, or file mutations occurred without reading source.

## Scope Boundaries

### In Scope

- Extend `check-log.sh` with change tracking and summary `changes:` block
- Extend `guide-manifest.sh` with default output dirs and pipeline script registry
- New `scripts/lib/cli-agent.sh` for layered help and actionable errors
- Refactor `decomp-cli.sh` for per-command help and examples
- Integrate check-log into pipeline scripts: `run-programmatic-phase.sh`, `objdiff-gate.sh`, `get-context.sh`
- Tests for change summary, CLI help examples, and pipeline trace output

### Out of Scope

- vacuum/matcher/scorer loop redesign
- Changing objdiff pass/fail heuristics

## Implementation Units

### U1. Extend check-log and guide-manifest

**Goal:** Track file ops in a summary block; centralize output folder defaults.

**Files:**
- `scripts/lib/check-log.sh`
- `scripts/lib/guide-manifest.sh`

**Approach:** `check_log_file_op` records op+path in `CHECK_LOG_CHANGES[]`; summary prints `changes:` section. Manifest adds `GUIDE_OUTPUT_DIRS`, `GUIDE_PIPELINE_SCRIPTS`, helper `guide_manifest_rel`.

**Test scenarios:**
- `check_log_file_op` entries appear under `changes:` in summary
- Manifest exports expected output dirs (prompts/, context/, build/)

**Files (tests):** `tests/test-check-log.sh`

### U2. Agent CLI helpers and decomp-cli refactor

**Goal:** Non-interactive, discoverable CLI with examples on every subcommand help.

**Files:**
- `scripts/lib/cli-agent.sh`
- `scripts/decomp-cli.sh`

**Approach:** `cli_agent_usage`, `cli_agent_missing_arg`, `cli_agent_unknown_command`; support `decomp-cli.sh help <cmd>` and `--help` on subcommands; every error includes a correct example invocation.

**Test scenarios:**
- `decomp-cli.sh help decomp-function` includes Examples section
- Missing args print example line and exit non-zero

**Files (tests):** `tests/test-decomp-cli.sh`

### U3. Pipeline script logging integration

**Goal:** Verbose trace for programmatic phase, objdiff gate, and get-context.

**Files:**
- `scripts/run-programmatic-phase.sh`
- `scripts/objdiff-gate.sh`
- `scripts/get-context.sh`

**Approach:** Source check-log; log each phase step, file reads/writes, objdiff targets; `--quiet` opt-out; summary before success token.

**Test scenarios:**
- Default run prints phase trace on stderr
- `--quiet` suppresses trace but keeps exit code

**Files (tests):** `tests/test-pipeline-logging.sh` (new)

## Success Criteria

1. `./scripts/decomp-cli.sh help run-objdiff` shows Examples with real paths
2. `./scripts/run-programmatic-phase.sh --help` documents `--quiet`
3. Pipeline scripts emit `changes:` block when files are created/written
4. `./scripts/run-test-suite.sh` passes
