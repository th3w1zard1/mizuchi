# Workspace Contract

This document defines the stable on-disk contract for a decompilation case. The current
workspace still uses article-faithful scripts, but new automation should target this
contract rather than inventing per-script state.

## Case directory

Each case lives under:

```text
prompts/<case-id>/
```

Required files:

- `case.yaml` — stable case identity for the app/orchestrator
- `settings.yaml` — strict Mizuchi tool contract for the current workflow
- `prompt.md` — agent brief and working context
- `notes.md` — human notes, blockers, hypotheses, and commentary

## `case.yaml`

`case.yaml` is the architecture-level case manifest. It is broader than
`settings.yaml`, but intentionally small enough to stay stable as the runtime evolves.

Required shape:

```yaml
schemaVersion: 1
caseId: fun_00148020
adapter:
  id: odyssey
  capabilitiesProfile: ghidra-mizuchi-v1
ingest:
  sourceType: binary
  sourcePath: /TSL/k2_xbox_default.xbe
  provenance: direct
target:
  family: odyssey
  binary: /TSL/k2_xbox_default.xbe
  platform: xbox
load:
  tool: agdec-http
  analysisProviders:
    - id: agdec-http
      role: surface
      detail: AgentDecompile HTTP bridge surface used by the current Odyssey workflow.
    - id: ghidra
      role: analyzer
      detail: Ghidra project import, analysis, and scripting backend behind the bridge.
  programPath: /TSL/k2_xbox_default.xbe
  contextPath: context/ctx.h
symbol:
  name: FUN_00148020
  locator: "0x00148020"
proof:
  targetObjectPath: build/xbox/fun_00148020.o
  source: golden-object
  comparator: objdiff
workspace:
  promptPath: prompts/fun_00148020
  buildDir: build
```

Field intent:

- `caseId` — stable workspace identifier; must match the prompt folder name
- `adapter.id` — selected adapter/runtime family that will normalize and execute the case
- `adapter.capabilitiesProfile` — named capability bundle the orchestrator can reason about without inspecting target-specific prose
- `ingest.sourceType` — the user-facing input shape (`binary`, `project`, `archive`, etc.)
- `ingest.sourcePath` — the original input path the operator pointed Mizuchi at
- `ingest.provenance` — whether the case came from a direct path, extraction step, generated fixture, or another upstream source
- `target.family` — adapter family (`odyssey`, `elf-ps2`, `pe-win32`, etc.)
- `target.binary` — binary/module identity used during discovery
- `target.platform` — platform/toolchain family for the case
- `load.tool` — tool surface used to load or inspect the case (`agdec-http`, `ghidra`, etc.)
- `load.analysisProviders` — ordered provider chain that explains which public surface and backing analyzers/parsers are involved
- `load.programPath` — concrete program/module path used by the loader
- `load.contextPath` — current compiler-visible context path or bundle used by the workflow
- `symbol.name` — linker or analysis-facing symbol identifier
- `symbol.locator` — address, offset, or equivalent locator string
- `proof.targetObjectPath` — golden object path used by the proof gate
- `proof.source` — the proof artifact family currently expected by the case (`golden-object`, etc.)
- `proof.comparator` — the verification primitive (`objdiff`, future comparators, etc.)
- `workspace.promptPath` — canonical workspace-relative case path
- `workspace.buildDir` — prompt-local artifact directory

## `settings.yaml`

`settings.yaml` stays strict because it is the current tool contract:

- `functionName`
- `targetObjectPath`
- `asm`

`case.yaml` and `settings.yaml` must agree on symbol name and golden object path.

## Derived state

Case state is **derived from artifacts**, not declared authoritatively in `notes.md`.

Allowed lifecycle labels in human-facing docs:

- `queued`
- `in_progress`
- `blocked`
- `matched`
- `integrated`

Rules:

- `matched` requires a proof artifact and a passing `objdiff` gate
- `integrated` requires a verified match plus target-tree landing
- `blocked` may be declared by notes when prerequisites are missing

Today, `scripts/validate-prompt-status.sh` enforces the strongest part of that rule:
no prompt may claim `matched` without proof artifacts.

## Run artifacts

Prompt-local runtime output belongs under:

```text
prompts/<case-id>/build/
```

Expected artifact families:

