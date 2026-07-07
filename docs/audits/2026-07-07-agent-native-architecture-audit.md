# Agent-Native Architecture Review: ReconstructKit

Date: 2026-07-07

Scope: local ReconstructKit workspace, including CLI front doors, Cursor command specs, prompt folders, scripts, documented MCP analogues, and proof artifacts. ReconstructKit is not a conventional web UI; this audit treats user-facing shell commands, slash commands, prompt folders, manifests, and generated proof files as the product surface.

## Overall Score Summary

| Core Principle | Score | Percentage | Status |
|----------------|-------|------------|--------|
| Action Parity | 35/42 executable CLI commands mapped or documented | 83% | Excellent |
| Tools as Primitives | 9/14 documented agent/tool surfaces are primitive | 64% | Partial |
| Context Injection | 5/7 expected context types injected or documented | 71% | Partial |
| Shared Workspace | 6/7 primary data spaces shared by user and agent | 86% | Excellent |
| CRUD Completeness | 4/9 entity classes have full or near-full CRUD | 44% | Needs Work |
| UI Integration | 4/6 state-change surfaces have visible receipts or listings | 67% | Partial |
| Capability Discovery | 6/7 discovery mechanisms present | 86% | Excellent |
| Prompt-Native Features | 9/15 features primarily prompt/spec driven | 60% | Partial |

Overall Agent-Native Score: 70%

Status legend:

- Excellent: 80% or better
- Partial: 50-79%
- Needs Work: below 50%

## Evidence Inventory

- CLI bridge exposes 42 shell subcommands in `scripts/decomp-cli.sh`.
- Installable Python front door exposes the default one-shot recovery path, three upstream-compatible command shims, two self-report commands, and 11 legacy command pass-throughs in the recovery runtime front-door module (`src/recovery_runtime/reconkit_cli.py`, with implementation in `src/reconkit_re/reconkit_cli.py`).
- Cursor surface includes 5 command specs, 2 agent specs, and 8 local skill specs under `.cursor/`.
- Current test inventory includes 118 top-level test scripts.
- Prompt corpus includes 87 prompt `case.yaml` manifests.
- `CAPABILITY_MATRIX.md` documents the intended agent roles, MCP tools, context injection template, constraints, and discovery surface.

## Action Parity Audit

User actions found:

| Action | Location | Agent/CLI Surface | Status |
|--------|----------|-------------------|--------|
| Build or refine a prompt folder | `.cursor/commands/decomp-prompt.md`, `scripts/decomp-cli.sh decomp-prompt` | `decomp-prompt-architect`, prompt templates | Covered |
| Validate prompt settings and case manifests | `decomp-validate`, validators | CLI and tests | Covered |
| Audit prompt production readiness | `decomp-readiness` | CLI | Covered |
| Find similar examples | `.cursor/commands/decomp-atlas.md`, `source-parity-feature-index` | CLI bridge and skill | Partial: Atlas command is guidance-only locally |
| Run function matching | `.cursor/commands/decomp-function.md`, `decomp-function` | CLI orchestrator | Covered |
| Integrate verified matches | `.cursor/commands/decomp-integrate.md`, `decomp-integrate`, `commit-verified-match` | Verification-gated CLI | Covered |
| Export context | `export-context`, `export-context-batch` | CLI | Covered |
| Run one-shot package workflows | `one-shot-source*`, `binary-source-roundtrip`, PE/ELF roundtrip commands | CLI | Covered |
| Run source parity synthesis | `source-parity-synthesize`, `source-parity-one-shot`, `recover` | CLI and Python modules | Covered |
| Manage autonomous queues | `queue`, `scorer`, `vacuum`, `init-vacuum-state` | CLI | Covered |
| Inspect capabilities/help | `verify-surface`, `help-command.sh`, `reconkit-cli self-check`, `upstream-status` | CLI | Covered |
| Discover live Ghidra/BinaryNinja-style function details | `CAPABILITY_MATRIX.md`, AgentDecompile references | External MCP/config | Partial: not fully executable from the repo alone |
| Delete or archive generated work | `one-shot-source-clean`, queue reset | Specific cleanup commands only | Partial by design, because destructive operations are constrained |

