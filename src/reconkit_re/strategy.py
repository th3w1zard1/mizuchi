"""Evidence-based recovery strategy planning."""

from __future__ import annotations

from typing import Any

from .targets import TargetIdentity


def build_strategy(
    target: TargetIdentity,
    capabilities: dict[str, Any],
    inventory: dict[str, Any] | None = None,
    functions: dict[str, Any] | None = None,
    source_generation: dict[str, Any] | None = None,
    source_synthesis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tools = capabilities.get("tools", {})
    local = capabilities.get("localSurfaces", {})
    available = {name for name, item in tools.items() if item.get("available")}
    inventory = inventory or {}
    functions = functions or {}
    source_generation = source_generation or {}
    source_synthesis = source_synthesis or {}
    inventory_summary = inventory.get("summary", {})
    function_summary = functions.get("summary", {})
    required_semantic_inputs = [
        "function-boundary inventory",
        "compiler family/version/optimization hypothesis",
        "ABI/calling convention model",
        "relocation/import/global data model",
        "type and layout hypotheses",
        "candidate generator or decompiler output",
        "object-level verifier such as objdiff",
    ]
    blockers = []
    if "objdiff" not in available:
        blockers.append("objdiff unavailable: cannot make matching-decompilation acceptance claims")
    if target.format == "pe" and "wine" not in available:
        blockers.append("wine unavailable: Windows compiler/toolchain probing may be unavailable")
    if not local.get("oneShotSource"):
        blockers.append("generic byte-authority package generator missing")
    if inventory.get("status") != "complete":
        blockers.append("binary inventory incomplete: cannot derive code/data/import layout")

    code_ranges = int(inventory_summary.get("codeRanges") or 0)
    imports = int(inventory_summary.get("imports") or 0)
    function_symbols = int(inventory_summary.get("functionSymbols") or 0)
    symbols = int(inventory_summary.get("symbols") or 0)
    function_candidates = int(function_summary.get("candidateCount") or 0)
    high_confidence_candidates = int((function_summary.get("byConfidence") or {}).get("high") or 0)
    medium_confidence_candidates = int((function_summary.get("byConfidence") or {}).get("medium") or 0)
    generated_sources = int(source_generation.get("generatedSourceCandidates") or 0)
    semantic_sources = int(source_generation.get("semanticSourceCandidates") or 0)
    nonsemantic_bootstrap_sources = int(source_generation.get("nonSemanticBootstrapCandidates") or 0)
    high_level_sources = int(source_generation.get("highLevelSourceCandidates") or 0)
    inline_asm_sources = int(source_generation.get("inlineAsmSourceCandidates") or 0)
    byte_emission_sources = int(source_generation.get("byteEmissionSourceCandidates") or 0)
    synthesized_sources = int(source_synthesis.get("generatedCandidates") or 0)
    semantic_synthesized_sources = int(source_synthesis.get("semanticGeneratedCandidates") or 0)
    nonsemantic_synthesized_sources = int(source_synthesis.get("nonSemanticBootstrapCandidates") or 0)
    code_slice_matches = int(source_synthesis.get("codeSliceMatchedCandidates") or 0)
    semantic_code_slice_matches = int(source_synthesis.get("semanticCodeSliceMatchedCandidates") or 0)
    nonsemantic_code_slice_matches = int(source_synthesis.get("nonSemanticCodeSliceMatchedCandidates") or 0)
    semantic_mismatches = int(source_synthesis.get("semanticMismatchedCandidates") or 0)
    source_shape_searches = int(source_synthesis.get("sourceShapeSearches") or 0)
    source_shape_search_matches = int(source_synthesis.get("sourceShapeSearchMatches") or 0)
    accepted_synthesis_matches = int(source_synthesis.get("acceptedCandidates") or 0)
    target_slices = int(source_generation.get("targetSlices") or 0)
    function_fact_artifacts = source_generation.get("functionFactArtifacts") or {}
    compiler_profile_artifacts = source_generation.get("compilerProfileArtifacts") or {}
    normalized_facts = int(function_fact_artifacts.get("factCount") or 0)
    compiler_profile_status = str(compiler_profile_artifacts.get("status") or "missing")
    inventory_decisions = [
        {
            "input": "code ranges",
            "value": code_ranges,
            "effect": "candidate function discovery can be scoped to executable ranges" if code_ranges else "function discovery is blocked until executable ranges are known",
        },
        {
            "input": "imports",
            "value": imports,
            "effect": "external calls and runtime/library fingerprints can inform compiler and ABI hypotheses" if imports else "import-based compiler/runtime fingerprinting is weak",
        },
        {
            "input": "function symbols",
            "value": function_symbols,
            "effect": "symbol-backed function slicing can seed matching" if function_symbols else "function boundaries must be recovered from disassembly/decompiler analysis",
        },
        {
            "input": "function candidates",
            "value": function_candidates,
            "effect": "candidate queue can be initialized from discovered boundaries" if function_candidates else "no function queue can be initialized yet",
        },
        {
            "input": "automatic source candidates",
            "value": generated_sources,
            "effect": (
                f"{high_level_sources} high-level C candidate(s), {inline_asm_sources} inline-asm C candidate(s), {byte_emission_sources} byte-emission assembly candidate(s), {nonsemantic_bootstrap_sources} nonsemantic bootstrap candidate(s)"
                if generated_sources
                else "source generation is blocked until decompiler/model/programmatic candidate output exists"
            ),
        },
        {
            "input": "synthesized source candidates",
            "value": synthesized_sources,
            "effect": (
                f"{semantic_synthesized_sources} semantic candidate(s), {nonsemantic_synthesized_sources} nonsemantic bootstrap candidate(s) entered compile/objdiff"
                if synthesized_sources
                else "generated source tasks have not produced compiler-verifiable candidates yet"
            ),
        },
        {
            "input": "code-slice objdiff matches",
            "value": code_slice_matches,
            "effect": (
                f"{semantic_code_slice_matches} semantic match(es), {nonsemantic_code_slice_matches} nonsemantic bootstrap match(es)"
                if code_slice_matches
                else "no generated source has matched bounded target bytes yet"
            ),
        },
        {
            "input": "semantic source mismatches",
            "value": semantic_mismatches,
            "effect": (
                f"semantic candidates compile but do not match current compiler/profile bytes; {source_shape_searches} source-shape search(es), {source_shape_search_matches} source-shape match(es)"
                if semantic_mismatches
                else "no semantic compiler/profile mismatches recorded"
            ),
        },
        {
            "input": "target slices",
            "value": target_slices,
            "effect": "candidate generation has exact code bytes for bounded functions" if target_slices else "matching work is still missing exact function byte slices",
        },
        {
            "input": "normalized function facts",
            "value": normalized_facts,
            "effect": "agent context can include structured prototypes, calls, globals, stack, and control-flow evidence" if normalized_facts else "agent context is still mostly raw binary/decompiler text",
        },
        {
            "input": "compiler profile artifacts",
            "value": compiler_profile_status,
            "effect": "compiler/flag evidence can rank source candidates" if compiler_profile_status == "available" else "compiler/flag ranking is still underconstrained",
        },
    ]
    source_status = str(source_generation.get("status") or "missing")
    if accepted_synthesis_matches:
        match_status = "target-object-objdiff-match"
    elif semantic_code_slice_matches:
        match_status = "semantic-code-slice-evidence"
    elif code_slice_matches:
        match_status = "nonsemantic-code-slice-evidence"
    elif semantic_synthesized_sources and semantic_mismatches:
        match_status = "semantic-source-needs-compiler-profile"
    elif generated_sources:
        match_status = "needs-verification"
    elif not generated_sources and source_status in {"blocked", "queued-no-source", "missing"}:
        match_status = "needs-automatic-source-generation"
    elif high_confidence_candidates:
        match_status = "ready-for-symbol-slices"
    elif medium_confidence_candidates or function_candidates:
        match_status = "needs-boundary-refinement"
    else:
        match_status = "needs-function-boundaries"

    lanes = [
        {
            "name": "byte-authority",
            "status": "available" if local.get("oneShotSource") else "missing",
            "claim": "byte-exact source/emitter package, not semantic recovery",
            "appliesTo": ["unknown", "pe", "elf", "macho"],
        },
        {
            "name": "matching-decompilation",
            "status": "blocked" if blockers else match_status,
            "claim": "per-function high-level source accepted only by objdiff zero",
            "appliesTo": ["pe", "elf", "macho"],
        },
        {
            "name": "whole-program-relink",
            "status": "research-required",
            "claim": "translation-unit, data, libraries, linker order, and build-system parity",
            "appliesTo": ["pe", "elf", "macho"],
        },
    ]
    return {
        "schema": "reconkit.recovery-strategy.v1",
        "target": target.to_json(),
        "inventorySummary": inventory_summary,
        "functionCandidateSummary": function_summary,
        "sourceGenerationSummary": {
            "status": source_generation.get("status"),
            "generatedSourceCandidates": source_generation.get("generatedSourceCandidates"),
            "semanticSourceCandidates": source_generation.get("semanticSourceCandidates"),
            "nonSemanticBootstrapCandidates": source_generation.get("nonSemanticBootstrapCandidates"),
            "highLevelSourceCandidates": source_generation.get("highLevelSourceCandidates"),
            "inlineAsmSourceCandidates": source_generation.get("inlineAsmSourceCandidates"),
            "byteEmissionSourceCandidates": source_generation.get("byteEmissionSourceCandidates"),
            "generatedByLanguage": source_generation.get("generatedByLanguage"),
            "semanticByLanguage": source_generation.get("semanticByLanguage"),
            "generatedBySourceQuality": source_generation.get("generatedBySourceQuality"),
            "generatedByRule": source_generation.get("generatedByRule"),
            "semanticByRule": source_generation.get("semanticByRule"),
            "sourceCoverageArtifacts": source_generation.get("sourceCoverageArtifacts"),
            "taskCount": source_generation.get("taskCount"),
            "targetSlices": source_generation.get("targetSlices"),
            "functionFactArtifacts": function_fact_artifacts,
            "compilerProfileArtifacts": compiler_profile_artifacts,
            "blockers": source_generation.get("blockers", []),
        },
        "sourceSynthesisSummary": {
            "status": source_synthesis.get("status"),
            "compiler": source_synthesis.get("compiler"),
            "generatedCandidates": source_synthesis.get("generatedCandidates"),
            "semanticGeneratedCandidates": source_synthesis.get("semanticGeneratedCandidates"),
            "nonSemanticBootstrapCandidates": source_synthesis.get("nonSemanticBootstrapCandidates"),
            "attemptedCandidates": source_synthesis.get("attemptedCandidates"),
            "acceptedCandidates": source_synthesis.get("acceptedCandidates"),
            "codeSliceMatchedCandidates": source_synthesis.get("codeSliceMatchedCandidates"),
            "semanticCodeSliceMatchedCandidates": source_synthesis.get("semanticCodeSliceMatchedCandidates"),
            "nonSemanticCodeSliceMatchedCandidates": source_synthesis.get("nonSemanticCodeSliceMatchedCandidates"),
            "semanticMismatchedCandidates": source_synthesis.get("semanticMismatchedCandidates"),
            "verifyPackagedSource": source_synthesis.get("verifyPackagedSource"),
            "generatedBySourceQuality": source_synthesis.get("generatedBySourceQuality"),
            "attemptedBySourceQuality": source_synthesis.get("attemptedBySourceQuality"),
            "semanticCodeSliceMatchedBySourceQuality": source_synthesis.get("semanticCodeSliceMatchedBySourceQuality"),
            "semanticMismatchedBySourceQuality": source_synthesis.get("semanticMismatchedBySourceQuality"),
            "compileFailedBySourceQuality": source_synthesis.get("compileFailedBySourceQuality"),
            "errorBySourceQuality": source_synthesis.get("errorBySourceQuality"),
            "sourceShapeSearches": source_synthesis.get("sourceShapeSearches"),
            "sourceShapeSearchMatches": source_synthesis.get("sourceShapeSearchMatches"),
            "sourceTasks": source_synthesis.get("sourceTasks"),
            "promotionTargetsPath": source_synthesis.get("promotionTargetsPath"),
        },
        "verificationLadder": [
            {"tier": "candidate-queued", "acceptedSource": False},
            {"tier": "function-facts-normalized", "acceptedSource": False},
            {"tier": "target-slice-acquired", "acceptedSource": False},
            {"tier": "source-generated-unverified", "acceptedSource": False},
            {"tier": "object-compilable", "acceptedSource": False},
            {"tier": "code-slice-match", "acceptedSource": False},
            {"tier": "relocation-aware-slice-match", "acceptedSource": False},
            {"tier": "target-object-objdiff-match", "acceptedSource": True},
        ],
        "inventoryDecisions": inventory_decisions,
        "methodology": "plan-first evidence pipeline: identify target, infer compiler/toolchain, recover boundaries/types, generate candidates, verify with objdiff, then assemble/relink only after per-slice proof",
        "requiredSemanticInputs": required_semantic_inputs,
        "lanes": lanes,
        "blockers": blockers,
        "claimBoundary": "first-run whole-app semantic source parity is unproven until every generated source artifact rebuilds through the selected toolchain and passes object/executable parity gates",
    }