- `get-context.log`
- `compile.log`
- `candidate.o`
- `m2c.c`
- `permuter-best.c`
- assembly dumps / diff summaries
- future machine-readable run metadata (`run.json`, `verification.json`)
- project-level verifier config (`objdiff.json`)

Not every file exists on every run. The contract is the directory boundary and the
artifact family names, not a requirement that all files always exist.

For the Rust orchestrator output workspace, `objdiff.json` may exist even when no golden
object is known yet. In that state it should still contain truthful project metadata,
watch/ignore patterns, and scratch compiler/context hints, but its `units` array must stay
empty until a real proof target is recovered.

## Machine-owned JSON state

The Rust orchestrator extends the prompt-era contract with machine-owned state files:

- `analysis.json` — target-native inventories and evidence
- `reconstruction.json` — source candidates plus project structure
- `cfg-evidence.json` — function-boundary CFG readiness and unresolved control-flow evidence
- `type-relations.json` — symbol, demangling, RTTI, vtable, and unresolved type evidence
- `dependency-graph.json` — import, export, relocation, runtime, and linker-input evidence
- `build-plan.json` — build requirements, compiler profiles, and build units
- `proof-targets.json` — per-build-unit proof-target mapping from configured proof artifacts
- `build-unit-verification.json` — per-build-unit proof-attribution and promotion gate
- `source-audit.json` — source-output policy audit for generated candidate artifacts
- `source-verification.json` — source promotion ledger from scaffold/candidate to
  byte-proved or verified recovered source
- `build-graph.json` — evidence-derived dependency graph for compile/link/proof edges
- `build-manifest.json` — evidence-derived build-system manifest
- `toolchain-manifest.json` — evidence-derived compiler/linker/runtime ownership manifest
- `compiler-invocation.json` — exact compiler command recovery ledger
- `attempt-matrix.json` — machine-readable profile/backend rebuild-readiness matrix
- `upstream-evidence.json` — machine-readable upstream source references used to justify
  analysis/proof/target-model boundaries
- `compiler-compatibility.json` — public-source/proprietary compiler-family compatibility ledger
- `build-system/` — generated backend sketches such as `Makefile.generated` or
  `build.ninja.generated`
- `verification.json` — proof checks, failure classes, match scoring, and
  per-build-unit proof execution rows
- `verification-matrix.json` — derived authoritative/advisory/policy proof matrix
- `roundtrip.json` — end-to-end source→build→object/binary equivalence proof chain
- `byte-equivalence.json` — raw byte and native inventory equivalence ledger
- `drift-analysis.json` — classified decompilation drift, proof gaps, and infra gaps
- `uncertainty.json` — machine-readable unknowns and blocked conditions

### `reconstruction.json`

`reconstruction.json` must carry a `projectStructure` object. This is not a guessed
source tree; it is a truthful compile/link boundary model for the current case.

Expected fields:

- `sourceRoots`
- `includeRoots`
- `buildRoots`
- `artifactRoots`
- `translationUnits`
- `linkUnits`
- `notes`

Each translation unit records:

- `id`
- `sourcePath`
- `objectPath`
- `language`
- `kind`
- `status`
- `proofTarget`
- `compilerProfileCandidates`
- `blockingReasons`
- `evidence`

`status` may remain `blocked` when only a scaffold exists, or move to `candidate` when an
imported prompt-case artifact such as `trial.c` has been preserved into the workspace.
`candidate` still means "not verified recovered source."

Each link unit records:

- `id`
- `artifactPath`
- `kind`
- `status`
- `linkerProfileCandidates`
- `dependencyLibraries`
- `linkInputs`
- `runtimeArtifacts`
- `blockingReasons`

### `analysis.json`

`analysis.json` is the native-evidence intake record. It must describe what was observed in
the binary, not what Mizuchi wishes were true.

Expected target fields include:

- `fileKind`
- `architecture`
- `endianness`
- `platformFingerprint`
- `sections`
- `segments`
- `symbols`
- `dynamicSymbols`
- `functions`
- `relocations`
- `imports`
- `exports`
- `archiveKind`
- `archiveIsThin`
- `archiveMembers`
- `debug`
- `toolchain`

`platformFingerprint` records the evidence-derived binary interface boundary:

- `objectFormat`
- `pointerWidthBits`
- `vendor`
- `operatingSystem`
- `environment`
- `binaryInterfaceHypotheses`
- `tripleCandidates`

