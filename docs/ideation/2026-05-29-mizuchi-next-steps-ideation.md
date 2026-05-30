---
date: 2026-05-29
topic: mizuchi-next-steps
focus: determine what we should do next based on create-plugin-scaffold and lfg
mode: repo-grounded
run_id: ef104ca7
supersedes: prior same-day run (fresh re-ideation)
---

# Ideation: Mizuchi Next Steps (Plugin Scaffold + LFG)

**Fresh run.** Main (`4b0c77c`) already landed the LFG script stack (decomp-cli, inject-context, verify-workspace-surface, Cycle 3 plans, tests). Remote `feat/lfg-remote-pipeline-complete` is a **stale empty commit** — reconciliation is about confirming main as canonical, not merging missing work.

## Grounding Context

**Codebase context:** Agent-native matching-decompilation workspace for Odyssey/KOTOR. Success = **objdiff 0**. `matching-decompilation-re` plugin v0.1.7 ships full skills/agents/commands; workspace `.cursor/` still holds thin stubs. Cycle 3 vacuum (U1–U7) is fully planned in `docs/plans/2026-05-29-005-*` but **not implemented** (no `state/queue.json`, no vacuum scripts). `fun_00148020` notes say **matched** while OPEN blockers list missing golden `.o`, compiler, and m2ctx. Working tree has uncommitted `.compound-engineering/`, `.gitignore`, `docs/ideation/`.

**Past learnings:** No `docs/solutions/` yet; STRATEGY + knowledgebase enforce objdiff gate and sandbox invariants.

**External context:** Cursor marketplace = public repo + `plugin.json` + manual review per update; `review-plugin-submission` skill audits manifests. Compound `/lfg` = plan → work → review → PR → CI loop (bounded slices only). Adjacent RE pipelines (Mizuchi upstream, FORGE, REagent) persist per-case artifacts and gated verification — aligns with prompts/ + objdiff story.

## Topic Axes

1. Plugin packaging & marketplace readiness (`create-plugin-scaffold`)
2. LFG autonomous delivery (plan → work → review → PR)
3. Verification proof loop (objdiff 0, golden artifacts, honest status)
4. Throughput & queue operations (Cycle 3 vacuum)
5. Workspace/plugin surface authority (single source of truth)

## Ranked Ideas

### 1. Proof integrity gate before any queue or LFG throughput work
**Description:** Add a fail-closed rule: `notes.md` may not say `matched` unless golden `targetObjectPath` exists on disk and `objdiff-gate.sh` / `run-objdiff.sh` reports 0 diffs. Reset `fun_00148020` to `blocked` or `in_progress` with explicit remediation. Extend `verify-workspace-surface.sh` to fail on status/oracle mismatch.
**Axis:** Verification proof · throughput/queue
**Basis:** direct: `prompts/fun_00148020/notes.md` (`status: matched` + OPEN blockers); `docs/knowledgebase/90-meta/evidence-caveats.md`; STRATEGY objdiff metric
**Rationale:** Cycle 3 queue and LFG automation amplify whatever status lies on disk; fixing honesty first prevents compounding false positives.
**Downsides:** Needs Xbox golden object access or explicit blocked state; slows “green dashboard” optics.
**Confidence:** 93%
**Complexity:** Medium
**Status:** Unexplored

### 2. LFG-bounded Cycle 3 Unit 1 — queue schema + scorer CLI
**Description:** Implement **only U1** from `docs/plans/2026-05-29-005-quick-ref.md`: `state/queue.json`, atomic load/save, heuristic scorer, `vacuum-cli.sh status|next|score`. No Claude matcher, no full vacuum loop. Ship via `/lfg` as one vertical slice with tests.
**Axis:** LFG delivery · throughput/queue
**Basis:** direct: Cycle 3 plan U1 spec; grep shows no vacuum/queue scripts in `scripts/`; external: LFG requires bounded software task with verification
**Rationale:** Best **`/lfg`** target today — plan exists, scope is testable, unblocks U2–U7 without a mega-PR.
**Downsides:** Still does not match functions; stakeholders may expect full vacuum immediately.
**Confidence:** 90%
**Complexity:** Medium
**Status:** Unexplored

### 3. Plugin marketplace readiness pass (`review-plugin-submission`)
**Description:** Run create-plugin-scaffold validation on `~/.cursor/plugins/local/matching-decompilation-re/`: manifest, README, LICENSE, CHANGELOG, component discovery, hooks/MCP documented for reviewers. Add workspace pin note in AGENTS.md. Optional LFG slice: “marketplace-ready plugin bundle” PR.
**Axis:** Plugin packaging · workspace authority
**Basis:** external: Cursor plugins reference + marketplace security (open source, manual review); direct: plugin v0.1.7 exists with 33 files; create-plugin-scaffold workflow attached by user
**Rationale:** Natural “next” after skills exist; makes workflow portable beyond Mizuchi repo.
**Downsides:** Weaker story without one verified match demo; review latency.
**Confidence:** 82%
**Complexity:** Low–Medium
**Status:** Unexplored

