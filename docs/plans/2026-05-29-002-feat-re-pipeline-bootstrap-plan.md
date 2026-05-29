---
title: "feat: Add reverse-engineering pipeline bootstrap"
status: active
created: 2026-05-29
---

## Summary

Add a one-command bootstrap path for reverse-engineering workflows so new prompt folders can be initialized consistently and validated before compile/programmatic phases.

## Problem Frame

The workspace has execution scripts for compile, context, and programmatic phases, but there is no single setup entrypoint that scaffolds a prompt folder and verifies required files up front.

## Scope Boundaries

### In Scope

- Create a bootstrap script that initializes a prompt folder layout for RE work.
- Add a test covering script behavior and failure handling.
- Add documentation showing how to run the bootstrap before existing pipeline scripts.

### Out of Scope

- Changing compile/toolchain behavior.
- Modifying objdiff/permuter logic.

### Deferred to Follow-Up Work

- Integrating bootstrap into a higher-level TUI or wizard.

## Key Technical Decisions

- Keep bootstrap logic shell-based to match existing scripts.
- Reuse current prompt/settings conventions (`prompt.md`, `settings.yaml`) and validation flow.

## Implementation Units

### U1. Add prompt bootstrap script

**Goal:** Create a script that scaffolds a new prompt folder with required files if they do not exist.

**Requirements:** Deterministic folder/file setup for RE pipeline entry.

**Dependencies:** None.

**Files:**
- scripts/bootstrap-re-pipeline.sh
- scripts/validate-prompt-settings.sh

**Approach:** Parse `--prompt <path>`, create folder, create placeholder `prompt.md` and minimal `settings.yaml` template, then run existing validation.

**Patterns to follow:** Existing strict-shell script style in `scripts/*.sh`.

**Test scenarios:**
- Happy path: missing folder is created and required files are written.
- Edge case: folder already exists with files; script is idempotent and does not overwrite content unexpectedly.
- Error path: missing `--prompt` exits non-zero with usage text.

**Verification:** Running script with valid input creates a usable prompt folder; invalid input exits with clear guidance.

### U2. Add coverage and docs

**Goal:** Ensure bootstrap behavior is testable and discoverable.

**Requirements:** Durable usage guidance and executable verification.

**Dependencies:** U1.

**Files:**
- tests/bootstrap_re_pipeline_test.sh
- docs/re-pipeline-bootstrap.md

**Approach:** Add shell test that asserts generated file presence/content and a short doc showing usage with follow-on scripts.

**Patterns to follow:** Existing docs format and shell-test conventions in this repo.

**Test scenarios:**
- Happy path: script creates expected files and test confirms contents.
- Error path: script fails with usage on missing args.

**Verification:** Test passes locally and docs commands align with script behavior.