Score: 35/42 executable CLI commands mapped or documented.

Missing or partial parity:

- The documented Decomp Atlas and AgentDecompile MCP capabilities are not fully mirrored by local executable commands.
- Some package/entity mutations are intentionally narrow cleanup or queue moves rather than general update/delete operations.
- Prompt-folder copy/scaffold creation is documented but not exposed as one explicit idempotent primitive command.

Recommendations:

- Add a generated capability map that derives rows directly from `scripts/decomp-cli.sh`, `.cursor/commands`, and `reconkit-cli upstream-status`.
- Promote `decomp-atlas` from a guidance echo into a concrete local search/index command or mark it explicitly external-only.
- Add a non-destructive `prompt scaffold` command that creates missing prompt files from `_template` without overwriting existing user edits.

## Tools as Primitives Audit

Tool analysis:

| Tool or Surface | File | Type | Reasoning |
|-----------------|------|------|-----------|
| `get_workspace_context` | `scripts/get-workspace-context.sh`, `CAPABILITY_MATRIX.md` | Primitive | Reads workspace state without making decisions. |
| `list_prompts` | `scripts/list-prompts.sh` | Primitive | Lists prompt entities with optional status filters. |
| `run_objdiff` | `scripts/run-objdiff.sh`, `scripts/lib/verify-objdiff.sh` | Primitive | Performs a single verification comparison. |
| `compile_and_view_assembly` | `scripts/compile-and-view-assembly.sh` | Primitive | Compiles one candidate and returns assembly/diff data. |
| `export-context` | `cli.py` (`src/recovery_runtime/cli.py`, impl in `src/reconkit_re/cli.py`) | Primitive-ish | Packages context, but includes analysis policy knobs. |
| `decomp-function` | `scripts/decomp-cli.sh` | Workflow | Encodes programmatic phase, AI fallback, and report writing. |
| `recover` | `cli.py` (`src/recovery_runtime/cli.py`, impl in `src/reconkit_re/cli.py`), `pipeline.py` (`src/reconkit_re/pipeline.py`) | Workflow | Full staged recovery pipeline. |
| `source-parity-one-shot` | `source_parity_one_shot.py` (`src/recovery_runtime/source_parity_one_shot.py`, impl in `src/reconkit_re/source_parity_one_shot.py`) | Workflow | Orchestrates a complete one-shot pipeline. |
| `commit-verified-match` | `scripts/commit-verified-match.sh` | Domain workflow | Verification-gated staging helper, appropriately high-stakes. |
| `one-shot-source-clean` | `scripts/one-shot-source-clean.py` | Domain workflow | Cleanup policy is encoded for safety. |
| `queue summary/next/move/attempt` | `scripts/lib/queue-state.sh` | Mixed | Entity-state primitives grouped under one command. |
| `reconkit-cli self-check` | `reconkit_cli.py` (`src/recovery_runtime/reconkit_cli.py`, impl in `src/reconkit_re/reconkit_cli.py`) | Primitive report | Inspects local assets and capabilities. |
| `verify-surface` | `scripts/verify-workspace-surface.sh` | Primitive report | Verifies command/config presence. |
| Cursor command specs | `.cursor/commands/*.md` | Prompt-native workflows | Natural-language feature definitions over executable primitives. |

Score: 9/14 documented agent/tool surfaces are primitive or primitive-ish.

Problematic workflow-shaped surfaces:

- `decomp-function`, `recover`, and `source-parity-one-shot` are intentionally workflow-shaped. That is useful for operator ergonomics but limits agent composition unless lower-level primitives stay documented and stable.
- Queue operations are grouped under a single command, making CRUD completeness less obvious.