### 4. Agent-native script parity (CAPABILITY_MATRIX → decomp-cli)
**Description:** Close gaps between `CAPABILITY_MATRIX.md` promises and `scripts/decomp-cli.sh` subcommands — ensure list-prompts, inject-context, run-objdiff, programmatic phase entry points are discoverable and tested. Treat as prerequisite so `/lfg` and agents share one CLI surface (agent-native parity).
**Axis:** Workspace authority · LFG delivery
**Basis:** direct: `CAPABILITY_MATRIX.md` agent ops table; `scripts/decomp-cli.sh` added in `4b0c77c`; tests exist under `tests/`
**Rationale:** LFG and vacuum loop will call scripts, not prose stubs; parity prevents “docs say yes, CLI says no.”
**Downsides:** Audit work before visible feature progress.
**Confidence:** 86%
**Complexity:** Medium
**Status:** Unexplored

### 5. Baseline hygiene — commit workspace assets, retire stale branch story
**Description:** Commit `.gitignore`, `.compound-engineering/config.local.example.yaml`, and ideation docs; document that **main is canonical** (feat branch superseded). Optionally delete or merge-close `feat/lfg-remote-pipeline-complete` on remote after verification.
**Axis:** LFG delivery · workspace authority
**Basis:** direct: `git status` uncommitted files; `git log` shows main `4b0c77c` contains full script stack vs empty `29faf0e` on feat
**Rationale:** Clean baseline before `/lfg` PRs; removes obsolete “merge feat branch first” guidance from prior ideation.
**Downsides:** Commits local CE config choices; remote branch cleanup needs owner consent.
**Confidence:** 88%
**Complexity:** Low
**Status:** Unexplored

### 6. Harden verify-workspace-surface + LFG smoke as pre-flight gate
**Description:** Extend `scripts/lfg-smoke.sh` and `verify-workspace-surface.sh` to assert the full script inventory from main (decomp-cli, run-objdiff, inject-context, list-prompts) and plugin hook presence. Run before any `/lfg` invocation as documented pre-flight in `docs/lfg-smoke.md`.
**Axis:** LFG delivery · verification proof
**Basis:** direct: `docs/lfg-smoke.md`, `scripts/verify-workspace-surface.sh`; external: LFG CI-watch pattern expects verifiable preconditions
**Rationale:** Cheap gate that catches drift between plugin pin, stubs, and execution scripts before autonomous delivery runs.
**Downsides:** Maintenance burden as scripts grow.
**Confidence:** 84%
**Complexity:** Low
**Status:** Unexplored

### 7. First verified objdiff-0 match as plugin demo milestone
**Description:** Pick the smallest function with a realistic golden `.o` path (may not be Xbox scaffold); run programmatic → AI sandbox → objdiff 0; capture artifact layout for README/marketplace. Sequence **after** proof gate (#1), optionally before marketplace submit (#3).
**Axis:** Plugin packaging · verification proof
**Basis:** direct: STRATEGY metrics table; `prompts/fun_00148020` blocked; external: marketplace curated listings favor auditable demo paths
**Rationale:** Packaging without proof is weak marketing; one verified match unlocks Decomp Atlas seed + submission narrative.
**Downsides:** Blocked on toolchain/golden assets for chosen target.
**Confidence:** 78%
**Complexity:** High
**Status:** Unexplored

## Recommended sequence

1. **#5** (baseline hygiene) — quick, unblocks clean PRs  
2. **#1** (proof integrity) — fail-closed status before automation  
3. **#6** (pre-flight gates) — verify LFG prerequisites  
4. **#2** (U1 via `/lfg`) — first substantial autonomous delivery slice  
5. **#4** (CLI parity) — can overlap with #2  
6. **#3 + #7** (marketplace + demo match) — after proof story exists  

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 1 | Merge feat/lfg-remote into main | Superseded — main already contains script stack; feat commit is empty |
| 2 | Full Cycle 3 U1–U7 in one LFG pass | Too expensive for LFG gates; scope overrun |
| 3 | Lewis one-shot batch at volume | Subject expansion; bypasses objdiff-first STRATEGY |
| 4 | Skip programmatic phase | Violates pipeline invariants in AGENTS.md |
| 5 | Move plugin into Mizuchi monorepo | Scope overrun; plugin already local at ~/.cursor/plugins/local |
| 6 | Marketplace submit before any verified match | Weak basis for curated marketplace; downgrade to #3 after #7 |
| 7 | Remove all workspace stubs immediately | Duplicates stronger #4 + authority work without migration plan |
| 8 | Auto-queue from prompts only (no U1 schema) | Duplicates #2; less aligned with Cycle 3 plan contract |
| 9 | docs/solutions/ only, no code | Interesting but better as brainstorm variant; not step-function alone |
| 10 | Semantic decomp without objdiff | Subject-replacement vs matching-decompilation identity |
| 11 | Pivot to benchmark/scoring studies | Explicitly out of scope per STRATEGY “Not working on” |
| 12 | Zapier integration next | User deferred; not grounded in current focus hint |

## Scratch

- Raw candidates: `/tmp/compound-engineering/ce-ideate/ef104ca7/raw-candidates.md`
- Survivors checkpoint: `/tmp/compound-engineering/ce-ideate/ef104ca7/survivors.md`
