---
title: "feat: reference pipeline contract and case manifests"
status: active
type: feat
created: 2026-06-05
origin: architecture-plan-2026-06-05
---

## Summary

Implement the first architecture slice for Mizuchi's article-faithful reference pipeline: make the canonical runtime explicit in the knowledgebase, introduce a stable prompt-local `case.yaml` contract that can survive migration into a cross-platform app, and wire validation of that contract into the existing workspace-surface guardrails.

## Problem Frame

The repo already has the building blocks of the matching-decompilation workflow, but the architecture is implied across scripts and bridge docs rather than encoded as a single source of truth. Prompt folders also lack a stable identity/proof manifest above the strict Mizuchi `settings.yaml` tool contract, which makes future app-native orchestration harder and leaves the workspace without a machine-checkable case contract.

## Requirements

- **R1. Canonical reference runtime doc**
  - Add a knowledgebase document that defines the article-faithful reference pipeline as Mizuchi's first implementation shape.
  - Preserve current invariants: proof-first, programmatic-before-AI, no integration before verification.

- **R2. Stable workspace contract**
  - Add a knowledgebase document defining the prompt-local case contract, artifact layout, and derived-state rules.
  - Distinguish the architecture-level `case.yaml` contract from the strict Mizuchi `settings.yaml` contract.

- **R3. Prompt-local case manifests**
  - Add `case.yaml` to the prompt template and the real example prompt.
  - Ensure the manifest covers target identity, symbol identity, proof target, and workspace path/build dir.

- **R4. Machine-checkable validation**
  - Add a validator that checks every real prompt folder has a valid `case.yaml`.
  - Enforce consistency between `case.yaml` and `settings.yaml` for symbol name and golden object path.

- **R5. Workspace-surface integration**
  - Wire the new docs and validator into the existing surface/guide verification path.
  - Add at least one focused regression test for the new validator.

## Implementation Units

### U1. Canonical runtime docs

**Goal:** Make the reference pipeline and workspace contract explicit knowledgebase artifacts.

**Files:**
- `docs/knowledgebase/10-architecture-runtime/reference-pipeline.md`
- `docs/knowledgebase/10-architecture-runtime/workspace-contract.md`
- `docs/knowledgebase/10-architecture-runtime/pipeline-bridge.md`
- `docs/knowledgebase/50-execution/playbook.md`

**Approach:** Add the two new source-of-truth docs and link them from the existing bridge/playbook so the repo has one canonical runtime description and one stable workspace contract.

### U2. Case manifest contract

**Goal:** Introduce prompt-local `case.yaml` as the stable case identity/proof manifest.

**Files:**
- `prompts/_template/case.yaml`
- `prompts/fun_00148020/case.yaml`
- `prompts/_template/notes.md`
- `prompts/_template/prompt.md`

**Approach:** Add a minimal architecture-stable manifest with required target/symbol/proof/workspace fields, then update template guidance so new prompt folders treat `case.yaml` as required alongside `settings.yaml`.

### U3. Validation and workspace enforcement

**Goal:** Make the new contract executable instead of doc-only.

**Files:**
- `scripts/lib/case-manifest.sh`
- `scripts/validate-case-manifests.sh`
- `scripts/lib/guide-manifest.sh`
- `scripts/verify-workspace-surface.sh`
- Manual verification: run `./scripts/validate-case-manifests.sh --quiet`
  against real prompt folders and inspect the emitted status.

**Approach:** Add a shared manifest reader plus a validator that fails on missing or mismatched case manifests, then wire it into the workspace-surface guard and cover it with a focused shell test.

## Success Criteria

1. The repo has a canonical reference-pipeline document and a canonical workspace-contract document.
2. Every real prompt folder in the repo carries `case.yaml`.
3. `scripts/validate-case-manifests.sh` passes on the repo and fails for missing/mismatched manifests.
4. `scripts/verify-workspace-surface.sh` includes the new validator and contract docs in its checks.
5. The targeted regression test for case-manifest validation passes.
