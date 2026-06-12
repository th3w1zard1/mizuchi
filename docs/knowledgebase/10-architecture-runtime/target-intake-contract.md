# Target Intake Contract

This document defines how Mizuchi normalizes user input into a stable case workspace.

## Product rule

The operator should be able to point Mizuchi at a target input without first deciding
which internal script graph or storage shape the repo expects. Intake is responsible for
capturing that original input shape, selecting an adapter, and materializing a
`prompts/<case-id>/case.yaml` manifest that the rest of the runtime can trust.

That input shape is not limited to a bare binary. The current product direction also
includes importing an existing prompt directory or direct `case.yaml` so the Rust runtime
can preserve previously recovered proof metadata instead of downgrading the workflow back
to raw-file probing.

## Normalized intake fields

Each case manifest must record:

- `adapter.id` — the adapter selected for the case
- `adapter.capabilitiesProfile` — the capability bundle or runtime profile attached to that adapter
- `ingest.sourceType` — original input kind such as `binary`, `project`, `archive`, or `directory`
- `ingest.sourcePath` — the original user-supplied path or extracted path that became the case
- `ingest.provenance` — how the runtime obtained the effective target (`direct`, `extracted`, `fixture`, etc.)
- `load.tool` — loader/analyzer surface used to inspect the case
- `load.programPath` — loader-facing program path
- `load.contextPath` — current compiler-visible context bundle or header path
- `target.*` — normalized target identity for downstream orchestration
- `proof.*` — proof artifact and comparator contract

## Why `ingest` and `target` are both present

They are not the same thing.

- `ingest.*` describes what the user pointed Mizuchi at.
- `target.*` describes the normalized execution target that downstream phases operate on.

For direct binary workflows those may look identical. For extracted archives, unpacked
firmware, or multi-program projects they will diverge, and that distinction is necessary
for reproducibility.

For imported `case.yaml` workflows, the runtime may also choose a different local
analysis artifact than `target.binary` when that is the most truthful exact-match input.
Example: if `proof.targetObjectPath` exists locally and the original program path is
remote or unavailable, Mizuchi may analyze the proof object while preserving the case's
original `ingest.*` and `target.*` identity.

## Adapter boundary

Adapters should stay narrow. A case should be normalizable if the adapter can answer:

- what input shape was provided
- what target identity should be used for downstream execution
- what loader/analyzer surface owns discovery
- what proof artifact is authoritative
- what workspace/context assets need to be materialized locally

Current repo status:

- `odyssey` is the first implemented adapter family
- `case.yaml` now carries enough intake metadata to support a second family without
  redesigning the manifest shape