Recommendations:

- Keep workflow commands as shortcuts, but document their underlying primitive command sequence in a machine-readable manifest.
- Split or alias queue operations into discoverable primitive names for create/read/update/delete semantics.
- Add JSON schemas for primitive outputs so agents can reliably compose them.

## Context Injection Audit

Context types analysis:

| Context Type | Injected? | Location | Notes |
|--------------|-----------|----------|-------|
| Available capabilities | Yes | `CAPABILITY_MATRIX.md`, `scripts/help-command.sh`, `scripts/inject-context.sh` | Includes commands, MCP tools, and quick reference. |
| Workspace state | Yes | `scripts/get-workspace-context.sh`, `scripts/inject-context.sh` | Prompt counts and current state are intended startup context. |
| Prompt queue summary | Yes | `.cursor/agents/*.md`, `scripts/inject-context.sh` | Explicit context field. |
| Recent activity | Partial | `.cursor/agents/*.md`, prompt notes | Documented, but freshness depends on notes/state quality. |
| Constraints/guardrails | Yes | `AGENTS.md`, `CAPABILITY_MATRIX.md`, `.cursor/rules` | Objdiff and no-direct-edit invariants are prominent. |
| Tool availability/provenance | Partial | `reconkit-cli self-check`, `tools.py` (`src/reconkit_re/tools.py`) | Available as report, not clearly injected in every workflow. |
| User/project preferences | Partial | `AGENTS.md`, prompt docs | Project constraints exist; personalized preference context is external to repo. |

Score: 5/7 expected context types injected or documented.

Missing context:

- There is no single committed `context.md` or equivalent rolling workspace-memory file with current priorities and learned decisions.
- Toolchain availability is inspected but not consistently surfaced alongside every matching prompt.
- Context freshness is not verified by a dedicated test that asserts injected state matches live prompt/queue counts.

Recommendations:

- Add `docs/context/current-workspace.md` or a generated `state/context.md` contract with identity, current state, available resources, constraints, and stale-data markers.
- Add a test that compares `scripts/inject-context.sh` output to `scripts/list-prompts.sh` or queue summaries.
- Inject capability/self-check summaries into long-running source synthesis and vacuum reports.

## Shared Workspace Audit

Data store analysis:

| Data Store | User Access | Agent Access | Shared? |
|------------|-------------|--------------|---------|
| `prompts/<name>/prompt.md` | Direct file edit | Prompt builder/function agent | Yes |
| `prompts/<name>/settings.yaml` | Direct file edit | Validators and agents | Yes |
| `prompts/<name>/notes.md` | Direct file edit | Agents log state | Yes |
| `prompts/<name>/build/` | Inspectable artifacts | Matching pipeline writes | Yes, but generated |
| `state/queue.json` | Direct file and queue CLI | Vacuum/scorer/queue tools | Yes |
| `target/**` run directories | Direct filesystem | Pipelines write receipts/packages | Yes, generated |
| External Ghidra/AgentDecompile state | External app | MCP references | Partial/external |

Score: 6/7 primary data spaces are shared.

Isolated data or risks:

- External MCP/tool state is referenced but not normalized into the shared workspace contract.
- Generated build and target directories are shared but can be noisy; stable receipt paths are more agent-friendly than raw temporary artifacts.

Recommendations:

- Maintain stable `report.json`/receipt contracts for every workflow that writes under `target/` or `prompts/*/build/`.
- Add path conventions for generated vs. user-editable files to reduce accidental overwrites.
- Normalize external tool snapshots into prompt-local files before matching loops begin.

## CRUD Completeness Audit

Entity CRUD analysis:

