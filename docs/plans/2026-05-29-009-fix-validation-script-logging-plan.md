---
title: "fix: validation script logging and guide manifest defaults"
status: completed
type: fix
created: 2026-05-29
origin: user-request-validation-logging
---

## Summary

Make Mizuchi validation and audit shell scripts verbose by default: log every MCP server, file read, grep probe, and file mutation, then emit a structured end-of-run summary. Centralize guide coverage paths in one manifest so callers never pass folder lists manually.

## Problem Frame

Today `validate-guide-coverage.sh`, `verify-workspace-surface.sh`, `audit-plugin-readiness.sh`, and related scripts fail with sparse stderr and require `--quiet` to suppress noise — the opposite of what agents need. Sub-checks hide which server or file failed. File-mutating scripts like `bootstrap-re-pipeline.sh` do not report created vs preserved paths.

## Scope Boundaries

### In Scope

- Shared `scripts/lib/check-log.sh` (verbose default, `--quiet` opt-out, summary block)
- Shared `scripts/lib/guide-manifest.sh` (all guide paths, MCP servers, commands, invariants)
- Refactor guide/surface/audit/capability scripts to use shared logging
- Improve `bootstrap-re-pipeline.sh` mutation logging
- Tests asserting summary markers and quiet mode

### Out of Scope

- Rewriting vacuum/matcher/objdiff pipeline scripts
- Changing pass/fail semantics or success token strings

## Implementation Units

### U1. Shared check-log and guide-manifest libraries

**Goal:** One logging API and one source of truth for guide coverage paths.

**Files:**
- `scripts/lib/check-log.sh`
- `scripts/lib/guide-manifest.sh`

**Approach:**
- `check-log.sh`: `check_log_read`, `check_log_grep`, `check_log_mcp_server`, `check_log_file_op` (created|preserved|removed), `check_log_fail`, `check_log_summary`
- `guide-manifest.sh`: exported arrays for files, kb layers, MCP servers, slash commands, invariants, CLI tokens; helper `guide_manifest_root`

**Verification:** Sourced by refactored scripts without duplicate path lists.

### U2. Refactor guide and surface validators

**Goal:** Verbose trace + summary for guide coverage and workspace surface checks.

**Files:**
- `scripts/validate-guide-coverage.sh`
- `scripts/validate-capability-parity.sh`
- `scripts/verify-workspace-surface.sh`

**Approach:** Source libs; log each check; propagate `--quiet` to sub-scripts; print summary before success token.

**Test scenarios:**
- Default run prints `check summary` section on stderr
- `--quiet` suppresses trace but keeps success token on stdout
- Missing artifact still exits non-zero with fail line in summary

**Files (tests):**
- `tests/test-guide-coverage.sh`
- `tests/test-check-log.sh`

### U3. Refactor audit and bootstrap scripts

**Goal:** Plugin audit and RE bootstrap report every check and file mutation.

**Files:**
- `scripts/audit-plugin-readiness.sh`
- `scripts/bootstrap-re-pipeline.sh`
- `scripts/run-test-suite.sh`

**Approach:** Per-section audit logging; bootstrap logs created vs preserved; test suite prints failed test names in summary (existing) plus check counts.

**Verification:** `./scripts/run-test-suite.sh` green.

## Success Criteria

1. Running `./scripts/validate-guide-coverage.sh` lists each MCP server and file checked, then a summary block.
2. `--quiet` still supported for tests and machine parsing.
3. No duplicate guide path arrays outside `guide-manifest.sh`.
4. Full test suite passes locally.