Triple candidates and ABI/interface hypotheses are allowed to remain partially unknown.
They exist to keep compiler and backend selection truthful when exact platform recovery
has not happened yet.

`functions` records symbol-derived function boundary evidence. Each entry may include a
name, address locator, size, source, confidence, `cfgStatus`, and supporting evidence.
These are analysis facts only: a function boundary does not imply recovered source,
verified control flow, or a matched rebuild.

For archive/static-library targets, `analysis.json` may also include member-level rows:

- `archiveKind` — parsed archive family such as GNU/BSD/COFF when detectable
- `archiveIsThin` — whether the archive is a thin archive
- `archiveMembers` — object-member inventory rows used to seed truthful multi-unit
  reconstruction scaffolding

Each `archiveMembers` row may include:

- `id`
- `name`
- `fileKind`
- `architecture`
- `endianness`
- `sizeBytes`
- `sha256`
- `objectFormat`
- `parserStatus`
- `isThin`
- `sectionCount`
- `symbolCount`
- `functionCount`
- `relocationCount`
- `importCount`
- `exportCount`
- `date`
- `uid`
- `gid`
- `mode`
- `evidence`

Archive member rows are evidence-only metadata. They must not be presented as recovered
source, but they can drive per-member translation units and build units for static-library
workspaces.

### `cfg-evidence.json`

`cfg-evidence.json` records control-flow graph readiness without fabricating basic blocks
or edges. It is the contract for separating observed function boundaries from recovered
CFGs and for explaining why CFG comparison is blocked.

Expected fields:

- `status`
- `functionCount`
- `recoveredFunctionCount`
- `unresolvedFunctionCount`
- `edgeCount`
- `functions`
- `edges`
- `comparisonReadiness`
- `uncertainty`

Each function row records:

- `name`
- `status`
- `confidence`
- `boundarySource`
- `basicBlockCount`
- `edgeCount`
- `evidence`
- `missingEvidence`

`comparisonReadiness` records whether target and candidate CFGs exist and which artifact
will carry the comparison once available. Current Rust output may record symbol-derived
function rows while keeping `basicBlockCount`, `edgeCount`, and `edges` unresolved. This
is intentional: function boundaries are not CFGs, and Mizuchi must not invent branch
targets, fallthrough edges, calls, exception edges, or switch structure from names alone.

### `type-relations.json`

`type-relations.json` records symbol/type relationship evidence without pretending that
Mizuchi has recovered source-level types. It is the contract for names, mangling schemes,
function-signature candidates, RTTI/vtable evidence, template/generic hints, and type
uncertainty.

Expected fields:

- `status`
- `symbolCount`
- `typeCandidateCount`
- `relationshipCount`
- `unresolvedTypeCount`
- `symbols`
- `typeCandidates`
- `relationships`
- `uncertainty`

Each symbol node records:

- `name`
- `kind`
- `scope`
- `address`
- `size`
- `demangleStatus`
- `demangledName`
- `namespace`
- `evidence`

Each type candidate records:

- `kind`
- `status`
- `confidence`
- `sourceSymbols`
- `evidence`

Known mangling or RTTI/vtable markers are evidence only. `demangleStatus` values such as
`mangled-itanium-unresolved`, `mangled-msvc-unresolved`, or
`mangled-rust-unresolved` must not be treated as recovered type names. Class layouts,
template parameters, calling conventions, parameter types, return types, and inheritance
relationships remain unresolved until backed by debug information, ABI analysis,
decompiler/type-analysis output, and rebuild/diff proof.

### `dependency-graph.json`

`dependency-graph.json` records dependency and linker-input evidence without pretending
that Mizuchi has recovered a complete build environment. It is the contract for imports,
exports, relocation edges, runtime/debug sidecars, and unresolved link requirements.

Expected fields:

- `status`
- `importCount`
- `exportCount`
- `relocationEdgeCount`
- `runtimeArtifactCount`
- `unresolvedDependencyCount`
- `imports`
- `exports`
- `relocationEdges`
- `runtimeArtifacts`
- `linkRequirements`
- `uncertainty`

Each import records:

- `library`
- `symbol`
- `status`
- `confidence`
- `evidence`

Each relocation edge records:

- `section`
- `offset`
- `target`
- `kind`
- `status`
- `evidence`

Each link requirement records:

- `kind`
- `name`
- `status`
- `blockers`
- `evidence`

