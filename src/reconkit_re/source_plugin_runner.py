"""Batch runner for the upstream-style source recovery plugin pipeline."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .plugin_pipeline import PluginPipeline
from .source_export import export_recovered_source
from .source_parity_synthesize import (
    append_packaged_fallback,
    clean_jsonl,
    compiler_compatible_candidates,
    filter_candidates_by_explicit_rule_strategies,
    filter_candidates_by_source_quality,
    generate,
    is_boundary_suspect,
    iter_jsonl,
    iter_source_task_rows,
    packaged_source_candidate,
    parse_profile_flag_set,
    strategy_allowed,
    synthesis_row_priority,
    write_json,
)
from .source_plugins import SourceCandidateGeneratorPlugin, SourceCandidateObjdiffPlugin


@dataclass
class SourcePluginRunConfig:
    queue: Path | None = None
    source_tasks: list[Path] = field(default_factory=list)
    source_tasks_only: bool = False
    out_dir: Path = Path("target/source-plugin-pipeline/swkotor")
    limit: int = 25
    offset: int = 0
    max_variants_per_function: int = 8
    max_retries: int = 8
    strategies: set[str] | None = None
    source_qualities: set[str] | None = None
    compiler: str = "msvc"
    clang: str = "clang"
    compiler_profiles: list[tuple[str, list[str]]] = field(default_factory=list)
    dry_run: bool = False
    semantic_only: bool = False
    skip_boundary_suspect: bool = False
    source_shape_search: bool = False
    clean: bool = False
    inventory: Path = Path("target/swkotor-unpack/facts/function-inventory.jsonl")
    vc_root: Path | None = None
    wine: str = "wine"
    wineprefix: Path | None = None
    timeout: int = 120
    progress_every: int = 0


def run_source_plugin_pipeline(config: SourcePluginRunConfig) -> dict[str, Any]:
    if config.clean and config.out_dir.exists():
        shutil.rmtree(config.out_dir)
    config.out_dir.mkdir(parents=True, exist_ok=True)
    attempts_path = config.out_dir / "plugin-attempts.jsonl"
    matches_path = config.out_dir / "plugin-code-slice-matches.jsonl"
    source_shape_matches_path = config.out_dir / "plugin-source-shape-matches.jsonl"
    events_path = config.out_dir / "plugin-events.jsonl"
    runs_path = config.out_dir / "plugin-runs.jsonl"
    for path in (attempts_path, matches_path, source_shape_matches_path, events_path, runs_path):
        clean_jsonl(path)

    rows = collect_rows(config)
    inspected = 0
    skipped = 0
    skipped_boundary = 0
    skipped_no_candidate = 0
    succeeded = 0
    failed = 0
    pipeline = PluginPipeline(max_retries=max(1, config.max_retries), event_handler=jsonl_event_handler(events_path))
    pipeline.register(SourceCandidateGeneratorPlugin(), SourceCandidateObjdiffPlugin())

    for row in rows:
        if inspected >= config.limit:
            break
        if not strategy_allowed(row, config.strategies, {}):
            continue
        if compatible_candidate_count(row, config) == 0:
            skipped_no_candidate += 1
            continue
        if skipped < config.offset:
            skipped += 1
            continue
        if config.skip_boundary_suspect and is_boundary_suspect(row):
            skipped_boundary += 1
            continue
        inspected += 1
        result = pipeline.run_pipeline(
            prompt_path=str(row.get("source") or row.get("entry") or row.get("name") or ""),
            prompt_content="",
            function_name=str(row.get("name") or row.get("entry") or f"row_{inspected}"),
            target_object_path="",
            asm="",
            config={"schema": "reconkit.source-plugin-pipeline-config.v1"},
            initial_context={
                "sourceParityRow": row,
                "outDir": str(config.out_dir),
                "codeSliceMatchesPath": str(matches_path),
                "sourceShapeMatchesPath": str(source_shape_matches_path),
                "compiler": config.compiler,
                "clang": config.clang,
                "compilerProfiles": config.compiler_profiles,
                "inventory": str(config.inventory),
                "vcRoot": str(config.vc_root) if config.vc_root else None,
                "wine": config.wine,
                "wineprefix": str(config.wineprefix) if config.wineprefix else None,
                "timeout": config.timeout,
                "dryRun": config.dry_run,
                "semanticOnly": config.semantic_only,
                "sourceShapeSearch": config.source_shape_search,
                "maxVariantsPerFunction": config.max_variants_per_function,
                "strategies": sorted(config.strategies) if config.strategies else None,
                "sourceQualities": sorted(config.source_qualities) if config.source_qualities else None,
            },
        )
        append_jsonl(runs_path, result.to_json())
        if result.success:
            succeeded += 1
        else:
            failed += 1
        if config.progress_every and inspected % config.progress_every == 0:
            print(
                f"source-plugin-pipeline: inspected={inspected} succeeded={succeeded} failed={failed}",
                flush=True,
            )

    export = export_recovered_source([matches_path, source_shape_matches_path], out_dir=config.out_dir / "recovered-source", source_name="source_slices.c")
    raw_match_quality_counts = merge_counts(
        count_jsonl_values(matches_path, "sourceQuality"),
        count_jsonl_values(source_shape_matches_path, "sourceQuality", default="high-level-c"),
    )
    raw_match_scope_counts = merge_counts(
        count_jsonl_values(matches_path, "sourceRecoveryScope"),
        count_jsonl_values(source_shape_matches_path, "sourceRecoveryScope", default="whole-function"),
    )
    exported_quality_counts = count_manifest_function_values(Path(str(export.get("manifest") or "")), "sourceQuality", default="unknown")
    exported_scope_counts = count_manifest_function_values(Path(str(export.get("manifest") or "")), "sourceRecoveryScope", default="unknown")
    summary = {
        "schema": "reconkit.source-plugin-pipeline-summary.v1",
        "status": "generated-only" if config.dry_run else "complete",
        "outDir": str(config.out_dir),
        "queue": str(config.queue) if config.queue else None,
        "sourceTasks": [str(path) for path in config.source_tasks],
        "sourceTasksOnly": config.source_tasks_only,
        "attemptsPath": str(attempts_path),
        "codeSliceMatchesPath": str(matches_path),
        "sourceShapeMatchesPath": str(source_shape_matches_path),
        "eventsPath": str(events_path),
        "runsPath": str(runs_path),
        "totalInputRows": len(rows),
        "candidateEligibleFunctions": inspected + skipped,
        "candidateCoverageRatio": safe_ratio(inspected + skipped, len(rows)),
        "inspectedFunctions": inspected,
        "skippedEligibleFunctions": skipped,
        "skippedBoundarySuspectFunctions": skipped_boundary,
        "skippedNoCompatibleCandidateFunctions": skipped_no_candidate,
        "successfulFunctions": succeeded,
        "failedFunctions": failed,
        "matchedBySourceQuality": exported_quality_counts,
        "matchedByRecoveryScope": exported_scope_counts,
        "rawMatchedBySourceQuality": raw_match_quality_counts,
        "rawMatchedByRecoveryScope": raw_match_scope_counts,
        "highLevelSourceMatches": exported_quality_counts.get("high-level-c", 0),
        "inlineAsmSourceMatches": exported_quality_counts.get("inline-asm-c", 0),
        "sourceShapeMatchedFunctions": sum(count_jsonl_values(source_shape_matches_path, "status").values()),
        "compiler": config.compiler,
        "compilerProfiles": [name for name, _flags in config.compiler_profiles],
        "compilerProfileSelection": "explicit" if config.compiler_profiles else "row-hints-then-defaults",
        "semanticOnly": config.semantic_only,
        "strategies": sorted(config.strategies) if config.strategies else None,
        "sourceQualityFilter": sorted(config.source_qualities) if config.source_qualities else None,
        "recoveredSourceExport": export,
        "claimBoundary": "Plugin pipeline emits source only for rows that compile and objdiff/byte-compare with zero differences; this is partial recovered source, not whole-program parity.",
    }
    write_json(config.out_dir / "summary.json", summary)
    return summary


def collect_rows(config: SourcePluginRunConfig) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not config.source_tasks_only and config.queue is not None:
        rows.extend(iter_jsonl(config.queue))
    for source_tasks in config.source_tasks:
        rows.extend(iter_source_task_rows(source_tasks))
    if config.source_tasks:
        rows.sort(key=synthesis_row_priority)
    return rows


def compatible_candidate_count(row: dict[str, Any], config: SourcePluginRunConfig) -> int:
    raw_candidates = append_packaged_fallback(
        generate(row, config.max_variants_per_function),
        packaged_source_candidate(row),
    )
    raw_candidates, _rule_filtered = filter_candidates_by_explicit_rule_strategies(raw_candidates, config.strategies)
    raw_candidates, _quality_filtered = filter_candidates_by_source_quality(raw_candidates, promotion_seed_qualities(config))
    candidates = compiler_compatible_candidates(row, raw_candidates, config.compiler, config.max_variants_per_function)
    if config.semantic_only:
        candidates = [candidate for candidate in candidates if candidate.semantic_source]
    return len(candidates)


def jsonl_event_handler(path: Path):
    def emit(event: dict[str, Any]) -> None:
        append_jsonl(path, event)

    return emit


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def count_jsonl_values(path: Path, key: str, *, default: str = "unknown") -> dict[str, int]:
    counts: dict[str, int] = {}
    if not path.exists():
        return counts
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            value = str(row.get(key) or default)
            counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def count_manifest_function_values(path: Path, key: str, *, default: str = "unknown") -> dict[str, int]:
    if not path.exists():
        return {}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    counts: dict[str, int] = {}
    for row in manifest.get("functions") or []:
        if not isinstance(row, dict):
            continue
        value = str(row.get(key) or default)
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def promotion_seed_qualities(config: SourcePluginRunConfig) -> set[str] | None:
    if not config.source_shape_search or not config.source_qualities or "high-level-c" not in config.source_qualities:
        return config.source_qualities
    return set(config.source_qualities) | {"inline-asm-c"}


def merge_counts(*items: dict[str, int]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for item in items:
        for key, value in item.items():
            merged[key] = merged.get(key, 0) + value
    return dict(sorted(merged.items()))


def parse_csv_set(value: str | None) -> set[str] | None:
    if not value:
        return None
    parsed = {item.strip() for item in value.split(",") if item.strip()}
    return parsed or None


def parse_profile_values(values: list[str]) -> list[tuple[str, list[str]]]:
    return [parse_profile_flag_set(value) for value in values if value.strip()]


def safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)
