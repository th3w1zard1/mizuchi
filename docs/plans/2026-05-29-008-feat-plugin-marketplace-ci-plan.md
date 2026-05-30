---
title: "feat: plugin marketplace readiness audit and CI test gate"
status: active
type: feat
created: 2026-05-29
origin: docs/ideation/2026-05-29-mizuchi-next-steps-ideation.md
---

## Summary

Add a repeatable marketplace-readiness audit for the `matching-decompilation-re` plugin and wire GitHub Actions so PR #2 and future branches run the full shell test suite automatically.

## Problem Frame

Ideation item **#3** calls for a marketplace readiness pass, but readiness checks are manual and PRs currently have **no CI checks**. Agents need a fail-closed audit script with fixture-backed tests, plus a workflow that runs on every push/PR.

## Scope Boundaries

### In Scope

- `scripts/audit-plugin-readiness.sh` — manifest, docs, discoverability, frontmatter checks
- Fixture-backed tests (pass + fail cases) so CI does not depend on `~/.cursor/plugins/local/`
- `scripts/run-test-suite.sh` — single entry to run all `tests/*.sh`
- `.github/workflows/test.yml` — bash test gate on ubuntu-latest
- `docs/plugin-marketplace-readiness.md` — checklist + usage
- Extend `verify-workspace-surface.sh` inventory for new scripts

### Out of Scope

- Publishing to Cursor marketplace or editing marketplace.json in a multi-plugin repo
- Fixing plugin content inside `~/.cursor/plugins/local/` (audit reports only; optional local `--fix` deferred)
- First verified objdiff-0 match (#7) — blocked on golden `.o` / toolchain

### Deferred to Follow-Up Work

- Auto-sync plugin version from CHANGELOG into manifest
- Plugin-side MCP template validation when `mcp.json` lands in plugin repo

## Implementation Units

### U1. Plugin readiness audit script

**Goal:** Executable audit aligned with `review-plugin-submission` checklist.

**Requirements:** Emits `PLUGIN_READINESS_OK` on pass; non-zero with actionable stderr on failure.

**Files:**
- `scripts/audit-plugin-readiness.sh`
- `tests/fixtures/plugin-readiness-pass/` (minimal valid plugin tree)
- `tests/fixtures/plugin-readiness-fail/` (broken frontmatter)
- `tests/test-plugin-readiness-audit.sh`

**Approach:**
- `--plugin-root PATH` (required in tests; default `$PLUGIN_ROOT` or local plugin path when present)
- Validate `.cursor-plugin/plugin.json` JSON + required fields
- Require `README.md`, `LICENSE`, `CHANGELOG.md`
- Discover skills/commands/agents/rules; verify files exist
- Require `name` + `description` frontmatter on skills, agents, commands; `description` on rules
- Validate `hooks/hooks.json` parses and references existing hook scripts

**Verification:** Fixture pass exits 0; fixture fail exits non-zero; real plugin path works locally when installed.

### U2. CI test workflow

**Goal:** PR checks run automatically.

**Requirements:** Workflow runs `scripts/run-test-suite.sh` and fails on any test failure.

**Files:**
- `scripts/run-test-suite.sh`
- `.github/workflows/test.yml`
- `docs/plugin-marketplace-readiness.md`
- `AGENTS.md` (audit + CI commands)

**Approach:**
- `run-test-suite.sh` executes every `tests/*.sh` in sorted order; prints summary
- GHA: checkout, bash, run suite on push + pull_request to main

**Verification:** Local `./scripts/run-test-suite.sh` green; after push, `gh pr checks` shows workflow.

## Success Criteria

1. `./scripts/audit-plugin-readiness.sh --plugin-root tests/fixtures/plugin-readiness-pass` → `PLUGIN_READINESS_OK`
2. `./scripts/run-test-suite.sh` → all tests pass
3. GitHub Actions workflow present and runnable on PR #2