Dependency entries are evidence only. Imported symbols, export addresses, relocation
offsets, runtime components, and debug sidecars may be recorded as metadata locators, but
they must not become hardcoded source logic or fabricated linker commands. Exact library
paths, versions, search order, sysroots, import libraries, startup objects, and linker
scripts remain unresolved until backed by rebuild and diff proof.

### `build-plan.json`

`build-plan.json` must carry `buildUnits` alongside the top-level toolchain and artifact
fields. A build unit records the source/object/proof boundary that must eventually be
compiled under an exact toolchain configuration.

Each build unit records:

- `id`
- `sourcePath`
- `objectPath`
- `language`
- `status`
- `proofTarget`
- `proofTargetStatus`
- `proofTargetLocator`
- `proofSourcePath`
- `compilerProfileCandidates`
- `dependencySymbols`
- `requiredInputs`
- `blockers`

For static-library inputs, `buildUnits` may contain one row per archive member while the
top-level `expectedArtifact.kind` / `linkPlan.kind` is `static-library`. This does not
imply that archive index behavior, librarian invocation, or per-member proof targets have
been recovered; those remain blocked until explicit evidence exists.

`build-plan.json` also carries:

- `toolchain.stages` — compile/assemble/link/runtime/configure stage requirements
- `linkPlan.inputs` — import-table and profile-derived linker/runtime inputs
- `linkPlan.runtimeArtifacts` — debug sidecars, UUIDs, CRT/runtime components, and other
  runtime-owned artifacts that must be reconciled before exact rebuild proof

`proofTargetStatus` records whether the current `proofTarget` is actually usable for that
unit. Current values include `mapped`, `unavailable`, `proof-source-missing`,
`proof-source-unparsed`, `missing-member-match`, and `thin-member-unavailable`.
Archive/static-library units may therefore point at extracted proof member objects such as
`proof/members/<member>.o` when mapping succeeds, or remain explicitly unmapped when the
configured proof artifact cannot be translated into member-level proof.

### `source-audit.json`

`source-audit.json` records whether generated source artifacts obey Mizuchi's source
output policy. It exists to prevent scaffold or guessed code from quietly becoming
"recovered source."

Expected fields:

- `status`
- `artifactCount`
- `blockedStubCount`
- `suspiciousCount`
- `artifacts`

Each artifact records:

- `path`
- `language`
- `kind`
- `verdict`
- `markedBlocking`
- `compileBlocking`
- `containsHardcodedAddress`
- `containsUnmarkedPlaceholder`
- `containsFabricationMarker`
- `evidence`

Current valid behavior is that generated unknown source may be a marked blocking stub
that fails compilation intentionally. Unmarked placeholders, hardcoded virtual
addresses, or fabricated behavior fail the audit. Imported prompt-case candidates such as
`trial.c` may pass the audit as `unverified-source`: they are valid rebuild inputs, but
they are not yet promoted to verified recovered source.

### `proof-targets.json`

`proof-targets.json` is the machine-owned mapping ledger between the configured proof
artifact and the build units in the current workspace. It exists so Mizuchi can represent
proof availability per build unit instead of flattening every case into one global proof
path.

Expected fields:

- `status`
- `sourcePath`
- `comparator`
- `collectionKind`
- `unitCount`
- `mappedUnitCount`
- `unavailableUnitCount`
- `units`

Each unit records:

- `buildUnitId`
- `proofSourcePath`
- `proofTarget`
- `kind`
- `status`
- `locator`
- `memberIndex`
- `memberName`
- `blockers`
- `evidence`

For single-unit direct-object cases, `proofTarget` may be the configured object path
itself. For archive/static-library cases, `proofTarget` may be a materialized member path
such as `proof/members/<member>.o` when Mizuchi can map a configured archive proof source
to a specific member. Thin archive members, unreadable proof archives, unmatched member
names, and opaque member payloads must remain explicit unavailable states instead of being
treated as passing proof surfaces.

### `build-unit-verification.json`

`build-unit-verification.json` is the machine-owned per-build-unit proof ledger. It ties
each compile boundary in `build-plan.json` to the current verification and exact-invocation
state so Mizuchi can say which units are truly proof-attributed, which are only
byte-proved candidates, which remain unverified, and which still have no proof target.

Expected fields:

