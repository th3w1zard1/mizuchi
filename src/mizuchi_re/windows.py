"""Windowed recovery orchestration for large binaries."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from .pipeline import RecoveryConfig, RecoveryRunner
from .sourcegen import is_recoverable_candidate
from .state import atomic_write_json, now


def run_recovery_windows(
    *,
    base_config: RecoveryConfig,
    window_size: int,
    start_offset: int = 0,
    max_windows: int | None = None,
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

    offsets = list(range(max(0, start_offset), recoverable_total, window_size))
    if max_windows is not None:
        offsets = offsets[: max(0, max_windows)]

    windows: list[dict[str, Any]] = []
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
        "windowsPlanned": len(offsets),
        "windows": windows,
    }
    summary_path = base_dir / "windows-summary.json"
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
        windows.append(window_summary)
        aggregate.update(summarize_aggregate(aggregate))
        atomic_write_json(summary_path, aggregate)

    aggregate.update(summarize_aggregate(aggregate))
    aggregate["completedAt"] = now()
    atomic_write_json(summary_path, aggregate)
    return aggregate


def summarize_window(shard_dir: Path, offset: int, limit: int, return_code: int) -> dict[str, Any]:
    analysis = read_json(shard_dir / "function-analysis.json")
    source = read_json(shard_dir / "source-generation/summary.json")
    analysis_image = read_json(shard_dir / "analysis-target.json")
    return {
        "offset": offset,
        "limit": limit,
        "workDir": str(shard_dir),
        "returnCode": return_code,
        "status": "complete" if return_code == 0 and source.get("status") else "failed",
        "analysisStatus": analysis.get("status"),
        "analysisReturnCode": analysis.get("returnCode"),
        "functionsFound": analysis.get("functionsFound", 0),
        "decompiled": (analysis.get("decompile") or {}).get("decompiled", 0),
        "sourceStatus": source.get("status"),
        "generatedSourceCandidates": source.get("generatedSourceCandidates", 0),
        "taskCount": source.get("taskCount", 0),
        "sourceByStatus": source.get("byStatus", {}),
        "analysisImageStatus": analysis_image.get("status"),
        "analysisImageTransform": analysis_image.get("transform"),
        "blockers": source.get("blockers", []),
    }


def summarize_aggregate(aggregate: dict[str, Any]) -> dict[str, Any]:
    windows = aggregate.get("windows", [])
    failed = [row for row in windows if row.get("returnCode") != 0 or row.get("sourceStatus") in {None, "blocked"}]
    return {
        "status": "failed" if failed else "complete",
        "windowsComplete": sum(1 for row in windows if row.get("returnCode") == 0),
        "windowsFailed": len(failed),
        "functionsFound": sum(int(row.get("functionsFound") or 0) for row in windows),
        "decompiled": sum(int(row.get("decompiled") or 0) for row in windows),
        "generatedSourceCandidates": sum(int(row.get("generatedSourceCandidates") or 0) for row in windows),
        "taskCount": sum(int(row.get("taskCount") or 0) for row in windows),
    }


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