| Entity | Create | Read | Update | Delete/Clean | Score |
|--------|--------|------|--------|--------------|-------|
| Prompt folder | Partial | Yes | Yes | No general safe delete | 2.5/4 |
| Case manifest | Yes via templates/import | Yes | Yes | No | 3/4 |
| Prompt notes/status | Yes | Yes | Yes | No | 3/4 |
| Queue item | Yes | Yes | Yes | Reset by name | 4/4 |
| Source task | Import/generate | Yes | Upgrade/regenerate | No per-task clean | 2.5/4 |
| One-shot package | Yes | Verify/read | Refresh/validate derived receipts | Clean command exists | 3.5/4 |
| Candidate source | Generate/import | Yes | Regenerate | No general delete | 2.5/4 |
| Matched integration | Commit/integrate | Yes | No generic rollback/update | No | 2/4 |
| Capability matrix/context docs | Manual | Yes | Manual | No | 2/4 |

Overall score: 4/9 entity classes have full or near-full CRUD.

Incomplete entities:

- Prompt folders lack an explicit safe archive/delete primitive.
- Source tasks and candidate source files are create/read-heavy with limited update/delete semantics.
- Integration has strong create/read gates but no first-class revert/rollback workflow.

Recommendations:

- Prefer non-destructive archive commands over delete commands, e.g. `prompt archive <name>` and `source-task archive <id>`.
- Add entity-specific `read`/`list` commands for source tasks and one-shot package receipts.
- Document intentionally missing delete operations as guardrails so CRUD gaps are explicit rather than accidental.

## UI Integration Audit

Agent action to UI/update analysis:

| Agent Action | UI/Operator Mechanism | Immediate? | Notes |
|--------------|-----------------------|------------|-------|
| Prompt creation/edit | Filesystem, validators | Yes | User sees files immediately. |
| Matching run | `build/decomp-function.json`, notes, objdiff artifacts | Partial | Receipts exist; no live stream UI. |
| Queue movement | `state/queue.json`, queue summary | Yes | CLI-readable. |
| One-shot package generation | Package directory and proof reports | Yes | Strong receipt model. |
| Capability/help discovery | `/help`, `help-command.sh`, docs | Yes | JSON-capable help exists. |
| External MCP analysis | External tool surfaces | Partial | Needs snapshot into workspace for durable visibility. |

Score: 4/6 action surfaces have immediate visible receipts or listings.

Silent action risks:

- Long-running recovery stages can update many target/build files without a concise live status file until a stage completes.
- External MCP activity may not leave a prompt-local audit trail unless the operator records it.

Recommendations:

- Add a per-run append-only activity log for `recover`, `source-parity-one-shot`, and `vacuum`.
- Make external tool calls write a minimal prompt-local snapshot before downstream matching starts.
- Standardize status receipt fields: `schema`, `status`, `stage`, `updatedAt`, `claimBoundary`, `nextAction`.

## Capability Discovery Audit

Discovery mechanism analysis:

| Mechanism | Exists? | Location | Quality |
|-----------|---------|----------|---------|
| Onboarding/overview docs | Yes | `README.md`, `AGENTS.md` | Strong |
| Help documentation | Yes | `.cursor/commands/help.md`, `scripts/help-command.sh` | Strong |
| Capability matrix | Yes | `CAPABILITY_MATRIX.md` | Strong but dated |
| Suggested commands/actions | Yes | `AGENTS.md`, `scripts/decomp-cli.sh usage` | Strong |
| Empty-state guidance | Partial | prompt templates | Limited to prompt folders |
| Slash commands | Yes | `.cursor/commands/*.md` | Strong |
| Agent self-describes capabilities | Yes | `.cursor/agents/*.md`, injected context template | Strong |

Score: 6/7 mechanisms present.

Missing discovery:

- The capability matrix is manually maintained and can drift from the CLI surface.
- Empty-state guidance for new users without prompt folders is limited.

Recommendations:

- Generate a capability appendix from CLI parsers and Cursor command files.
- Add a `reconkit-cli capabilities --json` command or extend `self-check` with explicit command inventory.
- Add first-run guidance for an empty `prompts/` queue.