- `status`
- `unitCount`
- `proofAttributedCount`
- `verifiedUnitCount`
- `byteProvedUnitCount`
- `unverifiedUnitCount`
- `proofUnavailableCount`
- `units`

Each unit records:

- `id`
- `sourcePath`
- `objectPath`
- `proofTarget`
- `status`
- `proofAttributed`
- `rebuildStatus`
- `objectMatchStatus`
- `binaryDiffStatus`
- `compilerInvocationStatus`
- `exactInvocationRecovered`
- `blockers`
- `evidence`

`status=byte-proved-build-unit` means the current source/object/proof tuple rebuilt and
matched, but exact compiler invocation recovery is still missing. It is not the same as a
verified recovered build unit.

For archive/static-library inputs, `units` may contain one row per member-backed build
unit while still reporting `status=proof-unavailable` for members that do not yet have a
real per-member proof target. This prevents one matched object from being misattributed to
the whole archive.

`verification.json` now carries the companion execution rows that feed this ledger. That
allows Mizuchi to record per-build-unit rebuild, objdiff, and native byte-comparison
results even when a top-level archive/package comparison is still unavailable. For common
non-thin `ar` archives whose raw regular members align with analyzed build units, Mizuchi
can preserve container evidence, rebuild the package artifact, and record a top-level
package comparison once the rebuilt archive exists.

### `source-verification.json`

`source-verification.json` is the machine-owned promotion ledger that sits after audit and
proof. It explains whether each source artifact is still a scaffold, merely an
unverified source, a byte-proved candidate, a policy violation, or a verified recovered
source.

Expected fields:

- `status`
- `artifactCount`
- `verifiedSourceCount`
- `byteProvedCandidateCount`
- `unverifiedSourceCount`
- `blockedStubCount`
- `policyViolationCount`
- `exactInvocationRecoveredCount`
- `proofAttributedCount`
- `artifacts`

Each artifact records:

- `path`
- `language`
- `kind`
- `status`
- `auditVerdict`
- `sourceCandidateStatus`
- `buildUnitId`
- `proofAttributed`
- `rebuildStatus`
- `objectMatchStatus`
- `binaryDiffStatus`
- `compilerInvocationStatus`
- `exactInvocationRecovered`
- `byteEquivalentProof`
- `verifiedRecoveredSource`
- `blockers`
- `evidence`

Source promotion is intentionally routed through `build-unit-verification.json`, not a
single workspace-global candidate/proof assumption. A source artifact can only become
`byte-proved-candidate` or `verified-recovered-source` when Mizuchi can attribute the
proof chain to that artifact's build unit.

`status=byte-proved-candidate` is intentionally not the same thing as recovered source.
It means the current candidate rebuilt and matched the current proof target, but Mizuchi
still lacks exact compiler invocation recovery and must keep the source blocked from final
promotion.

### `build-graph.json`

`build-graph.json` expresses the current evidence-derived dependency graph:

- toolchain stages
- function boundaries
- translation units
- link units
- compile-to-object edges
- object-to-proof edges

It is a truthful graph artifact, not a claim that Mizuchi has already recovered an
executable build.

### `build-manifest.json`

`build-manifest.json` is the generated build-system surface for the current Rust slice.
It must remain explicit when the build is not yet executable.

Expected fields:

- `generator`
- `target`
- `toolchain`
- `buildSystem`
- `translationUnits`
- `linkPlan`

`buildSystem` must also carry:

- `preferredBackend`
- `candidateBackends`
- `generatedArtifacts`

For now, `buildSystem.executable` must stay `false` unless exact commands, flags, library
paths, and environment are actually recovered and verified.

Generated backend artifacts are evidence-bearing sketches only. They may help downstream
tools or humans understand likely orchestration families such as `make`, `ninja`,
`msbuild`, `nmake`, or `xcodebuild`, but they must not pretend to be runnable until
Mizuchi has exact invocation proof.

### `toolchain-manifest.json`

`toolchain-manifest.json` is the machine-owned compiler ecosystem view for the current
case. It is not a recovered command database; it is the truthful inventory of likely
executables, runtime ownership, and stage coverage implied by the current evidence.

Expected fields:

- `platformFingerprint`
- `rankingStatus`
- `recommendedProfile`
- `selectedProfile`
- `hostResolutionSummary`
- `candidateProfiles`
- `executableComponents`
- `runtimeOwnership`
- `stageCoverage`
- `upstreamEvidence`
- `blockers`

