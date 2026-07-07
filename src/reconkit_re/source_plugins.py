"""Concrete source-recovery plugins for the Python plugin pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .plugin_pipeline import PluginResult, now_ms
from .source_export import export_recovered_source
from .source_parity_synthesize import (
    append_packaged_fallback,
    attempt_candidate,
    compiler_compatible_candidates,
    filter_candidates_by_explicit_rule_strategies,
    filter_candidates_by_source_quality,
    generate,
    generated_candidate_source_quality,
    matched_source_shape_record,
    packaged_source_candidate,
    prioritize_candidates,
)


class SourceCandidateGeneratorPlugin:
    id = "source-candidate-generator"
    name = "Source Candidate Generator"
    description = "Generate and select semantic source candidates for a bounded target slice."

    def execute(self, context: dict[str, Any]) -> tuple[PluginResult, dict[str, Any]]:
        start = now_ms()
        row = context.get("sourceParityRow")
        if not isinstance(row, dict):
            return failure(self, start, "sourceParityRow missing from context"), context
        compiler = str(context.get("compiler") or "msvc")
        max_variants = int(context.get("maxVariantsPerFunction") or 8)
        raw_candidates = append_packaged_fallback(
            generate(row, max_variants),
            packaged_source_candidate(row),
        )
        strategies = normalize_filter_set(context.get("strategies"))
        raw_candidates, rule_filtered = filter_candidates_by_explicit_rule_strategies(raw_candidates, strategies)
        source_qualities = normalize_filter_set(context.get("sourceQualities"))
        effective_source_qualities = promotion_seed_qualities(source_qualities, bool(context.get("sourceShapeSearch")))
        raw_candidates, quality_filtered = filter_candidates_by_source_quality(raw_candidates, effective_source_qualities)
        candidates = compiler_compatible_candidates(row, raw_candidates, compiler, max_variants)
        if context.get("semanticOnly"):
            candidates = [candidate for candidate in candidates if candidate.semantic_source]
        candidates = prioritize_candidates(candidates)
        index = int(context.get("sourceCandidateIndex") or 0)
        if index >= len(candidates):
            return failure(
                self,
                start,
                f"no source candidate at index {index}; generated {len(candidates)} compatible candidate(s)",
                {"generatedCandidates": len(candidates), "candidateIndex": index},
            ), context
        selected = candidates[index]
        updated = dict(context)
        updated["sourceParityCandidates"] = candidates
        updated["selectedSourceCandidate"] = selected
        return (
            PluginResult(
                self.id,
                self.name,
                "success",
                now_ms() - start,
                data={
                    "generatedCandidates": len(candidates),
                    "candidateIndex": index,
                    "rule": selected.rule,
                    "variant": selected.variant,
                    "sourceQuality": generated_candidate_source_quality(selected),
                    "semanticSource": selected.semantic_source,
                    "filteredByExplicitRuleStrategies": rule_filtered,
                    "filteredBySourceQuality": quality_filtered,
                },
            ),
            updated,
        )

    def prepare_retry(
        self,
        context: dict[str, Any],
        _previous_attempts: list[dict[str, PluginResult]],
    ) -> dict[str, Any]:
        updated = dict(context)
        updated["sourceCandidateIndex"] = int(updated.get("sourceCandidateIndex") or 0) + 1
        return updated


class SourceCandidateObjdiffPlugin:
    id = "source-candidate-objdiff"
    name = "Source Candidate Objdiff"
    description = "Compile selected generated source and objdiff it against the bounded target slice."

    def execute(self, context: dict[str, Any]) -> tuple[PluginResult, dict[str, Any]]:
        start = now_ms()
        row = context.get("sourceParityRow")
        candidate = context.get("selectedSourceCandidate")
        if not isinstance(row, dict):
            return failure(self, start, "sourceParityRow missing from context"), context
        if candidate is None:
            return failure(self, start, "selectedSourceCandidate missing from context"), context
        out_dir = Path(str(context.get("outDir") or "target/plugin-source-pipeline"))
        records = attempt_candidate(
            row,
            candidate,
            out_dir,
            compiler=str(context.get("compiler") or "msvc"),
            clang=str(context.get("clang") or "clang"),
            compiler_profiles=list(context.get("compilerProfiles") or []),
            inventory=Path(str(context.get("inventory") or "target/swkotor-unpack/facts/function-inventory.jsonl")),
            vc_root=Path(str(context["vcRoot"])) if context.get("vcRoot") else None,
            wine=str(context.get("wine") or "wine"),
            wineprefix=Path(str(context["wineprefix"])) if context.get("wineprefix") else None,
            timeout=int(context.get("timeout") or 120),
            dry_run=bool(context.get("dryRun")),
            source_shape_search=bool(context.get("sourceShapeSearch")),
        )
        matches = [
            record
            for record in records
            if record.get("status") in {"matched", "code-slice-matched"} and optional_int(record.get("differences"), -1) == 0
        ]
        output_source_qualities = normalize_filter_set(context.get("sourceQualities"))
        exportable_matches = [
            record
            for record in matches
            if output_source_qualities is None or str(record.get("sourceQuality") or "") in output_source_qualities
        ]
        source_shape_matches = []
        for record in records:
            search_path = record.get("sourceShapeSearch")
            if search_path:
                source_shape_record = matched_source_shape_record(record, str(search_path))
                if source_shape_record is not None:
                    source_shape_matches.append(source_shape_record)
        best_difference = min((int(record.get("differences", 999999)) for record in records), default=999999)
        attempts_path = out_dir / "plugin-attempts.jsonl"
        out_dir.mkdir(parents=True, exist_ok=True)
        with attempts_path.open("a", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
        match_rows_path = Path(str(context.get("codeSliceMatchesPath"))) if context.get("codeSliceMatchesPath") else None
        if match_rows_path is not None and exportable_matches:
            match_rows_path.parent.mkdir(parents=True, exist_ok=True)
            with match_rows_path.open("a", encoding="utf-8") as fh:
                for record in exportable_matches:
                    fh.write(json.dumps(record, sort_keys=True) + "\n")
        source_shape_rows_path = Path(str(context.get("sourceShapeMatchesPath"))) if context.get("sourceShapeMatchesPath") else None
        if source_shape_rows_path is not None and source_shape_matches:
            source_shape_rows_path.parent.mkdir(parents=True, exist_ok=True)
            with source_shape_rows_path.open("a", encoding="utf-8") as fh:
                for record in source_shape_matches:
                    fh.write(json.dumps(record, sort_keys=True) + "\n")
        updated = dict(context)
        updated["sourceParityAttemptRecords"] = records
        updated["sourceParityMatchRecords"] = exportable_matches
        updated["sourceShapeMatchRecords"] = source_shape_matches
        updated["sourceParityAttemptsPath"] = str(attempts_path)
        status = "success" if exportable_matches or source_shape_matches else "failure"
        return (
            PluginResult(
                self.id,
                self.name,
                status,
                now_ms() - start,
                error=None if matches else f"no byte-identical source candidate; best differences={best_difference}",
                data={
                    "attemptCount": len(records),
                    "matchCount": len(matches),
                    "exportableMatchCount": len(exportable_matches),
                    "sourceShapeMatchCount": len(source_shape_matches),
                    "differenceCount": 0 if matches else best_difference,
                    "attemptsPath": str(attempts_path),
                    "bestStatus": matches[0].get("status") if matches else (records[0].get("status") if records else None),
                },
            ),
            updated,
        )


class RecoveredSourceExportPlugin:
    id = "recovered-source-export"
    name = "Recovered Source Export"
    description = "Export verified source matches from plugin-pipeline attempts into a bounded source shard."

    def execute(self, context: dict[str, Any]) -> tuple[PluginResult, dict[str, Any]]:
        start = now_ms()
        out_dir = Path(str(context.get("outDir") or "target/plugin-source-pipeline"))
        matches_path = out_dir / "plugin-code-slice-matches.jsonl"
        matches = list(context.get("sourceParityMatchRecords") or [])
        out_dir.mkdir(parents=True, exist_ok=True)
        with matches_path.open("w", encoding="utf-8") as fh:
            for record in matches:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
        export = export_recovered_source(
            [matches_path],
            out_dir=out_dir / "recovered-source",
            source_name="source_slices.c",
        )
        updated = dict(context)
        updated["recoveredSourceExport"] = export
        return (
            PluginResult(
                self.id,
                self.name,
                "success" if export.get("status") == "complete" else "failure",
                now_ms() - start,
                error=None if export.get("status") == "complete" else "no verified source matches to export",
                data=export,
            ),
            updated,
        )


def failure(plugin: Any, start: int, message: str, data: dict[str, Any] | None = None) -> PluginResult:
    return PluginResult(
        plugin.id,
        plugin.name,
        "failure",
        now_ms() - start,
        error=message,
        data=data or {},
    )


def optional_int(value: Any, default: int) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_filter_set(value: Any) -> set[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
    else:
        try:
            items = [str(part).strip() for part in value]
        except TypeError:
            items = [str(value).strip()]
    normalized = {item for item in items if item}
    return normalized or None


def promotion_seed_qualities(source_qualities: set[str] | None, source_shape_search: bool) -> set[str] | None:
    if not source_shape_search or not source_qualities or "high-level-c" not in source_qualities:
        return source_qualities
    return set(source_qualities) | {"inline-asm-c"}
