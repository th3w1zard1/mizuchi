---
date: 2026-07-07
topic: open-ideation
focus: (none — surprise-me)
mode: repo-grounded
run_id: 302a41b9
---

# Ideation: Mizuchi Open (Surprise-Me)

## Grounding Context

**Codebase context:** Python `mizuchi_re` package plus 50+ bash scripts; 46 KOTOR prompt folders; dual orchestration (bash CLI + Python vacuum/scorer); AgentDecompile/Ghidra recently disabled; vacuum/scorer Cycle 3; recover pipeline; STRATEGY.md tracks acquisition → compiler forensics → matching. Recent commits focus on recovery/CLI/verifier hardening.

**Past learnings:** No `docs/solutions/` yet; rich material in `docs/knowledgebase/`, STRATEGY.md, vacuum docs, playbook, evidence caveats (objdiff 0 gate).

**External context:** Mizuchi/macabeus ~74% match benchmark; Chris Lewis vacuum pattern; objdiff as proof oracle; KOTOR exe matching largely unclaimed; anti-patterns include re-exec metrics, unattended quota burn, permuter-as-progress.

**Topic axes:** Decomposition skipped — surprise-me mode

## Ranked Ideas

### 1. Compiler Profile Lab as the Shippable Product
**Description:** Treat compiler-profile forensics (flags, ABI, codegen fingerprints, drift detection) as the primary deliverable—not just a prerequisite for matching. Ship profile reports, drift gates, and profile-match badges before chasing full function matches.
**Basis:** `direct:` STRATEGY.md acquisition→compiler forensics→matching; evidence caveats that compiler drift is the default failure mode.
**Rationale:** KOTOR exe matching is largely unclaimed; a credible profile lab is shippable before the long tail of objdiff-0 functions.
**Downsides:** Less exciting than headline match counts; needs clear UX so it does not feel like stalling.
**Confidence:** 82%
**Complexity:** Medium
**Status:** Unexplored

### 2. Lane-Aware Vacuum with Proof-Ready Scoring
**Description:** Classify queue items into lanes (trivial / reloc-wrapper / one-shot / hard) and invert the scorer so proof-ready items (golden .o, compiler profile locked, verifier path clear) rank above raw asm complexity.
**Basis:** `direct:` vacuum/scorer Cycle 3 docs + playbook; `external:` ATFM-style calibration flights (score readiness, not just difficulty).
**Rationale:** Stops burning quota on functions that cannot be verified yet; makes overnight vacuum runs auditable.
**Downsides:** Lane taxonomy must stay honest; misclassification hides hard work or over-prioritizes easy wins.
**Confidence:** 78%
**Complexity:** Medium
**Status:** Unexplored

### 3. Auto-Ingest objdiff-0 Matches into Decomp Atlas
**Description:** When objdiff reports 0 diffs, automatically promote the function into Decomp Atlas as a matched example (prompt slice, C seed, proof manifest)—no manual `/decomp-atlas` step.
**Basis:** `direct:` AGENTS.md intent for Atlas as institutional memory; `external:` Mizuchi/macabeus flywheel pattern for matched examples improving prompts.
**Rationale:** Each verified match compounds prompt quality for the next functions; closes the loop the pipeline already implies.
**Downsides:** Bad matches poison Atlas; needs strict gate (objdiff 0 only, no close enough).
**Confidence:** 85%
**Complexity:** Medium
**Status:** Unexplored

### 4. Unified Proof Specimen Chain
**Description:** One artifact chain per function: vacuum autopsy packet → objdiff receipt → integrate sandbox diff → conservation-style commit record (who/when/verifier version). Operators and CI share the same proof story.
**Basis:** `direct:` vacuum autopsy + readiness docs; `external:` numismatic specimen IDs + museum conservation records; `reasoned:` trust at scale requires auditable lineage, not scattered logs.
**Rationale:** Addresses proof-mode confusion and makes public claims defensible.
**Downsides:** More schema/workflow to maintain; risk of bureaucracy if not automated.
**Confidence:** 80%
**Complexity:** Medium–High
**Status:** Unexplored

### 5. Compiler-Profile Gate Before AI or Vacuum Spend
**Description:** Hard gate: no AI matching or vacuum dequeue until compiler profile for the target object is locked and matches the prompt settings.yaml profile (or explicit waiver logged).
**Basis:** `direct:` matching-decompilation-core forbidden claims + evidence ladder; `reasoned:` mismatched toolchain makes objdiff failure uninterpretable.
**Rationale:** Cheapest lever to cut wasted AI cycles and false almost-matched narratives.
**Downsides:** Blocks progress when profile data is incomplete; needs escape hatch with visible waiver.
**Confidence:** 88%
**Complexity:** Low–Medium
**Status:** Unexplored

### 6. Evidence Ladder as Public Coverage Product
**Description:** Publish tiered coverage metrics (L0: profile locked, L1: compiles, L2: objdiff clean, L3: integrated in tree)—not a single percent-decompiled number. Marketing and contributors share honest progress language.
**Basis:** `direct:` knowledgebase evidence caveats; `external:` Chris Lewis / vacuum honest-metrics pattern.
**Rationale:** KOTOR matching is a long game; tiered ladder prevents overclaiming and guides where to invest next.
**Downsides:** Requires discipline; easy to revert to one headline metric.
**Confidence:** 75%
**Complexity:** Low
**Status:** Unexplored

### 7. Public BYO-Binary Verifier Image
**Description:** Ship a container/CI image where users drop their .o + candidate C; Mizuchi scripts run compile + objdiff and return a signed receipt—without needing the full prompt queue or proprietary game assets.
**Basis:** `reasoned:` distribution moat for a matching toolkit; `external:` decomp.me-style scratch verification; aligns with recover/BYO-binary direction in strategy.
**Rationale:** Expands who can contribute proofs; separates verifier from full decomp factory.
**Downsides:** Support burden; must not leak copyrighted binaries; scope creep into product company.
**Confidence:** 65%
**Complexity:** High
**Status:** Unexplored

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 1 | Atlas-only mode | Too speculative; starves hard-function discovery |
| 2 | Overnight parallel swarm | High cost; weak production evidence |
| 3 | Full shell→Python orchestrator collapse | Right direction but oversized; shadow-diff first |
| 4 | decomp.me scratch ingest | Good brainstorm variant; external dependency |
| 5 | Proof-mode confusion index alone | Tactical; folded into specimen chain |
| 6 | One-shot scheduler-only | Largely already policy in docs |
| 7 | OuLiPo permuter styles | Interesting tweak; not top-7 |
| 8 | White-glove anti-vacuum sprint | Operational mode, not product improvement |

## Checkpoints

- Raw candidates: `/tmp/compound-engineering/ce-ideate/302a41b9/raw-candidates.md`
- Survivors: `/tmp/compound-engineering/ce-ideate/302a41b9/survivors.md`