Executable components may list candidate command names such as `cl.exe`, `link.exe`,
`clang`, `gcc`, `ld`, `lld`, `llvm-mc`, or historical toolchain binaries. These are
candidate identities only, not proof that the exact binary, version, or invocation has
been recovered. When Mizuchi can resolve a candidate on the current host, an
`installedCandidates` row may also include:

- `resolvedPath`
- `probeStatus`
- `versionProbe`
- `versionOutput`

`resolvedPath` is the host executable path Mizuchi found on `PATH`; it is not, by itself,
proof that the target binary used that executable. `probeStatus` records whether Mizuchi
only resolved a path or also captured a version banner. Version probing is optional and
currently opt-in through `DECOMP_PROBE_TOOL_VERSIONS=1` so tool fingerprinting can improve
without turning subprocess probing into an unconditional runtime cost.

This lets Mizuchi represent one-shot reconstruction workspaces truthfully even when exact
compiler flags, CRT inputs, or link scripts are not yet recovered.

Each candidate profile may include `upstreamEvidence` entries with:

- `evidenceScore`
- `evidenceConfidence`
- `rankingReasons`
- `appliesToProfiles`
- `apiUrl`
- `gitUrl`
- `htmlUrl`
- `downloadUrl`
- `verification`
- `rustPortStatus`

### `compiler-invocation.json`

`compiler-invocation.json` is the machine-owned ledger for exact compiler command
recovery. It is intentionally stricter than compiler profile ranking: a profile can be
plausible while every invocation remains unresolved.

Expected fields:

- `status`
- `candidateCount`
- `recoveredInvocationCount`
- `missingEvidence`
- `invocations`

Each invocation records:

- `profileId`
- `buildUnitId`
- `language`
- `status`
- `exactCommandRecovered`
- `sourcePath`
- `objectPath`
- `proofTarget`
- `toolCandidates`
- `argumentVector`
- `environment`
- `requiredEvidence`
- `blockers`
- `evidence`

`exactCommandRecovered` must stay `false` until Mizuchi has real compiler/linker
executable identity, version, argument vector, working directory, environment, include
paths, defines, sysroot, libraries, startup objects, and proof results. Empty
`argumentVector` means "not recovered"; it is not a placeholder command.

`toolCandidates[].installedCandidates[]` may also carry the same host-side
`resolvedPath` / `probeStatus` / `versionProbe` / `versionOutput` fields used by
`toolchain-manifest.json`. These are supporting evidence for host readiness only; they do
not upgrade an invocation into a recovered target command.

A nearby replay script or local wrapper also does not count as exact command recovery on
its own. Mizuchi may promote a configured replay command only after rebuild proof,
authoritative object/binary diff proof, and a source-sensitivity probe show that changing
the source changes or breaks the rebuilt artifact. Even then, the promoted row is evidence
for exact current-candidate replay; original compiler executable/version identity remains
unclaimed unless separately proven. Ghidra decompiler configuration and clang/gcc driver
sources show real subsystems around option setup, job construction, response files, and
environment-driven driver behavior, so replayability is useful proof input but not the
same claim as historical invocation recovery.

### `compiler-compatibility.json`

`compiler-compatibility.json` records which compiler families are currently modeled from
public source, which families are only analysis evidence, and which families remain
proprietary or unavailable gaps. It is derived from compiler profiles, upstream source
references, and `compiler-invocation.json`.

Expected fields:

- `status`
- `profileCount`
- `publicSourceModeledCount`
- `proprietaryGapCount`
- `exactInvocationRecovered`
- `selectedProfile`
- `recommendedProfile`
- `profiles`
- `blockers`
- `evidence`

Each profile records:

- `id`
- `family`
- `vendor`
- `compatibilityStatus`
- `sourceAvailability`
- `modelingBoundary`
- `exactInvocationStatus`
- `sourceSystems`
- `rustPortStatuses`
- `requiredComponents`
- `blockers`

Public compiler code such as GCC or LLVM may be used to model driver, target, runtime,
and linker compatibility boundaries. It must not be treated as a transplanted compiler
implementation or as proof of the exact historical compiler used by the target. Proprietary
or unavailable families such as MSVC internals, IDO, CodeWarrior, console SDK compilers,
and vendor linkers must stay explicit compatibility gaps until recovered from artifacts,
available binaries, documentation, or byte-equivalence proof.

