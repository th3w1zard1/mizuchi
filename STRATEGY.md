---
name: Mizuchi
last_updated: 2026-06-05
---

# Mizuchi Strategy

## Target problem

Reverse engineers still recover source from existing applications through slow, tool-fragmented workflows: manually driving disassemblers, rebuilding context by hand, and stitching together ad hoc scripts per target. The hard part is not getting plausible code for one function; it is turning arbitrary application inputs into a repeatable, packageable pipeline that can recover large codebases quickly without losing proof, state, or operator trust.

## Our approach

Mizuchi wins by turning agentic reverse engineering into a **single entrypoint** with a normalized case contract, adapter-driven ingest, and proof-aware orchestration. Instead of making the operator manually drive Ghidra and bespoke scripts for every target, Mizuchi accepts multiple best-practice input shapes, normalizes them into one runtime, runs fast programmatic and one-shot passes behind the same interface, and keeps verification and packaging inside one self-contained product.

The product bet is not "AI writes decompilation." The bet is that a proof-aware orchestrator plus good ingest, context packaging, and adapter boundaries can make one-shot-first reverse engineering far faster than manual iterative tooling, while still falling back to deterministic and iterative paths when the first shot is not enough.

## Who it's for

**Primary:** Reverse engineers and decompilation contributors recovering source from existing applications, firmware, games, and other compiled targets. They're hiring Mizuchi to turn a binary or project input into a fast, guided, mostly-autonomous source-recovery workflow without rebuilding the whole toolchain and process by hand for each target.

**Secondary:** AI-assisted RE operators who need one system to manage ingest, context, proof, retries, and packaging across multiple target families.

## Key metrics

| Metric | Where | What good looks like |
|--------|-------|----------------------|
| Time to first runnable case | app run metadata / workspace state | A new target becomes executable quickly, without manual repo surgery |
| Verified recovery rate | proof artifacts per recovered unit | More units end in real proof, not just plausible output |
| Throughput per unattended run | orchestrator run history | Large targets progress without constant operator steering |
| Input compatibility | ingest adapter coverage | More target/input shapes normalize into the same runtime successfully |
| Self-contained setup success | installer / first-run telemetry | Users reach a working environment without bespoke local setup work |

## Tracks

### Universal ingest and target modeling

Build a flexible input layer that accepts multiple target shapes and normalizes them into one case/workspace contract.

_Why it serves the approach:_ A single entrypoint only works if heterogeneous binaries and projects become one runtime model instead of a pile of target-specific setup paths.

### Proof-aware orchestration

Keep programmatic passes, one-shot attempts, iterative fallback, and verification inside one orchestrator with truthful state and explicit artifacts.

_Why it serves the approach:_ Speed only compounds if the operator can trust the system's status, retries, and proof gates.

### Self-contained packaging

Collapse today's toolchain sprawl into a product that can be installed, run, and updated as one cross-platform system.

_Why it serves the approach:_ Manual RE remains slow partly because every workflow is also an environment-assembly exercise.

### Performance and scale

Optimize for unattended recovery of many units across a target, not just success on one hand-driven function.

_Why it serves the approach:_ The product only beats manual RE if throughput and operator leverage improve at application scale.

## Not working on

- A KOTOR/Odyssey-only product boundary
- Benchmark leaderboards or model bake-offs as the product thesis
- Human-only, tool-by-tool driving as the primary UX
- Claiming universal full-source recovery in v1 before adapter, proof, and packaging surfaces are real

## Marketing (optional)

**One-liner:** Mizuchi is a proof-aware, self-contained entrypoint for agentic reverse engineering.

**Key message:** Instead of manually driving a stack of RE tools for every target, point Mizuchi at an application, let it normalize the input, run the fastest viable recovery path, and keep the whole workflow grounded in explicit verification and durable state.
