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
) -> dict[str, Any]:
    tools = capabilities.get("tools", {})
    local = capabilities.get("localSurfaces", {})
    available = {name for name, item in tools.items() if item.get("available")}
    inventory = inventory or {}
    functions = functions or {}
    source_generation = source_generation or {}
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
            "effect": "compiler/objdiff verification can start on generated decompiler/model output" if generated_sources else "source generation is blocked until decompiler/model/programmatic candidate output exists",
        },
    ]
    source_status = str(source_generation.get("status") or "missing")
    if generated_sources:
        match_status = "needs-verification"
    elif source_status in {"blocked", "queued-no-source", "missing"}:
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
        "schema": "mizuchi.recovery-strategy.v1",
        "target": target.to_json(),
        "inventorySummary": inventory_summary,
        "functionCandidateSummary": function_summary,
        "sourceGenerationSummary": {
            "status": source_generation.get("status"),
            "generatedSourceCandidates": source_generation.get("generatedSourceCandidates"),
            "taskCount": source_generation.get("taskCount"),
            "blockers": source_generation.get("blockers", []),
        },
        "inventoryDecisions": inventory_decisions,
        "methodology": "plan-first evidence pipeline: identify target, infer compiler/toolchain, recover boundaries/types, generate candidates, verify with objdiff, then assemble/relink only after per-slice proof",
        "requiredSemanticInputs": required_semantic_inputs,
        "lanes": lanes,
        "blockers": blockers,
        "claimBoundary": "first-run whole-app semantic source parity is unproven until every generated source artifact rebuilds through the selected toolchain and passes object/executable parity gates",
    }