### `attempt-matrix.json`

`attempt-matrix.json` records profile/backend-family rebuild rows without pretending that
an invocation has already been recovered.

Each row records:

- `profileId`
- `profileScore`
- `profileConfidence`
- `backendId`
- `rowStatus`
- `statusReason`
- `hostStatus`
- `proofStatus`
- `rebuildStatus`
- `exactInvocationStatus`
- `priority`
- `priorityClass`
- `priorityReasons`
- `nextAction`
- `executableComponents`
- `runtimeRequirements`
- `evidence`
- `blockers`

`executableComponents[].installedCandidates[]` may include host-side `resolvedPath` and
optional version banner fields so the attempt planner can separate "the host has a
plausible compiler family installed" from "the target's exact historical invocation is
recovered."

For imported prompt cases, `--match` may still run the deterministic rebuild and
verification path using a preserved candidate such as `trial.c`. That path can prove
object or byte equivalence for the current candidate without promoting source recovery or
exact invocation recovery to `passed`.

Current row statuses include:

- `infra_blocked`
- `proof_blocked`
- `scaffold_attempted`
- `verification_mismatch`
- `invocation_unresolved`
- `match_candidate`

The matrix `summary` also records:

- `actionableRows`
- `rankingStatus`
- `recommendedProfile`
- `selectedProfile`
- `priorityOrdered`
- `topRows`
- `nextActions`

### `upstream-evidence.json`

`upstream-evidence.json` is the machine-readable source-provenance catalog for the current
case. It must stay honest about which public upstream code Mizuchi is using as evidence,
and it must not imply that those sources prove binary equivalence by themselves.

Expected top-level fields:

- `catalogStatus`
- `systems`
- `verificationModes`
- `validationStatuses`
- `profileCoverage`
- `validation`
- `references`

Each reference may include:

- `system`
- `role`
- `appliesToProfiles`
- `repo`
- `path`
- `revision`
- `sourceKind`
- `sourceSha`
- `apiUrl`
- `gitUrl`
- `htmlUrl`
- `downloadUrl`
- `verification`
- `validationStatus`
- `matchedCatalogSource`
- `resolvedSourceSha`
- `resolvedHtmlUrl`
- `resolvedDownloadUrl`
- `validationEvidence`
- `rationale`
- `rustPortStatus`

If a compiler family is proprietary or otherwise unavailable, Mizuchi should emit a
structured unresolved record instead of fabricating a source reference.

`sourceSha` is the SHA returned by `gh api repos/<owner>/<repo>/contents/<path>` for
the referenced source file. `sourceKind` is usually `github-content-file`; unresolved
or proprietary compiler references use `unresolved`. A `rustPortStatus` value such as
`selective-port-via-rust-object-crate`, `model-toolchain-contract-only`, or
`adapter-evidence-only` means Mizuchi is translating only the contract or evidence model
needed for reproducible rebuild attempts. It must not be read as wholesale transpilation
of Ghidra, RetDec, LLVM, objdiff, or any compiler implementation.

`verification` describes the catalog entry itself, not a live network check. GitHub-backed
references use `catalog-reference` until they are explicitly revalidated. When
`DECOMP_VALIDATE_UPSTREAM_SOURCES=1` is set and `gh` is available, Mizuchi may attach a
`validation` summary and per-reference `validationStatus` rows such as `matched`,
`drifted`, `missing`, or `error`. This keeps research provenance auditable without making
GitHub availability a mandatory dependency for core decompilation workflows.

### `verification-matrix.json`

`verification-matrix.json` is a derived view over `verification.json` and the companion
ledgers. It exists so the one-shot workflow can separate byte/object proof from useful
but non-authoritative evidence.

Each row records:

- `name`
- `domain`
- `authority`
- `status`
- `weight`
- `score`
- `blocking`
- `artifact`
- `failureClass`
- `detail`

Current authority levels are:

- `authoritative` — object match, binary diff, and rebuild proof rows that can establish
  or block a byte-level match
- `advisory` — CFG, symbol/type, relocation, dependency, debug, and native inventory
  evidence that improves triage but cannot prove source equivalence alone
- `policy` — source-output and compiler-invocation rules that prevent fabricated
  recovery from being presented as matched source

