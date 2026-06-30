"""Windowed recovery orchestration for large binaries."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

from .package_sweep import sweep_recovered_source_package
from .package_verify import resolve_msvc_root
from .pipeline import RecoveryConfig, RecoveryRunner
from .source_parity_synthesize import main as source_parity_synthesize_main
from .sourcegen import is_recoverable_candidate
from .state import atomic_write_json, now


def run_recovery_windows(
    *,
    base_config: RecoveryConfig,
    window_size: int,
    start_offset: int = 0,
    max_windows: int | None = None,
    semantic_sweep: bool = True,
    semantic_sweep_compiler: str = "auto",
    semantic_sweep_profiles: list[list[str]] | None = None,
    semantic_sweep_timeout: int | None = None,
    semantic_sweep_max_variants_per_function: int = 8,
    semantic_sweep_clang: str = "clang",
    semantic_sweep_clang_args: list[str] | None = None,
    semantic_sweep_clang_target: str | None = "i686-pc-windows-msvc",
    msvc_root: Path | None = None,
    wine: str = "wine",
    wineprefix: Path | None = None,
    objcopy: str = "objcopy",
    objdump: str = "objdump",
    source_parity_synthesis: bool = False,
    source_parity_queue: Path | None = None,
    source_parity_inventory: Path | None = None,
    source_parity_remaining_features: Path | None = None,
    source_parity_retrieval: Path | None = None,
    source_parity_matched_summaries: list[Path] | None = None,
    source_parity_out_dir: Path | None = None,
    source_parity_limit: int = 25,
    source_parity_offset: int = 0,
    source_parity_max_variants_per_function: int = 8,
    source_parity_strategies: str | None = None,
    source_parity_dry_run: bool = False,
    source_parity_clean: bool = False,
    source_parity_vc_root: Path | None = None,
    source_parity_wine: str | None = None,
    source_parity_timeout: int | None = None,
    source_parity_progress_every: int = 0,
    source_parity_compiler_profiles: list[str] | None = None,
) -> dict[str, Any]:
    if window_size <= 0:
        raise ValueError("window_size must be positive")
    base_dir = base_config.work_dir
    base_dir.mkdir(parents=True, exist_ok=True)

    plan_dir = base_dir / "_plan"
    plan_config = replace(
        base_config,
        work_dir=plan_dir,
        force=base_config.force,
        stop_after="discover-functions",
        function_analysis="none",
        source_task_limit=window_size,
        source_task_offset=0,
    )
    plan_code = RecoveryRunner(plan_config).run()
    candidates_path = plan_dir / "function-candidates.json"
    candidates_doc = json.loads(candidates_path.read_text(encoding="utf-8")) if candidates_path.exists() else {"candidates": []}
    recoverable_total = sum(1 for row in candidates_doc.get("candidates", []) if is_recoverable_candidate(row))

    summary_path = base_dir / "windows-summary.json"
    previous_summary = load_resume_summary(summary_path, base_config, window_size)
    window_by_offset = index_windows_by_offset(previous_summary.get("windows", [])) if base_config.resume else {}
    all_offsets = list(range(max(0, start_offset), recoverable_total, window_size))
    offsets = [
        offset
        for offset in all_offsets
        if not (base_config.resume and window_is_complete(window_by_offset.get(offset)))
    ]
    if max_windows is not None:
        offsets = offsets[: max(0, max_windows)]

    aggregate = {
        "schema": "mizuchi.recovery-windows.v1",
        "status": "running",
        "startedAt": now(),
        "input": str(base_config.input_path),
        "workDir": str(base_dir),
        "planDir": str(plan_dir),
        "windowSize": window_size,
        "startOffset": max(0, start_offset),
        "maxWindows": max_windows,
        "planReturnCode": plan_code,
        "candidateTotal": len(candidates_doc.get("candidates", [])),
        "recoverableCandidateTotal": recoverable_total,
        "recoverableWindowTotal": len(all_offsets),
        "windowsPlanned": len(offsets),
        "windowsKnownAtStart": len(window_by_offset),
        "windowsSkippedComplete": sum(1 for offset in all_offsets if window_is_complete(window_by_offset.get(offset))),
        "windowsScheduledThisRun": len(offsets),
        "windowOffsetsScheduled": offsets,
        "resumeSummaryLoaded": bool(previous_summary),
        "windows": sorted_windows(window_by_offset),
    }
    aggregate.update(summarize_aggregate(aggregate))
    atomic_write_json(summary_path, aggregate)

    for offset in offsets:
        limit = min(window_size, max(0, recoverable_total - offset))
        shard_dir = base_dir / f"window-{offset:06d}-{offset + limit:06d}"
        shard_config = replace(
            base_config,
            work_dir=shard_dir,
            stop_after="generate-source-candidates",
            source_task_limit=limit,
            source_task_offset=offset,
        )
        return_code = RecoveryRunner(shard_config).run()
        window_summary = summarize_window(shard_dir, offset, limit, return_code)
        window_by_offset[offset] = window_summary
        aggregate["windows"] = sorted_windows(window_by_offset)
        aggregate.update(summarize_aggregate(aggregate))
        atomic_write_json(summary_path, aggregate)

    aggregate["windows"] = sorted_windows(window_by_offset)
    source_package = build_recovered_source_package(base_dir, aggregate["windows"])
    aggregate["sourcePackage"] = source_package
    aggregate["semanticSweep"] = run_source_package_semantic_sweep(
        source_package,
        enabled=semantic_sweep,
        compiler=semantic_sweep_compiler,
        profiles=semantic_sweep_profiles,
        timeout=semantic_sweep_timeout or base_config.stage_timeout,
        max_variants_per_function=semantic_sweep_max_variants_per_function,
        clang=semantic_sweep_clang,
        clang_args=semantic_sweep_clang_args or [],
        clang_target=semantic_sweep_clang_target,
        msvc_root=msvc_root,
        wine=wine,
        wineprefix=wineprefix,
        objcopy=objcopy,
        objdump=objdump,
    )
    aggregate["sourceParitySynthesis"] = run_source_parity_synthesis(
        enabled=source_parity_synthesis,
        base_dir=base_dir,
        queue=source_parity_queue,
        inventory=source_parity_inventory,
        remaining_features=source_parity_remaining_features,
        retrieval=source_parity_retrieval,
        matched_summaries=source_parity_matched_summaries or [],
        out_dir=source_parity_out_dir,
        limit=source_parity_limit,
        offset=source_parity_offset,
        max_variants_per_function=source_parity_max_variants_per_function,
        strategies=source_parity_strategies,
        dry_run=source_parity_dry_run,
        clean=source_parity_clean,
        vc_root=source_parity_vc_root or msvc_root,
        wine=source_parity_wine or wine,
        wineprefix=wineprefix,
        timeout=source_parity_timeout or semantic_sweep_timeout or base_config.stage_timeout,
        progress_every=source_parity_progress_every,
        compiler_profiles=source_parity_compiler_profiles,
    )
    aggregate["sourceParityPromotion"] = promote_source_parity_accepts(
        source_package,
        aggregate["sourceParitySynthesis"],
    )
    aggregate.update(summarize_aggregate(aggregate))
    aggregate["coverage"] = write_window_coverage(base_dir, aggregate)
    aggregate["completedAt"] = now()
    atomic_write_json(summary_path, aggregate)
    return aggregate


def load_resume_summary(summary_path: Path, base_config: RecoveryConfig, window_size: int) -> dict[str, Any]:
    summary = read_json(summary_path)
    if not summary:
        return {}
    if summary.get("schema") != "mizuchi.recovery-windows.v1":
        return {}
    if str(summary.get("input") or "") != str(base_config.input_path):
        return {}
    if int(summary.get("windowSize") or -1) != window_size:
        return {}
    return summary


def index_windows_by_offset(windows: Any) -> dict[int, dict[str, Any]]:
    indexed: dict[int, dict[str, Any]] = {}
    if not isinstance(windows, list):
        return indexed
    for window in windows:
        if not isinstance(window, dict):
            continue
        try:
            offset = int(window.get("offset"))
        except (TypeError, ValueError):
            continue
        indexed[offset] = window
    return indexed


def sorted_windows(window_by_offset: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    return [window_by_offset[offset] for offset in sorted(window_by_offset)]


def window_is_complete(window: dict[str, Any] | None) -> bool:
    if not window:
        return False
    return window_has_source_progress(window)


def window_has_source_progress(window: dict[str, Any]) -> bool:
    return (
        window.get("returnCode") == 0
        and window.get("sourceStatus") not in {None, "blocked", "queued-no-source"}
        and int(window.get("generatedSourceCandidates") or 0) > 0
    )


def summarize_window(shard_dir: Path, offset: int, limit: int, return_code: int) -> dict[str, Any]:
    analysis = read_json(shard_dir / "function-analysis.json")
    source = read_json(shard_dir / "source-generation/summary.json")
    analysis_image = read_json(shard_dir / "analysis-target.json")
    return {
        "offset": offset,
        "limit": limit,
        "workDir": str(shard_dir),
        "returnCode": return_code,
        "status": "complete" if window_has_source_progress(
            {
                "returnCode": return_code,
                "sourceStatus": source.get("status"),
                "generatedSourceCandidates": source.get("generatedSourceCandidates", 0),
            }
        ) else "failed",
        "analysisStatus": analysis.get("status"),
        "analysisReturnCode": analysis.get("returnCode"),
        "functionsFound": analysis.get("functionsFound", 0),
        "decompiled": (analysis.get("decompile") or {}).get("decompiled", 0),
        "sourceStatus": source.get("status"),
        "generatedSourceCandidates": source.get("generatedSourceCandidates", 0),
        "freshGeneratedSourceCandidates": source.get("freshGeneratedSourceCandidates", 0),
        "reusedSourceCandidates": source.get("reusedSourceCandidates", 0),
        "taskCount": source.get("taskCount", 0),
        "sourceByStatus": source.get("byStatus", {}),
        "analysisImageStatus": analysis_image.get("status"),
        "analysisImageTransform": analysis_image.get("transform"),
        "blockers": source.get("blockers", []),
    }


def summarize_aggregate(aggregate: dict[str, Any]) -> dict[str, Any]:
    windows = aggregate.get("windows", [])
    failed = [row for row in windows if not window_has_source_progress(row)]
    status = "failed" if failed else "complete"
    semantic_sweep = aggregate.get("semanticSweep")
    if status == "complete" and isinstance(semantic_sweep, dict) and semantic_sweep.get("enabled"):
        if semantic_sweep.get("status") != "matched":
            status = "semantic-incomplete"
    source_parity = aggregate.get("sourceParitySynthesis")
    if status == "complete" and isinstance(source_parity, dict) and source_parity.get("enabled"):
        if source_parity.get("status") not in {"complete", "generated-only"}:
            status = "source-parity-synthesis-incomplete"
    return {
        "status": status,
        "windowsComplete": sum(1 for row in windows if window_has_source_progress(row)),
        "windowsFailed": len(failed),
        "functionsFound": sum(int(row.get("functionsFound") or 0) for row in windows),
        "decompiled": sum(int(row.get("decompiled") or 0) for row in windows),
        "generatedSourceCandidates": sum(int(row.get("generatedSourceCandidates") or 0) for row in windows),
        "freshGeneratedSourceCandidates": sum(int(row.get("freshGeneratedSourceCandidates") or 0) for row in windows),
        "reusedSourceCandidates": sum(int(row.get("reusedSourceCandidates") or 0) for row in windows),
        "taskCount": sum(int(row.get("taskCount") or 0) for row in windows),
    }


def write_window_coverage(base_dir: Path, aggregate: dict[str, Any]) -> dict[str, Any]:
    package = aggregate.get("sourcePackage") if isinstance(aggregate.get("sourcePackage"), dict) else {}
    package_dir = Path(str(package.get("packageDir") or base_dir / "recovered-source"))
    manifest = read_json(Path(str(package.get("manifest") or package_dir / "manifest.json")))
    coverage_path = package_dir / "coverage.json"
    coverage_md_path = package_dir / "COVERAGE.md"
    semantic = aggregate.get("semanticSweep") if isinstance(aggregate.get("semanticSweep"), dict) else {}
    source_parity = aggregate.get("sourceParitySynthesis") if isinstance(aggregate.get("sourceParitySynthesis"), dict) else {}
    promotion = aggregate.get("sourceParityPromotion") if isinstance(aggregate.get("sourceParityPromotion"), dict) else {}
    sweep = read_json(Path(str(semantic.get("report") or "")))
    windows = aggregate.get("windows") if isinstance(aggregate.get("windows"), list) else []
    recoverable_total = int(aggregate.get("recoverableCandidateTotal") or 0)
    source_functions = int(manifest.get("functionCount") or package.get("functionCount") or 0)
    matched_functions = int(semantic.get("matchedFunctions") or 0)
    semantic_matched = int(semantic.get("semanticMatchedFunctions") or 0)
    source_parity_accepted = int(manifest.get("sourceParityAcceptedFunctionCount") or package.get("sourceParityAcceptedFunctionCount") or 0)
    matched_rows, unmatched_rows = semantic_coverage_rows(sweep)
    parity_rows = source_parity_coverage_rows(manifest)
    coverage = {
        "schema": "mizuchi.recovery-window-coverage.v1",
        "status": coverage_status(
            recoverable_total,
            source_functions,
            matched_functions,
            semantic_matched,
            source_parity_accepted,
            semantic,
            source_parity,
        ),
        "generatedAt": now(),
        "input": aggregate.get("input"),
        "workDir": aggregate.get("workDir"),
        "sourcePackage": package,
        "candidateCoverage": {
            "candidateTotal": int(aggregate.get("candidateTotal") or 0),
            "recoverableCandidateTotal": recoverable_total,
            "recoverableWindowTotal": int(aggregate.get("recoverableWindowTotal") or 0),
            "windowSize": int(aggregate.get("windowSize") or 0),
            "windowsKnown": len(windows),
            "windowsComplete": int(aggregate.get("windowsComplete") or 0),
            "windowsFailed": int(aggregate.get("windowsFailed") or 0),
            "windowOffsetsCovered": [row.get("offset") for row in windows if isinstance(row, dict)],
        },
        "sourceCoverage": {
            "packagedFunctions": source_functions,
            "taskCount": int(package.get("taskCount") or aggregate.get("taskCount") or 0),
            "targetSlicedFunctions": source_functions,
            "generatedSourceCandidates": int(aggregate.get("generatedSourceCandidates") or 0),
            "freshGeneratedSourceCandidates": int(aggregate.get("freshGeneratedSourceCandidates") or 0),
            "reusedSourceCandidates": int(aggregate.get("reusedSourceCandidates") or 0),
            "recoverableFunctionCoveragePercent": percent(source_functions, recoverable_total),
        },
        "semanticCoverage": {
            "enabled": bool(semantic.get("enabled")),
            "status": semantic.get("status"),
            "functionsSwept": int(semantic.get("functions") or 0),
            "matchedFunctions": matched_functions,
            "semanticMatchedFunctions": semantic_matched,
            "codeMatchPackagePercent": percent(matched_functions, source_functions),
            "semanticMatchPackagePercent": percent(semantic_matched, source_functions),
            "semanticMatchRecoverablePercent": percent(semantic_matched, recoverable_total),
            "attempts": int(semantic.get("attempts") or 0),
            "attemptsCompiled": int(semantic.get("attemptsCompiled") or 0),
            "attemptsReused": int(semantic.get("attemptsReused") or 0),
            "report": semantic.get("report"),
            "attemptsPath": semantic.get("attemptsPath"),
            "compilerResolution": semantic.get("compilerResolution"),
            "compilerProfiles": semantic.get("compilerProfiles"),
        },
        "sourceParityCoverage": {
            "enabled": bool(source_parity.get("enabled")),
            "status": source_parity.get("status"),
            "inspectedFunctions": int(source_parity.get("inspectedFunctions") or 0),
            "attemptedCandidates": int(source_parity.get("attemptedCandidates") or 0),
            "acceptedCandidates": int(source_parity.get("acceptedCandidates") or 0),
            "promotedFunctions": int(promotion.get("promotedFunctions") or 0),
            "acceptedPackagedFunctions": source_parity_accepted,
            "acceptedPackagePercent": percent(source_parity_accepted, source_functions),
            "acceptedRecoverablePercent": percent(source_parity_accepted, recoverable_total),
            "attemptsPath": source_parity.get("attemptsPath"),
            "acceptedPath": source_parity.get("acceptedPath"),
            "promotion": promotion,
        },
        "matchedFunctions": [*matched_rows, *parity_rows],
        "unmatchedFunctions": unmatched_rows,
        "nextAction": coverage_next_action(
            aggregate,
            semantic,
            source_parity,
            recoverable_total,
            source_functions,
            matched_functions,
            semantic_matched,
            source_parity_accepted,
        ),
        "claimBoundary": (
            "Coverage is per-function source-candidate and code-byte evidence only. "
            "Full source parity remains false until every recoverable function and required data/linker artifact is rebuilt and verified."
        ),
        "fullSourceParity": False,
    }
    package_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(coverage_path, coverage)
    coverage_md_path.write_text(render_coverage_markdown(coverage), encoding="utf-8")
    return {"status": coverage["status"], "json": str(coverage_path), "markdown": str(coverage_md_path), "fullSourceParity": False}


def semantic_coverage_rows(sweep: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for row in sweep.get("results", []) if isinstance(sweep.get("results"), list) else []:
        if not isinstance(row, dict):
            continue
        attempts = row.get("semanticMatchedAttempts") or row.get("matchedAttempts") or []
        attempt = attempts[0] if attempts and isinstance(attempts[0], dict) else row.get("bestAttempt") or {}
        summary = {
            "name": row.get("name"),
            "address": row.get("address"),
            "source": row.get("source"),
            "metadata": row.get("metadata"),
            "semanticMatched": bool(row.get("semanticMatched")),
            "variant": attempt.get("variant"),
            "sourceKind": attempt.get("sourceKind"),
            "semanticSource": attempt.get("semanticSource"),
            "compilerArgs": attempt.get("compilerArgs") or attempt.get("profileArgs"),
            "codeCompareStatus": attempt.get("codeCompareStatus"),
            "score": attempt.get("score"),
            "firstDifference": attempt.get("firstDifference"),
        }
        if row.get("matched"):
            matched.append(summary)
        else:
            unmatched.append(summary)
    return matched, unmatched


def source_parity_coverage_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fn in manifest.get("functions", []) if isinstance(manifest.get("functions"), list) else []:
        if not isinstance(fn, dict) or fn.get("proofTier") != "target-object-objdiff-match":
            continue
        rows.append(
            {
                "name": fn.get("name"),
                "address": fn.get("address"),
                "source": fn.get("source"),
                "metadata": fn.get("metadata"),
                "semanticMatched": True,
                "proofTier": fn.get("proofTier"),
                "variant": fn.get("sourceParityVariant"),
                "sourceKind": "generated-c",
                "semanticSource": True,
                "codeCompareStatus": "objdiff-zero",
                "verifyReport": fn.get("verifyReport"),
                "differences": fn.get("differences"),
            }
        )
    return rows


def coverage_status(
    recoverable_total: int,
    source_functions: int,
    matched_functions: int,
    semantic_matched: int,
    source_parity_accepted: int,
    semantic: dict[str, Any],
    source_parity: dict[str, Any],
) -> str:
    if recoverable_total <= 0:
        return "no-recoverable-functions"
    if source_functions <= 0:
        return "no-source-candidates"
    if source_parity.get("enabled") and source_parity_accepted == source_functions and source_functions < recoverable_total:
        return "partial-source-parity-match"
    if source_parity.get("enabled") and source_parity_accepted == recoverable_total:
        return "all-recoverable-functions-source-parity-matched"
    if source_parity.get("enabled") and source_parity_accepted:
        return "partial-source-parity-match"
    if semantic.get("enabled") and semantic_matched == source_functions and source_functions < recoverable_total:
        return "partial-semantic-match"
    if semantic.get("enabled") and semantic_matched == recoverable_total:
        return "all-recoverable-functions-semantically-matched"
    if semantic.get("enabled") and matched_functions == source_functions and matched_functions > semantic_matched:
        return "partial-semantic-match-with-code-fallback" if semantic_matched else "code-matched-nonsemantic-fallback"
    if semantic.get("enabled") and matched_functions > semantic_matched:
        return "partial-code-match-with-semantic-gaps"
    if semantic.get("enabled") and semantic_matched:
        return "partial-semantic-match"
    if semantic.get("enabled"):
        return "source-candidates-unmatched"
    return "source-candidates-unverified"


def coverage_next_action(
    aggregate: dict[str, Any],
    semantic: dict[str, Any],
    source_parity: dict[str, Any],
    recoverable_total: int,
    source_functions: int,
    matched_functions: int,
    semantic_matched: int,
    source_parity_accepted: int,
) -> dict[str, Any]:
    if source_functions < recoverable_total:
        return {
            "kind": "continue-window-recovery",
            "reason": "not all recoverable function candidates have packaged source candidates",
            "suggestedCommand": [
                "mizuchi-recover",
                "recover-windows",
                str(aggregate.get("input") or "<input>"),
                "--work-dir",
                str(aggregate.get("workDir") or "<work-dir>"),
                "--resume",
                "--window-size",
                str(aggregate.get("windowSize") or 25),
                "--start-offset",
                str(aggregate.get("startOffset") or 0),
                "--max-windows",
                "1",
            ],
        }
    if source_parity.get("enabled") and source_parity_accepted < source_functions:
        return {
            "kind": "improve-source-parity-synthesis",
            "reason": "not all packaged functions have objdiff-zero generated C evidence",
            "sourceParityAcceptedFunctions": source_parity_accepted,
            "sourceParityUnmatchedFunctions": source_functions - source_parity_accepted,
        }
    if semantic.get("enabled") and semantic_matched < source_functions:
        if matched_functions > semantic_matched:
            return {
                "kind": "improve-semantic-source-generation",
                "reason": "all packaged functions were swept, but some only matched through nonsemantic code-byte fallback",
                "codeMatchedFunctions": matched_functions,
                "semanticMatchedFunctions": semantic_matched,
                "semanticUnmatchedFunctions": source_functions - semantic_matched,
            }
        return {
            "kind": "improve-source-variant-generation",
            "reason": "all packaged functions were swept, but some did not match semantically",
            "unmatchedFunctions": source_functions - semantic_matched,
        }
    return {
        "kind": "expand-non-function-coverage",
        "reason": "current packaged function set matched; data sections, globals, libraries, and linker layout still remain outside source parity",
    }


def percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100.0, 4)


def render_coverage_markdown(coverage: dict[str, Any]) -> str:
    candidate = coverage["candidateCoverage"]
    source = coverage["sourceCoverage"]
    semantic = coverage["semanticCoverage"]
    lines = [
        "# Mizuchi Recovery Coverage",
        "",
        f"Status: `{coverage['status']}`",
        f"Full source parity: `{str(coverage['fullSourceParity']).lower()}`",
        "",
        "## Candidate Coverage",
        "",
        f"- Recoverable candidates: `{candidate['recoverableCandidateTotal']}`",
        f"- Complete windows: `{candidate['windowsComplete']}` / `{candidate['recoverableWindowTotal']}`",
        f"- Packaged functions: `{source['packagedFunctions']}`",
        f"- Recoverable function coverage: `{source['recoverableFunctionCoveragePercent']}%`",
        "",
        "## Semantic Coverage",
        "",
        f"- Sweep status: `{semantic['status']}`",
        f"- Code matches: `{semantic['matchedFunctions']}` / `{source['packagedFunctions']}` packaged functions",
        f"- Code match package coverage: `{semantic['codeMatchPackagePercent']}%`",
        f"- Semantic matches: `{semantic['semanticMatchedFunctions']}` / `{source['packagedFunctions']}` packaged functions",
        f"- Semantic match over recoverable candidates: `{semantic['semanticMatchRecoverablePercent']}%`",
        f"- Attempts: `{semantic['attempts']}` compiled `{semantic['attemptsCompiled']}` reused `{semantic['attemptsReused']}`",
        "",
        "## Matched Functions",
        "",
    ]
    matched = coverage.get("matchedFunctions") or []
    if matched:
        for row in matched:
            lines.append(
                f"- `{row.get('name')}` at `{row.get('address')}` via `{row.get('variant')}` "
                f"`{row.get('codeCompareStatus')}` semantic=`{str(bool(row.get('semanticMatched'))).lower()}`"
            )
    else:
        lines.append("- None")
    lines.extend(["", "## Next Action", "", f"- Kind: `{coverage['nextAction']['kind']}`", f"- Reason: {coverage['nextAction']['reason']}", "", coverage["claimBoundary"], ""])
    return "\n".join(lines)


def run_source_parity_synthesis(
    *,
    enabled: bool,
    base_dir: Path,
    queue: Path | None,
    inventory: Path | None,
    remaining_features: Path | None,
    retrieval: Path | None,
    matched_summaries: list[Path],
    out_dir: Path | None,
    limit: int,
    offset: int,
    max_variants_per_function: int,
    strategies: str | None,
    dry_run: bool,
    clean: bool,
    vc_root: Path | None,
    wine: str,
    wineprefix: Path | None,
    timeout: int,
    progress_every: int,
    compiler_profiles: list[str] | None = None,
) -> dict[str, Any]:
    schema = "mizuchi.recovery-windows-source-parity-synthesis.v1"
    if not enabled:
        return {"schema": schema, "enabled": False, "status": "disabled"}
    if queue is None or inventory is None:
        return {
            "schema": schema,
            "enabled": True,
            "status": "missing-inputs",
            "reason": "--source-parity-queue and --source-parity-inventory are required when synthesis is enabled",
        }

    synthesis_dir = out_dir or base_dir / "source-parity-synthesis"
    synthesis_dir.mkdir(parents=True, exist_ok=True)
    empty_remaining = synthesis_dir / "remaining-features.empty.jsonl"
    empty_retrieval = synthesis_dir / "retrieval.empty.jsonl"
    if remaining_features is None and not empty_remaining.exists():
        empty_remaining.write_text("", encoding="utf-8")
    if retrieval is None and not empty_retrieval.exists():
        empty_retrieval.write_text("", encoding="utf-8")

    argv = [
        "--queue",
        str(queue.resolve()),
        "--inventory",
        str(inventory.resolve()),
        "--remaining-features",
        str((remaining_features or empty_remaining).resolve()),
        "--retrieval",
        str((retrieval or empty_retrieval).resolve()),
        "--out-dir",
        str(synthesis_dir.resolve()),
        "--limit",
        str(limit),
        "--offset",
        str(offset),
        "--max-variants-per-function",
        str(max_variants_per_function),
        "--timeout",
        str(timeout),
        "--progress-every",
        str(progress_every),
    ]
    for matched_summary in matched_summaries:
        argv.extend(["--matched-summary", str(matched_summary.resolve())])
    for profile in compiler_profiles or []:
        argv.extend(["--compiler-profile", profile])
    if strategies:
        argv.extend(["--strategies", strategies])
    if dry_run:
        argv.append("--dry-run")
    if clean:
        argv.append("--clean")
    if vc_root:
        argv.extend(["--vc-root", str(vc_root.resolve())])
    if wine:
        argv.extend(["--wine", wine])
    if wineprefix:
        argv.extend(["--wineprefix", str(wineprefix.resolve())])

    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            return_code = source_parity_synthesize_main(argv)
    except Exception as exc:
        return {
            "schema": schema,
            "enabled": True,
            "status": "failed",
            "outDir": str(synthesis_dir),
            "reason": str(exc),
        }

    summary = read_json(synthesis_dir / "summary.json")
    return {
        "schema": schema,
        "enabled": True,
        "status": summary.get("status", "failed") if return_code == 0 else "failed",
        "returnCode": return_code,
        "outDir": str(synthesis_dir),
        "summary": str(synthesis_dir / "summary.json"),
        "attemptsPath": summary.get("attemptsPath"),
        "acceptedPath": summary.get("acceptedPath"),
        "inspectedFunctions": summary.get("inspectedFunctions"),
        "generatedCandidates": summary.get("generatedCandidates"),
        "attemptedCandidates": summary.get("attemptedCandidates"),
        "acceptedCandidates": summary.get("acceptedCandidates"),
        "unsupportedFunctions": summary.get("unsupportedFunctions"),
        "compileFailedCandidates": summary.get("compileFailedCandidates"),
        "sliceFailedCandidates": summary.get("sliceFailedCandidates"),
        "mismatchedCandidates": summary.get("mismatchedCandidates"),
        "stdout": stdout.getvalue()[-4000:],
        "claimBoundary": summary.get("claimBoundary"),
    }


def promote_source_parity_accepts(source_package: dict[str, Any], source_parity: dict[str, Any]) -> dict[str, Any]:
    schema = "mizuchi.recovery-windows-source-parity-promotion.v1"
    if not source_parity.get("enabled"):
        return {"schema": schema, "enabled": False, "status": "disabled"}
    accepted_path_value = source_parity.get("acceptedPath")
    package_dir_value = source_package.get("packageDir")
    if not accepted_path_value or not package_dir_value:
        return {"schema": schema, "enabled": True, "status": "missing-inputs", "promotedFunctions": 0}
    accepted_path = Path(str(accepted_path_value))
    package_dir = Path(str(package_dir_value))
    manifest_path = package_dir / "manifest.json"
    functions_dir = package_dir / "functions"
    if not accepted_path.exists() or not manifest_path.exists():
        return {"schema": schema, "enabled": True, "status": "missing-inputs", "promotedFunctions": 0}

    manifest = read_json(manifest_path)
    if not manifest:
        return {"schema": schema, "enabled": True, "status": "invalid-manifest", "promotedFunctions": 0}
    functions = list(manifest.get("functions") or [])
    existing_keys = {(str(fn.get("name")), str(fn.get("address") or fn.get("entry"))) for fn in functions if isinstance(fn, dict)}
    promoted: list[dict[str, Any]] = []
    functions_dir.mkdir(parents=True, exist_ok=True)
    for row in iter_jsonl(accepted_path):
        if row.get("status") != "matched" or int(row.get("differences", -1)) != 0:
            continue
        key = (str(row.get("name")), str(row.get("entry")))
        if key in existing_keys:
            continue
        source = resolve_path(row.get("source"))
        if not source.exists():
            continue
        stem = safe_function_file_stem({"name": row.get("name"), "address": row.get("entry")})
        copied_c = functions_dir / f"{stem}{source_suffix(source)}"
        copied_json = functions_dir / f"{stem}.json"
        shutil.copy2(source, copied_c)
        metadata = {
            "schema": "mizuchi.recovered-source-function.v1",
            "name": row.get("name"),
            "entry": row.get("entry"),
            "address": row.get("entry"),
            "status": "source-parity-accepted",
            "proofTier": "target-object-objdiff-match",
            "source": str(copied_c),
            "sourceOrigin": row.get("sourceOrigin"),
            "sourceSha256": row.get("sourceSha256"),
            "callconv": row.get("callconv"),
            "symbol": row.get("symbol"),
            "section": row.get("section"),
            "bodyBytes": row.get("bodyBytes"),
            "instructionCount": row.get("instructionCount"),
            "rule": row.get("rule"),
            "variant": row.get("variant"),
            "differences": row.get("differences"),
            "message": row.get("message"),
            "verifyReport": row.get("verifyReport"),
            "attempt": row,
            "claimBoundary": "Promoted only because source-parity synthesis recorded objdiff zero for this generated C candidate.",
        }
        atomic_write_json(copied_json, metadata)
        function = {
            "name": row.get("name"),
            "address": row.get("entry"),
            "entry": row.get("entry"),
            "status": "source-parity-accepted",
            "proofTier": "target-object-objdiff-match",
            "source": str(copied_c),
            "metadata": str(copied_json),
            "verifyReport": row.get("verifyReport"),
            "sourceParityRule": row.get("rule"),
            "sourceParityVariant": row.get("variant"),
            "differences": row.get("differences"),
        }
        functions.append(function)
        promoted.append(function)
        existing_keys.add(key)

    manifest["functions"] = functions
    manifest["functionCount"] = len(functions)
    manifest["sourceParityAcceptedFunctionCount"] = sum(1 for fn in functions if isinstance(fn, dict) and fn.get("proofTier") == "target-object-objdiff-match")
    manifest["claimBoundary"] = (
        "Package may contain generated-unverified candidates plus source-parity accepted generated C. "
        "Only functions with proofTier=target-object-objdiff-match have objdiff-zero evidence; full source parity remains false."
    )
    atomic_write_json(manifest_path, manifest)
    (package_dir / "README.md").write_text(render_source_index(manifest), encoding="utf-8")
    source_package["functionCount"] = manifest["functionCount"]
    source_package["sourceParityAcceptedFunctionCount"] = manifest["sourceParityAcceptedFunctionCount"]
    source_package["claimBoundary"] = manifest["claimBoundary"]
    return {
        "schema": schema,
        "enabled": True,
        "status": "complete",
        "packageDir": str(package_dir),
        "manifest": str(manifest_path),
        "promotedFunctions": len(promoted),
        "sourceParityAcceptedFunctionCount": manifest["sourceParityAcceptedFunctionCount"],
    }


def run_source_package_semantic_sweep(
    source_package: dict[str, Any],
    *,
    enabled: bool,
    compiler: str,
    profiles: list[list[str]] | None,
    timeout: int,
    max_variants_per_function: int,
    clang: str,
    clang_args: list[str],
    clang_target: str | None,
    msvc_root: Path | None,
    wine: str,
    wineprefix: Path | None,
    objcopy: str,
    objdump: str,
) -> dict[str, Any]:
    if not enabled:
        return {"schema": "mizuchi.recovery-windows-semantic-sweep.v1", "enabled": False, "status": "disabled"}
    package_dir_value = source_package.get("packageDir")
    if not package_dir_value:
        return {"schema": "mizuchi.recovery-windows-semantic-sweep.v1", "enabled": True, "status": "missing-package", "reason": "source package has no packageDir"}
    function_count = int(source_package.get("functionCount") or 0)
    if function_count <= 0:
        return {"schema": "mizuchi.recovery-windows-semantic-sweep.v1", "enabled": True, "status": "skipped-no-functions", "reason": "source package contains no generated function candidates"}

    resolution = resolve_semantic_sweep_compiler(compiler, msvc_root, wine)
    selected_compiler = str(resolution["compiler"])
    try:
        report = sweep_recovered_source_package(
            Path(str(package_dir_value)),
            compiler=selected_compiler,
            clang=clang,
            clang_args=clang_args,
            clang_profiles=profiles,
            timeout=timeout,
            clang_target=clang_target,
            msvc_root=msvc_root,
            wine=wine,
            wineprefix=wineprefix,
            objcopy=objcopy,
            objdump=objdump,
            max_variants_per_function=max_variants_per_function,
        )
    except Exception as exc:
        return {
            "schema": "mizuchi.recovery-windows-semantic-sweep.v1",
            "enabled": True,
            "status": "failed",
            "compilerResolution": resolution,
            "reason": str(exc),
        }
    return {
        "schema": "mizuchi.recovery-windows-semantic-sweep.v1",
        "enabled": True,
        "status": report.get("status"),
        "compilerResolution": resolution,
        "package": report.get("package"),
        "report": str(Path(str(report.get("outDir") or package_dir_value)) / "sweep.json"),
        "attemptsPath": report.get("attemptsPath"),
        "functions": report.get("functions"),
        "matchedFunctions": report.get("matchedFunctions"),
        "semanticMatchedFunctions": report.get("semanticMatchedFunctions"),
        "attempts": report.get("attempts"),
        "attemptsCompiled": report.get("attemptsCompiled"),
        "attemptsReused": report.get("attemptsReused"),
        "compilerProfiles": report.get("compilerProfiles") or report.get("clangProfiles"),
        "claimBoundary": report.get("claimBoundary"),
    }


def resolve_semantic_sweep_compiler(requested: str, msvc_root: Path | None, wine: str) -> dict[str, Any]:
    if requested != "auto":
        return {"compiler": requested, "reason": "explicitly requested"}
    root = resolve_msvc_root(msvc_root)
    cl_exe = root / "bin" / "cl.exe"
    wine_path = shutil.which(wine) or (str(Path(wine)) if Path(wine).exists() else None)
    if cl_exe.exists() and wine_path:
        return {
            "compiler": "msvc",
            "reason": "auto-selected MSVC because cl.exe and wine are available",
            "msvcRoot": str(root),
            "cl": str(cl_exe),
            "wine": wine_path,
        }
    return {
        "compiler": "clang",
        "reason": "auto-selected clang because MSVC cl.exe or wine was not available",
        "msvcRoot": str(root),
        "clExists": cl_exe.exists(),
        "wine": wine_path,
    }


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def build_recovered_source_package(base_dir: Path, windows: list[dict[str, Any]]) -> dict[str, Any]:
    package_dir = base_dir / "recovered-source"
    functions_dir = package_dir / "functions"
    facts_path = package_dir / "function-facts.jsonl"
    tasks_path = package_dir / "tasks.jsonl"
    manifest_path = package_dir / "manifest.json"
    index_path = package_dir / "README.md"
    preserved_sweep = base_dir / ".recovered-source-sweep-cache"

    if preserved_sweep.exists():
        shutil.rmtree(preserved_sweep)
    if package_dir.exists():
        sweep_dir = package_dir / "sweep"
        if sweep_dir.exists():
            shutil.move(str(sweep_dir), str(preserved_sweep))
        shutil.rmtree(package_dir)
    functions_dir.mkdir(parents=True, exist_ok=True)

    functions: list[dict[str, Any]] = []
    task_count = 0
    fact_count = 0

    with facts_path.open("w", encoding="utf-8") as facts_out, tasks_path.open("w", encoding="utf-8") as tasks_out:
        for window in windows:
            shard_dir = Path(str(window.get("workDir") or ""))
            fact_file = shard_dir / "function-facts.jsonl"
            if fact_file.exists():
                for line in fact_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    if not line.strip():
                        continue
                    facts_out.write(line.rstrip() + "\n")
                    fact_count += 1

            task_file = shard_dir / "source-generation/tasks.jsonl"
            if not task_file.exists():
                continue
            for line in task_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    task = json.loads(line)
                except json.JSONDecodeError:
                    continue
                task_count += 1
                source = task.get("source")
                if source:
                    source_path = resolve_path(source)
                    if source_path.exists():
                        stem = safe_function_file_stem(task)
                        copied_c = functions_dir / f"{stem}{source_suffix(source_path)}"
                        copied_json = functions_dir / f"{stem}.json"
                        copied_slice = copy_target_slice(task, functions_dir / f"{stem}.target.bin")
                        shutil.copy2(source_path, copied_c)
                        task = {**task, "packagedSource": str(copied_c)}
                        if copied_slice is not None:
                            task["targetSlice"] = {
                                **(task.get("targetSlice") or {}),
                                "packagedBytesPath": str(copied_slice),
                            }
                        atomic_write_json(copied_json, task)
                        functions.append(
                            {
                                "name": task.get("name"),
                                "address": task.get("address"),
                                "rva": task.get("rva"),
                                "status": task.get("status"),
                                "source": str(copied_c),
                                "metadata": str(copied_json),
                                "targetSlice": task.get("targetSlice"),
                                "windowOffset": window.get("offset"),
                            }
                        )
                tasks_out.write(json.dumps(task, sort_keys=True) + "\n")

    manifest = {
        "schema": "mizuchi.recovered-source-package.v1",
        "status": "complete",
        "packageDir": str(package_dir),
        "functionsDir": str(functions_dir),
        "functionCount": len(functions),
        "factCount": fact_count,
        "taskCount": task_count,
        "facts": str(facts_path),
        "tasks": str(tasks_path),
        "functions": functions,
        "claimBoundary": "packaged sources are generated-unverified automatic candidates until compiler and objdiff gates accept them",
    }
    atomic_write_json(manifest_path, manifest)
    index_path.write_text(render_source_index(manifest), encoding="utf-8")
    if preserved_sweep.exists():
        if functions:
            shutil.move(str(preserved_sweep), str(package_dir / "sweep"))
        else:
            shutil.rmtree(preserved_sweep)
    return {
        "status": "complete",
        "packageDir": str(package_dir),
        "manifest": str(manifest_path),
        "index": str(index_path),
        "functionCount": len(functions),
        "factCount": fact_count,
        "taskCount": task_count,
    }


def resolve_path(path: Any) -> Path:
    candidate = Path(str(path))
    if candidate.is_absolute():
        return candidate
    return Path.cwd() / candidate


def copy_target_slice(task: dict[str, Any], destination: Path) -> Path | None:
    target_slice = task.get("targetSlice")
    if not isinstance(target_slice, dict):
        return None
    bytes_path = target_slice.get("bytesPath")
    if not bytes_path:
        return None
    source = resolve_path(bytes_path)
    if not source.exists():
        return None
    shutil.copy2(source, destination)
    return destination


def source_suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".c", ".cc", ".cpp", ".cxx"}:
        return suffix
    return ".c"


def safe_function_file_stem(task: dict[str, Any]) -> str:
    name = str(task.get("name") or "sub")
    address = task.get("address")
    suffix = "unknown"
    if address is not None:
        try:
            suffix = f"{int(str(address), 16):08x}" if isinstance(address, str) else f"{int(address):08x}"
        except (TypeError, ValueError):
            suffix = "".join(ch if ch.isalnum() else "_" for ch in str(address)) or "unknown"
    safe = "".join(ch if ch.isalnum() or ch in "._+-" else "_" for ch in name).strip("._") or "sub"
    return f"{safe}_{suffix}"


def render_source_index(manifest: dict[str, Any]) -> str:
    lines = [
        "# Mizuchi Recovered Source Package",
        "",
        f"Status: {manifest['status']}",
        f"Functions: {manifest['functionCount']}",
        f"Facts: {manifest['factCount']}",
        f"Tasks: {manifest['taskCount']}",
        "",
        manifest["claimBoundary"],
        "",
        "## Functions",
        "",
    ]
    for fn in manifest["functions"]:
        lines.append(f"- `{fn.get('name')}` at `{fn.get('address')}` -> `{fn.get('source')}`")
    lines.append("")
    return "\n".join(lines)
