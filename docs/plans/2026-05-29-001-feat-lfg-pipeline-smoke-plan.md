---
title: "feat: Add LFG pipeline smoke harness"
status: active
created: 2026-05-29
---

## Summary

Create a minimal, repeatable smoke harness so LFG runs against a concrete software task in this repository, instead of failing at plan/work gates due to missing implementation scope.

## Problem Frame

The current repository has no plan backlog or bounded feature stub for pipeline execution. LFG requires a plan-first flow and implementation changes beyond plan artifacts.

## Scope Boundaries

### In Scope

- Add a tiny executable workflow target that can be planned, implemented, reviewed, and validated in one pass.
- Keep changes confined to repository-local scripts/docs and avoid production behavior changes.

### Out of Scope

- Shipping product-facing features.
- Broad refactors or architecture changes.

### Deferred to Follow-Up Work

- Expanding the smoke harness into full regression automation.

## Key Technical Decisions

- Use a small script-based harness so execution is deterministic and easy to review.
- Keep outputs text-based and committed in standard repo locations.

## Implementation Units

### U1. Add smoke harness skeleton

**Goal:** Introduce a minimal runnable target for LFG-driven implementation.

**Requirements:** Establish an executable software slice that can be planned and implemented end-to-end.

**Dependencies:** None.

**Files:**

- scripts/lfg-smoke.sh
- docs/lfg-smoke.md

**Approach:** Add a shell script with strict flags and a short doc describing invocation and expected output.

**Patterns to follow:** Existing script and docs conventions in this repository.

**Test scenarios:**

- Happy path: running the script exits 0 and prints a completion marker.
- Error path: invalid invocation exits non-zero with clear usage output.

**Verification:** The script runs locally and its documented usage matches behavior.

### U2. Add minimal verification coverage

**Goal:** Ensure the smoke harness is verifiable in automation-friendly form.

**Requirements:** Provide at least one direct test/assertion path for harness behavior.

**Dependencies:** U1.

**Files:**

- tests/lfg_smoke_test.sh

**Approach:** Add a lightweight shell test that executes the harness and asserts marker output and exit code.

**Patterns to follow:** Existing repository test naming/layout conventions.

**Test scenarios:**

- Happy path: harness marker is present and exit code is 0.
- Edge case: missing required input produces documented failure behavior.

**Verification:** The test fails before harness behavior is correct and passes once behavior matches the documented contract.
