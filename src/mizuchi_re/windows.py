"""Windowed recovery orchestration for large binaries."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

from .package_sweep import sweep_recovered_source_package
from .package_verify import resolve_msvc_root
from .pipeline import RecoveryConfig, RecoveryRunner
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

    source_package = build_recovered_source_package(base_dir, windows)
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
    failed = [row for row in windows if row.get("returnCode") != 0 or row.get("sourceStatus") in {None, "blocked"}]
    status = "failed" if failed else "complete"
    semantic_sweep = aggregate.get("semanticSweep")
    if status == "complete" and isinstance(semantic_sweep, dict) and semantic_sweep.get("enabled"):
        if semantic_sweep.get("status") != "matched":
            status = "semantic-incomplete"
    return {
        "status": status,
        "windowsComplete": sum(1 for row in windows if row.get("returnCode") == 0),
        "windowsFailed": len(failed),
        "functionsFound": sum(int(row.get("functionsFound") or 0) for row in windows),
        "decompiled": sum(int(row.get("decompiled") or 0) for row in windows),
        "generatedSourceCandidates": sum(int(row.get("generatedSourceCandidates") or 0) for row in windows),
        "freshGeneratedSourceCandidates": sum(int(row.get("freshGeneratedSourceCandidates") or 0) for row in windows),
        "reusedSourceCandidates": sum(int(row.get("reusedSourceCandidates") or 0) for row in windows),
        "taskCount": sum(int(row.get("taskCount") or 0) for row in windows),
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
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


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
                        copied_c = functions_dir / f"{stem}.c"
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
        "claimBoundary": "packaged sources are generated-unverified decompiler candidates until compiler and objdiff gates accept them",
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


def safe_function_file_stem(task: dict[str, Any]) -> str:
    name = str(task.get("name") or "sub")
    address = task.get("address")
    suffix = f"{int(address):08x}" if address is not None else "unknown"
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
