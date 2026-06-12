# Universal Entrypoint Architecture

This document defines the product boundary Mizuchi is moving toward: one primary
entrypoint that accepts heterogeneous reverse-engineering inputs, normalizes them into
one shared workspace contract, and runs proof-aware recovery through a single runtime.

## Product boundary

Mizuchi is not "the Odyssey scripts, plus some helpers." It is a local-first runtime
that should let an operator point the product at an application input and get the same
core lifecycle every time:

1. Intake the source artifact
2. Select an adapter
3. Materialize a case workspace
4. Run programmatic and agentic recovery passes
5. Verify against an explicit proof contract
6. Expose truthful state for status, retry, and integration

Matching decompilation is the current strongest proof path, not the only future product
shape.

## Primary entrypoint

The canonical shell surface is:

```text
./scripts/decomp-cli.sh
```

Everything else is a parity surface around that runtime:

- Cursor slash commands
- MCP tools
- Agent prompts and injected workspace context
- Future GUI or packaged desktop shells

If a workflow exists in one of those surfaces, the runtime should be able to express it
through the same underlying case and proof model.

## Shared workspace model

The shared workspace is the durable interface between users, agents, and tooling:

```text
prompts/<case-id>/
├── case.yaml
├── settings.yaml
├── prompt.md
├── notes.md
└── build/
```

This is intentionally file-first:

- Users can inspect and edit case state directly
- Agents can reason over the same artifacts users see
- Runtime state stays portable and reviewable
- The orchestrator can evolve without hiding the source of truth in opaque storage

Human-readable content belongs in markdown; machine-owned identity and runtime contracts
belong in small structured manifests like `case.yaml`.

## Runtime layers

| Layer | Responsibility |
|------|-----------------|
| Primary entrypoint | user-facing commands for intake, status, orchestration, and verification |
| Core orchestrator | case lifecycle, retries, queue truth, proof gating, artifact recording |
| Adapter layer | target-family normalization, loader integration, proof-spec selection |
| Tool runners | compilers, analyzers, objdiff, permuter, future sidecars |
| Shared workspace | durable case state and artifacts visible to both user and agent |
| Parity surfaces | slash commands, MCP tools, UI, injected context |

## Truth and parity rules

The product should obey these rules:

- One canonical state model feeds CLI, help, queue, context injection, and future UI.
- A user-visible action should have an equivalent runtime/tool path for the agent.
- New surfaces should not invent parallel state or a separate workflow contract.
- Proof state is authoritative; plausible output is not success.

## Current adapter story

Today:

- `odyssey` is the first implemented adapter family
- `case.yaml` carries normalized intake and proof data
- `settings.yaml` remains the strict Mizuchi compatibility contract
- shared `context/ctx.h` is still a legacy bridge detail in some flows

The next generalization step is to add a second adapter family without changing the
orchestrator's top-level workflow.

## Packaging direction

The long-term runtime should be shippable as one local-first product with explicit
ownership of:

- app-owned runtime assets
- discovered or bundled analyzers
- case workspaces
- caches and machine-owned state

That may still involve sidecars or optional tool discovery, but the product should
present one entrypoint and one truthful state model regardless.
