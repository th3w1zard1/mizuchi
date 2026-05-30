---
title: "feat: CLI capability parity for agent-native guide surfaces"
status: active
type: feat
created: 2026-05-29
origin: docs/ideation/2026-05-29-mizuchi-next-steps-ideation.md
---

## Summary

Close the remaining parity gap between `CAPABILITY_MATRIX.md` and executable CLI entry points so every documented operation is discoverable through `scripts/decomp-cli.sh` and validated by tests/gates.

## Problem Frame

The workspace has strong guide surfaces (commands, agents, MCP wiring, capability docs), but `decomp-cli.sh` still exposes only a subset of documented operations. This creates drift risk for LFG/autonomous flows that depend on one canonical, executable interface.

## Scope Boundaries

### In Scope

- Add missing parity subcommands to `scripts/decomp-cli.sh`
- Add parity assertions that compare advertised capability rows to CLI surface
- Add/extend tests for new CLI behavior and gate integration

### Out of Scope

- New matching algorithms, scorer strategy changes, or vacuum loop redesign
- Marketplace publication work
- Changing capability policy in `CAPABILITY_MATRIX.md`

### Deferred to Follow-Up Work

- Auto-generation of matrix rows from code metadata
- Cross-repo parity checks (only this workspace)

## Implementation Units

### U1. Extend decomp CLI parity surface

**Goal:** Ensure `decomp-cli.sh` exposes all core operations documented for agents.

**Requirements:** Capability discovery and action parity stay aligned between docs and executable CLI.

**Dependencies:** None.

**Files:**
- `scripts/decomp-cli.sh`
- `scripts/help-command.sh`
- `CAPABILITY_MATRIX.md` (only if command names need canonical wording alignment)
- `tests/test-decomp-cli.sh` (new)

**Approach:**
- Add direct CLI subcommands for: `help`, `inject-context`, `list-prompts`, `run-objdiff`, and programmatic helpers already documented.
- Reuse existing scripts as thin wrappers instead of duplicating business logic.
- Keep command help output deterministic so tests can assert a stable surface.

**Test scenarios:**
- Happy path: each new subcommand executes and returns expected output shape.
- Error path: missing required arguments returns non-zero with usage.
- Integration: `help` output includes newly exposed command names.

**Verification:** CLI usage and behavior match documented operations in `CAPABILITY_MATRIX.md`.

### U2. Add capability parity gate and tests

**Goal:** Fail fast when command surface drifts from declared capability matrix.

**Requirements:** Drift between docs and executable surface is detectable in CI/local smoke checks.

**Dependencies:** U1.

**Files:**
- `scripts/verify-workspace-surface.sh`
- `scripts/validate-capability-parity.sh` (new)
- `tests/verify_workspace_surface_test.sh`
- `tests/test-capability-parity.sh` (new)

**Approach:**
- Add a parity validator script that checks required command tokens are present in both matrix/help docs and CLI usage.
- Call validator from `verify-workspace-surface.sh` so LFG smoke catches parity regressions.
- Keep checks text-based and deterministic.

**Test scenarios:**
- Happy path: validator passes on current repository state.
- Failure path: missing token in a temp fixture fails with clear error.
- Integration: workspace-surface test exercises validator transitively.

**Verification:** `verify-workspace-surface.sh` remains green with parity validator active.