Skipped or failed authoritative rows are blocking. Advisory rows may pass while the
project remains blocked. This is intentional: recovered inventories are evidence, not a
substitute for object or binary equivalence.

### `roundtrip.json`

`roundtrip.json` is the end-to-end proof ledger for the user-facing promise that recovered
source can be rebuilt and compared byte-for-byte against the target artifact. It is
derived from source verification, compiler invocation, rebuild, objdiff, binary diff, and
verification-matrix state.

Expected fields:

- `status`
- `byteEquivalent`
- `proofChainComplete`
- `candidateSource`
- `candidateArtifact`
- `proofTarget`
- `stages`
- `blockers`
- `evidence`

Current stages are:

- `source_recovery`
- `compiler_invocation`
- `rebuild`
- `object_match`
- `binary_diff`

`byteEquivalent` must remain `false` unless both authoritative object proof and native
binary byte comparison pass. A marked blocking source stub, missing exact compiler
invocation, failed rebuild, missing proof target, missing objdiff, or any byte/object
mismatch keeps the round-trip proof blocked. Passing advisory inventories do not make this
ledger pass. A byte-proved candidate still leaves `source_recovery` blocked until exact
compiler invocation recovery upgrades the artifact in `source-verification.json`.

For archive/static-library inputs, passing per-build-unit object and byte proof is still
not enough to mark `roundtrip.json` verified. Archive headers, member ordering, symbol
table/index behavior, and the final librarian output must also be rebuilt and compared as
the top-level artifact. Mizuchi currently performs that top-level package proof only for
common non-thin `ar` archives whose raw regular members align with analyzed build units;
thin archives and unsupported container layouts remain blocked instead of inferred.

### `byte-equivalence.json`

`byte-equivalence.json` is the narrow byte-identity ledger. It records whether the current
proof target and candidate artifact are byte-identical and whether native section, symbol,
and relocation inventories also agree. It is derived from `verification.json` and the
native artifact comparison; it does not run recovery or infer source behavior.

Expected fields:

- `status`
- `byteEquivalent`
- `comparisonAvailable`
- `proofTarget`
- `candidateArtifact`
- `targetFingerprint`
- `candidateFingerprint`
- `firstMismatchOffset`
- `sectionInventoryEqual`
- `symbolInventoryEqual`
- `relocationInventoryEqual`
- `blockingRows`
- `evidence`

`status=verified` requires an available artifact comparison, raw byte equality, equal
native inventories, and no blocking verification rows. If the proof target or candidate
object is missing, `comparisonAvailable` is `false`, fingerprints stay `null`, and the
ledger remains blocked or pending instead of treating infrastructure gaps as a semantic
decompilation mismatch.

For multi-unit archive/static-library cases, member-level byte comparisons may still be
present in `verification.json` and `build-unit-verification.json` while
`byte-equivalence.json` remains blocked. That is intentional: per-member byte proof does
not prove archive/package byte identity. When Mizuchi can rebuild the top-level archive
artifact itself, `byte-equivalence.json` may verify even while `source-verification.json`
remains partial because exact compiler invocation recovery is still unresolved.

### `drift-analysis.json`

`drift-analysis.json` classifies why the current reconstruction can drift from a
byte-equivalent rebuild. It is derived from existing ledgers and must not invent source,
types, compiler behavior, or proof.

Expected fields:

- `status`
- `driftCount`
- `blockingDriftCount`
- `categories`
- `items`
- `evidence`

Each item records:

- `id`
- `category`
- `failureClass`
- `status`
- `severity`
- `blocking`
- `summary`
- `sourceArtifact`
- `expectedProof`
- `blockers`
- `evidence`

Current categories include:

- `source`
- `compiler`
- `cfg`
- `type-layout`
- `dependency`
- `proof`
- `binary`
- `infrastructure`

Infrastructure drift is reported separately from decompilation difficulty. Missing tools
or analysis providers can block a run, but they must not be counted as semantic recovery
failure. CFG/type/dependency drift can be non-blocking advisory evidence until paired with
round-trip proof, while source, exact compiler invocation, proof-target, and binary-byte
drift are blocking for byte-equivalence claims.

## Legacy bridge note

Some current flows still depend on workspace-global assets such as `context/ctx.h`.
That is acceptable for the article-faithful bridge, but new orchestration should treat
prompt-local artifacts and the case manifest as the primary contract.