## Prompt-Native Features Audit

Feature definition analysis:

| Feature | Defined In | Type | Notes |
|---------|------------|------|-------|
| Function matching workflow | `.cursor/commands/decomp-function.md`, `.cursor/skills/decomp-pipeline.md`, scripts | Mixed | Prompt defines policy; script executes pipeline. |
| Prompt construction | `.cursor/commands/decomp-prompt.md`, prompt templates | Prompt-native | Good natural-language outcome definition. |
| Integration guardrails | `.cursor/commands/decomp-integrate.md`, scripts | Mixed | High-stakes gate correctly encoded in code too. |
| Objdiff proof boundary | `AGENTS.md`, rules, verify scripts | Mixed | Must remain code-enforced. |
| Capability discovery | `.cursor/commands/help.md`, `CAPABILITY_MATRIX.md` | Prompt-native | Help surface is mostly prose/data. |
| Source synthesis heuristics | `sourcegen.py`, `source_parity_synthesize.py` | Code-defined | Necessary compiler-pattern logic. |
| One-shot packaging | scripts and README | Code-defined workflow | Heavy deterministic artifact generation. |
| Queue/scorer/vacuum | scripts and docs | Mixed | Policy is partly docs, partly code. |
| Claim-boundary discipline | `AGENTS.md`, source reports | Prompt-native with code receipts | Strong. |

Score: 9/15 feature surfaces primarily prompt/spec driven.

Code-defined features:

- Source generation, byte slicing, verifier construction, and toolchain probing must remain code-defined because they are deterministic proof machinery.
- Some orchestration policy is embedded in scripts where prompt-defined behavior would be easier for agents to adapt.

Recommendations:

- Keep proof-critical mechanics in code, but move prioritization and matching-strategy guidance into editable prompt/spec files.
- Add a machine-readable workflow manifest that scripts and prompts both reference.
- Add examples to prompt-native docs for when to stop, when to escalate, and when to preserve inline-assembly boundaries.

## Top 10 Recommendations by Impact

| Priority | Action | Principle | Effort |
|----------|--------|-----------|--------|
| 1 | Generate a capability inventory from CLI parsers, `.cursor/commands`, and agent specs | Capability Discovery | Medium |
| 2 | Add a rolling workspace context contract with freshness markers | Context Injection | Medium |
| 3 | Promote `decomp-atlas` to an executable local command or mark it external-only | Action Parity | Medium |
| 4 | Add non-destructive archive primitives for prompt folders and source tasks | CRUD Completeness | Medium |
| 5 | Standardize JSON receipts across long-running workflows | UI Integration | Medium |
| 6 | Add a context-injection test that checks live prompt counts and capability listings | Context Injection | Low |
| 7 | Split queue/source-task operations into discoverable primitive aliases | Tools as Primitives | Medium |
| 8 | Write external MCP snapshots into prompt-local files before matching loops | Shared Workspace | Medium |
| 9 | Document intentionally missing delete operations as safety guardrails | CRUD Completeness | Low |
| 10 | Add a shared workflow manifest consumed by prompts and scripts | Prompt-Native Features | High |

## What's Working Excellently

- Objdiff and byte-proof claim boundaries are explicit across docs, scripts, and agent prompts.
- The filesystem is the primary shared workspace, which makes agent/user collaboration inspectable.
- The CLI surface is broad and already covers most operator actions.
- Capability discovery exists through `AGENTS.md`, `CAPABILITY_MATRIX.md`, Cursor command specs, and help scripts.
- Generated package/proof workflows leave durable receipts rather than opaque state.

## Audit Boundary

This is an architecture audit, not a semantic recovery proof. Scores reflect documented and locally inspectable agent-native architecture properties. They do not claim any target binary has been semantically recovered unless that target has its own object/byte parity evidence.
