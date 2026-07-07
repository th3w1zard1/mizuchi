"""Generic imperative pipeline runner."""

from __future__ import annotations

import json
import signal
import shutil
import subprocess
import sys
import time
from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Callable

from .context_export import ExportConfig, export_context
from .functions import analyze_function_candidates_with_objdump, discover_function_candidates, write_function_candidates
from .inventory import build_binary_inventory, write_inventory
from .sourcegen import generate_source_candidates
from .source_export import export_recovered_source
from .source_parity_synthesize import main as source_parity_synthesize_main
from .source_plugin_runner import SourcePluginRunConfig, run_source_plugin_pipeline
from .state import RunState, atomic_write_json, config_fingerprint, now
from .strategy import build_strategy
from .snapshot import snapshot_existing_recovery
from .targets import TargetIdentity, identify_binary
from .tools import inspect_capabilities, resolve_script_asset, resolve_steamless_cli


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Stage:
    name: str
    description: str
    outputs: tuple[Path, ...]
    run: Callable[["RecoveryRunner", "Stage"], dict[str, Any]]


@dataclass(frozen=True)
class RecoveryConfig:
    input_path: Path
    work_dir: Path
    preferred_name: str | None = None
    resume: bool = False
    force: bool = False
    stop_after: str | None = None
    json_output: bool = False
    progress_width: int = 24
    stage_timeout: int = 300
    enable_byte_authority: bool = False
    enable_legacy_adapters: bool = False
    snapshot_existing_label: str | None = None
    function_analysis: str = "auto"
    function_facts_jsonl: Path | None = None
    source_task_limit: int = 500
    source_task_offset: int = 0
    source_synthesis_engine: str = "legacy"
    source_synthesis_mode: str = "clang"
    source_synthesis_limit: int = 25
    source_synthesis_max_variants: int = 4
    source_synthesis_semantic_only: bool = False
    source_synthesis_skip_boundary_suspect: bool = False
    source_synthesis_verify_packaged_source: bool = False
    source_synthesis_upgrade_packaged_source: bool = False
    source_synthesis_strategies: tuple[str, ...] = ()
    source_synthesis_source_qualities: tuple[str, ...] = ()
    source_synthesis_vc_root: Path | None = None
    source_synthesis_wine: str = "wine"
    source_synthesis_wineprefix: Path | None = None
    steamless_cli: Path | None = None
    context_format: str = "json"
    context_binary_analysis: str = "standard"
    context_max_files: int = 1000
    context_max_depth: int = 4
    context_strings_limit: int = 500
    context_max_index_text_chars: int = 2_000
    context_extract_containers: bool = True
    context_include_low_signal_members: bool = False


class RecoveryRunner:
    def __init__(self, config: RecoveryConfig) -> None:
        self.config = config
        self.target: TargetIdentity | None = None
        self.run_dir = config.work_dir
        self.state = RunState(self.run_dir)
        self.cancelled = False
        self.stages = self._build_stages()

    def _build_stages(self) -> list[Stage]:
        return [
            Stage("discover", "resolve target binary and content identity", (self.run_dir / "target.json",), RecoveryRunner.stage_discover),
            Stage("inspect-capabilities", "inspect local tools and reusable proof surfaces", (self.run_dir / "capabilities.json",), RecoveryRunner.stage_inspect_capabilities),
            Stage("prepare-analysis-image", "prepare the binary image used for static analysis", (self.run_dir / "analysis-target.json",), RecoveryRunner.stage_prepare_analysis_image),
            Stage("export-context", "export app/archive/resource context into LLM-readable files", (self.run_dir / "context-export/manifest.json",), RecoveryRunner.stage_export_context),
            Stage("inventory-binary", "derive executable sections, imports, symbols, and code/data ranges", (self.run_dir / "binary-inventory.json",), RecoveryRunner.stage_inventory_binary),
            Stage("discover-functions", "derive function-boundary candidates from symbols and executable ranges", (self.run_dir / "function-candidates.json",), RecoveryRunner.stage_discover_functions),
            Stage("analyze-functions", "enrich function candidates with tool-backed boundary analysis", (self.run_dir / "function-analysis.json",), RecoveryRunner.stage_analyze_functions),
            Stage("generate-source-candidates", "generate automatic source-candidate tasks from decompiler facts", (self.run_dir / "source-generation/summary.json",), RecoveryRunner.stage_generate_source_candidates),
            Stage("synthesize-source-tasks", "compile and objdiff generated source tasks against bounded target slices", (self.run_dir / "source-synthesis/summary.json",), RecoveryRunner.stage_synthesize_source_tasks),
            Stage("plan-strategy", "derive recovery strategy and required proof inputs", (self.run_dir / "strategy.json",), RecoveryRunner.stage_plan_strategy),
            Stage("byte-authority", "optionally emit a byte-exact source authority package", (self.run_dir / "byte-authority/result.json",), RecoveryRunner.stage_byte_authority),
            Stage("legacy-adapter", "optionally dispatch compatible legacy target-specific adapters", (self.run_dir / "legacy-adapter.json",), RecoveryRunner.stage_legacy_adapter),
            Stage("snapshot-existing-recovery", "snapshot previously verified recovery artifacts for this target", (self.run_dir / "snapshot-existing-recovery.json",), RecoveryRunner.stage_snapshot_existing_recovery),
            Stage("report", "write aggregate run report", (self.run_dir / "report.json",), RecoveryRunner.stage_report),
        ]

    def run(self) -> int:
        signal.signal(signal.SIGINT, self._cancel)
        signal.signal(signal.SIGTERM, self._cancel)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        selected = self.selected_stages()
        for index, stage in enumerate(self.stages, start=1):
            if stage.name not in selected:
                continue
            if self.cancelled:
                self.mark_cancelled(stage.name)
                return 130
            if self.should_skip(stage):
                self.progress(index, stage, "resume: existing complete receipt")
                self.state.event("stage-skip", stage=stage.name, reason="resume-complete")
                continue
            started = time.monotonic()
            self.progress(index, stage, stage.description)
            receipt = self.state.stage_receipt(stage.name)
            receipt.update(
                {
                    "status": "running",
                    "startedAt": now(),
                    "description": stage.description,
                    "config": self.stage_config(stage),
                    "configFingerprint": self.stage_fingerprint(stage),
                }
            )
            self.state.save()
            self.state.event("stage-start", stage=stage.name, description=stage.description)
            try:
                summary = stage.run(self, stage)
            except subprocess.CalledProcessError as exc:
                self.stage_failed(stage, started, command_error(exc), exc.returncode)
                return exc.returncode or 1
            except Exception as exc:
                self.stage_failed(stage, started, str(exc), 1)
                return 1
            receipt.update(
                {
                    "status": "complete",
                    "completedAt": now(),
                    "durationSeconds": round(time.monotonic() - started, 3),
                    "outputs": [str(path) for path in stage.outputs],
                    "summary": summary,
                    "config": self.stage_config(stage),
                    "configFingerprint": self.stage_fingerprint(stage),
                }
            )
            self.state.save()
            self.state.event("stage-complete", stage=stage.name, summary=summary)
        return 0

    def selected_stages(self) -> set[str]:
        names = [stage.name for stage in self.stages]
        if self.config.stop_after is None:
            return set(names)
        if self.config.stop_after not in names:
            raise SystemExit(f"unknown --stop-after {self.config.stop_after!r}; choices: {', '.join(names)}")
        return set(names[: names.index(self.config.stop_after) + 1])

    def should_skip(self, stage: Stage) -> bool:
        if not self.config.resume:
            return False
        if self.config.force:
            return False
        receipt = self.state.data.get("stages", {}).get(stage.name, {})
        if receipt.get("status") != "complete":
            return False
        if receipt.get("configFingerprint") != self.stage_fingerprint(stage):
            return False
        return all(path.exists() for path in stage.outputs)

    def stage_config(self, stage: Stage) -> dict[str, Any]:
        return {
            "input": str(self.config.input_path),
            "preferredName": self.config.preferred_name,
            "enableByteAuthority": self.config.enable_byte_authority,
            "enableLegacyAdapters": self.config.enable_legacy_adapters,
            "snapshotExistingLabel": self.config.snapshot_existing_label,
            "functionAnalysis": self.config.function_analysis,
            "functionFactsJsonl": str(self.config.function_facts_jsonl) if self.config.function_facts_jsonl else None,
            "sourceTaskLimit": self.config.source_task_limit,
            "sourceTaskOffset": self.config.source_task_offset,
            "sourceSynthesisEngine": self.config.source_synthesis_engine,
            "sourceSynthesisMode": self.config.source_synthesis_mode,
            "sourceSynthesisLimit": self.config.source_synthesis_limit,
            "sourceSynthesisMaxVariants": self.config.source_synthesis_max_variants,
            "sourceSynthesisSemanticOnly": self.config.source_synthesis_semantic_only,
            "sourceSynthesisSkipBoundarySuspect": self.config.source_synthesis_skip_boundary_suspect,
            "sourceSynthesisVerifyPackagedSource": self.config.source_synthesis_verify_packaged_source,
            "sourceSynthesisUpgradePackagedSource": self.config.source_synthesis_upgrade_packaged_source,
            "sourceSynthesisStrategies": list(self.config.source_synthesis_strategies),
            "sourceSynthesisSourceQualities": list(self.config.source_synthesis_source_qualities),
            "sourceSynthesisVcRoot": str(self.config.source_synthesis_vc_root) if self.config.source_synthesis_vc_root else None,
            "sourceSynthesisWine": self.config.source_synthesis_wine,
            "sourceSynthesisWineprefix": str(self.config.source_synthesis_wineprefix) if self.config.source_synthesis_wineprefix else None,
            "steamlessCli": str(self.config.steamless_cli) if self.config.steamless_cli else None,
            "contextFormat": self.config.context_format,
            "contextBinaryAnalysis": self.config.context_binary_analysis,
            "contextMaxFiles": self.config.context_max_files,
            "contextMaxDepth": self.config.context_max_depth,
            "contextStringsLimit": self.config.context_strings_limit,
            "contextMaxIndexTextChars": self.config.context_max_index_text_chars,
            "contextExtractContainers": self.config.context_extract_containers,
            "contextIncludeLowSignalMembers": self.config.context_include_low_signal_members,
            "stageTimeout": self.config.stage_timeout,
            "stage": stage.name,
        }

    def stage_fingerprint(self, stage: Stage) -> str:
        return config_fingerprint(self.stage_config(stage))

    def progress(self, index: int, stage: Stage, message: str) -> None:
        if self.config.json_output:
            print(json.dumps({"event": "progress", "stage": stage.name, "index": index, "total": len(self.stages), "message": message}), flush=True)
            return
        done = int(self.config.progress_width * (index - 1) / max(len(self.stages), 1))
        bar = "#" * done + "-" * (self.config.progress_width - done)
        print(f"[{bar}] {index}/{len(self.stages)} {stage.name}: {message}", flush=True)

    def _cancel(self, signum: int, _frame: Any) -> None:
        self.cancelled = True
        self.state.event("cancel-requested", signal=signum)

    def mark_cancelled(self, stage_name: str) -> None:
        self.state.data["status"] = "cancelled"
        self.state.data["cancelledAt"] = now()
        self.state.data["cancelledDuring"] = stage_name
        self.state.save()

    def stage_failed(self, stage: Stage, started: float, reason: str, return_code: int) -> None:
        receipt = self.state.stage_receipt(stage.name)
        receipt.update(
            {
                "status": "failed",
                "completedAt": now(),
                "durationSeconds": round(time.monotonic() - started, 3),
                "reason": reason,
                "returnCode": return_code,
            }
        )
        self.state.save()
        self.state.event("stage-failed", stage=stage.name, reason=reason, returnCode=return_code)

    def load_target(self) -> TargetIdentity:
        if self.target is not None:
            return self.target
        target_path = self.run_dir / "target.json"
        if target_path.exists():
            data = json.loads(target_path.read_text(encoding="utf-8"))
            self.target = TargetIdentity(
                input_path=Path(data["inputPath"]),
                binary_path=Path(data["binaryPath"]),
                sha256=data["sha256"],
                size=int(data["size"]),
                format=data["format"],
                architecture_hint=data["architectureHint"],
                stable_id=data["stableId"],
            )
            return self.target
        self.target = identify_binary(self.config.input_path, self.config.preferred_name)
        return self.target

    def load_analysis_target(self) -> TargetIdentity:
        target = self.load_target()
        analysis_path = target.binary_path
        analysis_file = self.run_dir / "analysis-target.json"
        if analysis_file.exists():
            data = json.loads(analysis_file.read_text(encoding="utf-8"))
            analysis_path = Path(data.get("analysisBinaryPath") or analysis_path)
        if analysis_path == target.binary_path:
            return target
        analysis = identify_binary(analysis_path, analysis_path.name)
        return TargetIdentity(
            input_path=target.input_path,
            binary_path=analysis.binary_path,
            sha256=analysis.sha256,
            size=analysis.size,
            format=analysis.format,
            architecture_hint=analysis.architecture_hint,
            stable_id=target.stable_id,
        )

    def stage_discover(self, _stage: Stage) -> dict[str, Any]:
        self.target = identify_binary(self.config.input_path, self.config.preferred_name)
        atomic_write_json(self.run_dir / "target.json", self.target.to_json())
        self.state.data["target"] = self.target.to_json()
        self.state.save()
        return self.target.to_json()

    def stage_inspect_capabilities(self, _stage: Stage) -> dict[str, Any]:
        capabilities = inspect_capabilities(ROOT)
        atomic_write_json(self.run_dir / "capabilities.json", capabilities)
        return capabilities

    def stage_prepare_analysis_image(self, _stage: Stage) -> dict[str, Any]:
        target = self.load_target()
        out_path = self.run_dir / "analysis-target.json"
        summary: dict[str, Any] = {
            "schema": "mizuchi.analysis-target.v1",
            "originalBinaryPath": str(target.binary_path),
            "analysisBinaryPath": str(target.binary_path),
            "status": "original",
            "transform": None,
            "claimBoundary": "analysis image is for static recovery inputs; target identity remains the original binary",
        }
        if target.format == "pe":
            capabilities = json.loads((self.run_dir / "capabilities.json").read_text(encoding="utf-8"))
            mono = ((capabilities.get("tools") or {}).get("mono") or {}).get("available")
            steamless = resolve_steamless_cli(ROOT, self.config.steamless_cli)
            if mono and steamless is not None:
                image_dir = (self.run_dir / "analysis-image").resolve()
                image_dir.mkdir(parents=True, exist_ok=True)
                original_copy = image_dir / target.binary_path.name
                if not original_copy.exists() or original_copy.stat().st_size != target.binary_path.stat().st_size:
                    shutil.copy2(target.binary_path, original_copy)
                unpacked = Path(str(original_copy) + ".unpacked.exe")
                steamless_result: subprocess.CompletedProcess[str] | None = None
                if not unpacked.exists():
                    steamless_result = subprocess.run(
                        ["mono", str(steamless), "--quiet", "--keepbind", "--dumppayload", "--dumpdrmp", str(original_copy)],
                        cwd=ROOT,
                        text=True,
                        capture_output=True,
                        check=False,
                        timeout=self.config.stage_timeout,
                    )
                if unpacked.exists():
                    summary.update(
                        {
                            "analysisBinaryPath": str(unpacked),
                            "status": "transformed",
                            "transform": "steamless-unpacked-pe",
                            "transformTool": str(steamless),
                            "transformReturnCode": steamless_result.returncode if steamless_result is not None else None,
                            "analysisSha256": sha256_file(unpacked),
                            "analysisSize": unpacked.stat().st_size,
                        }
                    )
                else:
                    summary.update(
                        {
                            "status": "original",
                            "transformAttempted": "steamless-unpacked-pe",
                            "transformTool": str(steamless),
                            "transformResult": "not-produced",
                            "transformReturnCode": steamless_result.returncode if steamless_result is not None else None,
                            "transformStdout": steamless_result.stdout[-4000:] if steamless_result is not None else "",
                            "transformStderr": steamless_result.stderr[-4000:] if steamless_result is not None else "",
                        }
                    )
        atomic_write_json(out_path, summary)
        return summary

    def stage_export_context(self, _stage: Stage) -> dict[str, Any]:
        target = self.load_target()
        manifest = export_context(
            ExportConfig(
                input_path=target.input_path,
                out_dir=self.run_dir / "context-export",
                output_format=self.config.context_format,
                binary_analysis=self.config.context_binary_analysis,
                extract_containers=self.config.context_extract_containers,
                include_low_signal_members=self.config.context_include_low_signal_members,
                max_files=self.config.context_max_files,
                max_depth=self.config.context_max_depth,
                strings_limit=self.config.context_strings_limit,
                max_index_text_chars=self.config.context_max_index_text_chars,
            )
        )
        return {
            "status": "complete",
            "manifest": str(self.run_dir / "context-export/manifest.json"),
            "filesVisited": manifest.get("filesVisited"),
            "filesExported": manifest.get("filesExported"),
            "truncated": manifest.get("truncated"),
            "outputFormat": manifest.get("outputFormat"),
        }

    def stage_inventory_binary(self, _stage: Stage) -> dict[str, Any]:
        target = self.load_analysis_target()
        inventory = build_binary_inventory(target)
        write_inventory(self.run_dir / "binary-inventory.json", inventory)
        summary = inventory.get("summary", {})
        return {
            "format": inventory.get("format"),
            "status": inventory.get("status"),
            "entryVa": inventory.get("entryVa"),
            "summary": summary,
        }

    def stage_discover_functions(self, _stage: Stage) -> dict[str, Any]:
        inventory = json.loads((self.run_dir / "binary-inventory.json").read_text(encoding="utf-8"))
        candidates = discover_function_candidates(inventory)
        write_function_candidates(self.run_dir / "function-candidates.json", candidates)
        return candidates.get("summary", {})

    def stage_analyze_functions(self, _stage: Stage) -> dict[str, Any]:
        out_path = self.run_dir / "function-analysis.json"
        candidates_path = self.run_dir / "function-candidates.json"
        candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
        if self.config.function_analysis == "none":
            summary = {"status": "skipped", "reason": "--function-analysis none"}
            atomic_write_json(out_path, summary)
            return summary
        capabilities = json.loads((self.run_dir / "capabilities.json").read_text(encoding="utf-8"))
        objdump = (capabilities.get("tools") or {}).get("objdump") or {}
        if self.config.function_analysis in {"auto", "objdump"} and objdump.get("available"):
            target = self.load_analysis_target()
            analyzed = analyze_function_candidates_with_objdump(candidates, target.binary_path, self.config.stage_timeout)
            write_function_candidates(candidates_path, analyzed)
            atomic_write_json(out_path, analyzed.get("toolAnalysis", {}))
            if self.config.function_analysis == "objdump" or int((analyzed.get("toolAnalysis") or {}).get("candidatesAdded") or 0) > 0:
                return analyzed.get("toolAnalysis", {})
            candidates = analyzed
        summary = {"status": "skipped", "reason": f"no available analyzer for mode {self.config.function_analysis!r}"}
        atomic_write_json(out_path, summary)
        return summary

    def stage_plan_strategy(self, _stage: Stage) -> dict[str, Any]:
        target = self.load_target()
        capabilities_path = self.run_dir / "capabilities.json"
        capabilities = json.loads(capabilities_path.read_text(encoding="utf-8"))
        inventory = json.loads((self.run_dir / "binary-inventory.json").read_text(encoding="utf-8"))
        functions = json.loads((self.run_dir / "function-candidates.json").read_text(encoding="utf-8"))
        source_generation = json.loads((self.run_dir / "source-generation/summary.json").read_text(encoding="utf-8"))
        source_synthesis = json.loads((self.run_dir / "source-synthesis/summary.json").read_text(encoding="utf-8"))
        strategy = build_strategy(target, capabilities, inventory, functions, source_generation, source_synthesis)
        atomic_write_json(self.run_dir / "strategy.json", strategy)
        return {
            "format": target.format,
            "architectureHint": target.architecture_hint,
            "blockers": strategy["blockers"],
            "lanes": {lane["name"]: lane["status"] for lane in strategy["lanes"]},
        }

    def stage_generate_source_candidates(self, _stage: Stage) -> dict[str, Any]:
        target = self.load_target()
        candidates = json.loads((self.run_dir / "function-candidates.json").read_text(encoding="utf-8"))
        inventory = json.loads((self.run_dir / "binary-inventory.json").read_text(encoding="utf-8"))
        summary = generate_source_candidates(
            target=target.to_json(),
            function_candidates=candidates,
            out_dir=self.run_dir / "source-generation",
            inventory=inventory,
            function_facts_jsonl=self.config.function_facts_jsonl or default_function_facts_path(self.run_dir),
            limit=self.config.source_task_limit,
            offset=self.config.source_task_offset,
        )
        atomic_write_json(self.run_dir / "source-generation/summary.json", summary)
        return summary

    def stage_synthesize_source_tasks(self, _stage: Stage) -> dict[str, Any]:
        out_dir = self.run_dir / "source-synthesis"
        summary_path = out_dir / "summary.json"
        mode = self.config.source_synthesis_mode
        tasks_path = self.run_dir / "source-generation" / "tasks.jsonl"
        if mode == "none":
            summary = {
                "schema": "mizuchi.source-parity-synthesis-summary.v1",
                "status": "skipped",
                "reason": "disabled with --source-synthesis none",
                "sourceTasks": [str(tasks_path)],
                "claimBoundary": "source synthesis skipped; no source parity claim",
            }
            atomic_write_json(summary_path, summary)
            return summary
        if not tasks_path.exists():
            summary = {
                "schema": "mizuchi.source-parity-synthesis-summary.v1",
                "status": "skipped",
                "reason": "source-generation/tasks.jsonl missing",
                "sourceTasks": [str(tasks_path)],
                "claimBoundary": "source synthesis skipped; no source parity claim",
            }
            atomic_write_json(summary_path, summary)
            return summary
        if self.config.source_synthesis_engine == "plugin":
            (out_dir / "empty-queue.jsonl").parent.mkdir(parents=True, exist_ok=True)
            (out_dir / "empty-queue.jsonl").write_text("", encoding="utf-8")
            summary = run_source_plugin_pipeline(
                SourcePluginRunConfig(
                    queue=out_dir / "empty-queue.jsonl",
                    source_tasks=[tasks_path],
                    source_tasks_only=True,
                    out_dir=out_dir,
                    limit=self.config.source_synthesis_limit,
                    max_variants_per_function=self.config.source_synthesis_max_variants,
                    max_retries=self.config.source_synthesis_max_variants,
                    strategies=set(self.config.source_synthesis_strategies) or None,
                    source_qualities=set(self.config.source_synthesis_source_qualities) or None,
                    compiler=compiler_for_source_synthesis_mode(mode),
                    clang="clang-cl" if mode == "clang-cl" else "clang",
                    dry_run=mode == "dry-run",
                    semantic_only=self.config.source_synthesis_semantic_only or mode == "msvc",
                    skip_boundary_suspect=self.config.source_synthesis_skip_boundary_suspect or mode == "msvc",
                    clean=True,
                    vc_root=self.config.source_synthesis_vc_root,
                    wine=self.config.source_synthesis_wine,
                    wineprefix=self.config.source_synthesis_wineprefix,
                    timeout=self.config.stage_timeout,
                )
            )
            export_summary = self.export_synthesized_source_slices(summary)
            summary["runRootRecoveredSourceExport"] = export_summary
            atomic_write_json(summary_path, summary)
            return summary
        if self.config.source_synthesis_engine != "legacy":
            raise ValueError(f"unknown source synthesis engine: {self.config.source_synthesis_engine}")
        argv = [
            "--queue",
            str(self.run_dir / "source-synthesis" / "empty-queue.jsonl"),
            "--source-tasks",
            str(tasks_path),
            "--source-tasks-only",
            "--remaining-features",
            str(self.run_dir / "source-synthesis" / "empty-remaining-features.jsonl"),
            "--retrieval",
            str(self.run_dir / "source-synthesis" / "empty-retrieval.jsonl"),
            "--out-dir",
            str(out_dir),
            "--limit",
            str(self.config.source_synthesis_limit),
            "--max-variants-per-function",
            str(self.config.source_synthesis_max_variants),
            "--timeout",
            str(self.config.stage_timeout),
        ]
        if mode == "dry-run":
            argv.append("--dry-run")
        elif mode == "clang":
            argv.extend(["--compiler", "clang"])
        elif mode == "clang-cl":
            argv.extend(["--compiler", "clang-cl", "--clang", "clang-cl"])
        elif mode == "msvc":
            argv.extend(["--compiler", "msvc"])
            argv.append("--semantic-only")
            argv.append("--skip-boundary-suspect")
        else:
            raise ValueError(f"unknown source synthesis mode: {mode}")
        if self.config.source_synthesis_semantic_only and "--semantic-only" not in argv:
            argv.append("--semantic-only")
        if self.config.source_synthesis_skip_boundary_suspect and "--skip-boundary-suspect" not in argv:
            argv.append("--skip-boundary-suspect")
        if self.config.source_synthesis_verify_packaged_source:
            argv.append("--verify-packaged-source")
        if self.config.source_synthesis_upgrade_packaged_source:
            argv.append("--upgrade-packaged-source")
        if self.config.source_synthesis_strategies:
            argv.extend(["--strategies", ",".join(self.config.source_synthesis_strategies)])
        for quality in self.config.source_synthesis_source_qualities:
            argv.extend(["--source-quality", quality])
        if self.config.source_synthesis_vc_root:
            argv.extend(["--vc-root", str(self.config.source_synthesis_vc_root)])
        if self.config.source_synthesis_wine:
            argv.extend(["--wine", self.config.source_synthesis_wine])
        if self.config.source_synthesis_wineprefix:
            argv.extend(["--wineprefix", str(self.config.source_synthesis_wineprefix)])
        (out_dir / "empty-queue.jsonl").parent.mkdir(parents=True, exist_ok=True)
        for empty in ("empty-queue.jsonl", "empty-remaining-features.jsonl", "empty-retrieval.jsonl"):
            (out_dir / empty).write_text("", encoding="utf-8")
        stdout = StringIO()
        with redirect_stdout(stdout):
            rc = source_parity_synthesize_main(argv)
        raw_stdout = stdout.getvalue()
        (out_dir / "synthesis.stdout").write_text(raw_stdout, encoding="utf-8")
        if rc != 0:
            raise subprocess.CalledProcessError(rc, ["source_parity_synthesize", *argv], output=raw_stdout)
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        else:
            summary = json.loads(raw_stdout)
        export_summary = self.export_synthesized_source_slices(summary)
        summary["recoveredSourceExport"] = export_summary
        atomic_write_json(summary_path, summary)
        return summary

    def export_synthesized_source_slices(self, synthesis_summary: dict[str, Any]) -> dict[str, Any]:
        summaries = []
        for key in ("codeSliceMatchesPath", "sourceShapeMatchesPath", "acceptedPath"):
            value = synthesis_summary.get(key)
            if value:
                summaries.append(Path(str(value)))
        if not summaries:
            return {
                "schema": "mizuchi.recovered-source-export.v1",
                "status": "skipped",
                "reason": "source synthesis did not publish match JSONL paths",
                "claimBoundary": "no recovered source export was produced",
            }
        return export_recovered_source(
            summaries,
            out_dir=self.run_dir / "recovered-source",
            source_name="source_slices.c",
        )

    def stage_byte_authority(self, _stage: Stage) -> dict[str, Any]:
        target = self.load_target()
        out_dir = self.run_dir / "byte-authority"
        result_path = out_dir / "result.json"
        if not self.config.enable_byte_authority:
            summary = {"status": "skipped", "reason": "enable with --byte-authority", "claimBoundary": "no semantic source claim"}
            atomic_write_json(result_path, summary)
            return summary
        one_shot_script = resolve_script_asset(ROOT, "one-shot-source.py")
        if one_shot_script is None:
            raise FileNotFoundError("one-shot-source.py is not available in checkout scripts or installed package data")
        cmd = [
            sys.executable,
            str(one_shot_script),
            "--binary",
            str(target.binary_path),
            "--out",
            str(out_dir / "package"),
            "--result-out",
            str(result_path),
        ]
        subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=True, timeout=self.config.stage_timeout)
        return json.loads(result_path.read_text(encoding="utf-8"))

    def stage_legacy_adapter(self, _stage: Stage) -> dict[str, Any]:
        target = self.load_target()
        out_path = self.run_dir / "legacy-adapter.json"
        if not self.config.enable_legacy_adapters:
            summary = {"status": "skipped", "reason": "enable with --legacy-adapters"}
            atomic_write_json(out_path, summary)
            return summary
        summary = {
            "status": "not-dispatched",
            "reason": "legacy adapters remain target-specific and are intentionally isolated behind this explicit flag",
            "target": target.to_json(),
        }
        atomic_write_json(out_path, summary)
        return summary

    def stage_snapshot_existing_recovery(self, _stage: Stage) -> dict[str, Any]:
        out_path = self.run_dir / "snapshot-existing-recovery.json"
        label = self.config.snapshot_existing_label
        if not label:
            summary = {"status": "skipped", "reason": "enable with --snapshot-existing-recovery <label>"}
            atomic_write_json(out_path, summary)
            return summary
        target = self.load_target()
        summary = snapshot_existing_recovery(target.sha256, self.run_dir / "snapshots", label)
        atomic_write_json(out_path, summary)
        return summary

    def stage_report(self, _stage: Stage) -> dict[str, Any]:
        report = {
            "schema": "mizuchi.recover.report.v1",
            "generatedAt": now(),
            "state": str(self.state.state_path),
            "events": str(self.state.events_path),
            "target": json.loads((self.run_dir / "target.json").read_text(encoding="utf-8")),
            "analysisImage": json.loads((self.run_dir / "analysis-target.json").read_text(encoding="utf-8")),
            "contextExport": json.loads((self.run_dir / "context-export/manifest.json").read_text(encoding="utf-8")),
            "binaryInventory": json.loads((self.run_dir / "binary-inventory.json").read_text(encoding="utf-8")),
            "functionCandidates": json.loads((self.run_dir / "function-candidates.json").read_text(encoding="utf-8")),
            "functionAnalysis": json.loads((self.run_dir / "function-analysis.json").read_text(encoding="utf-8")),
            "sourceGeneration": json.loads((self.run_dir / "source-generation/summary.json").read_text(encoding="utf-8")),
            "sourceSynthesis": json.loads((self.run_dir / "source-synthesis/summary.json").read_text(encoding="utf-8")),
            "recoveredSource": json.loads((self.run_dir / "recovered-source/simple_matches.manifest.json").read_text(encoding="utf-8"))
            if (self.run_dir / "recovered-source/simple_matches.manifest.json").exists()
            else {"status": "missing", "claimBoundary": "no recovered source manifest exists"},
            "strategy": json.loads((self.run_dir / "strategy.json").read_text(encoding="utf-8")),
            "byteAuthority": json.loads((self.run_dir / "byte-authority/result.json").read_text(encoding="utf-8")),
            "legacyAdapter": json.loads((self.run_dir / "legacy-adapter.json").read_text(encoding="utf-8")),
            "snapshotExistingRecovery": json.loads((self.run_dir / "snapshot-existing-recovery.json").read_text(encoding="utf-8")),
            "fullSourceParity": False,
        }
        atomic_write_json(self.run_dir / "report.json", report)
        self.state.data["status"] = "complete"
        self.state.data["report"] = str(self.run_dir / "report.json")
        self.state.save()
        return {"report": str(self.run_dir / "report.json"), "fullSourceParity": False}


def compiler_for_source_synthesis_mode(mode: str) -> str:
    if mode == "dry-run":
        return "clang"
    if mode in {"clang", "clang-cl", "msvc"}:
        return mode
    raise ValueError(f"unknown source synthesis mode: {mode}")


def command_error(exc: subprocess.CalledProcessError) -> str:
    stderr = (exc.stderr or "").strip()
    stdout = (exc.output or "").strip()
    return (stderr or stdout or str(exc))[-4000:]


def default_function_facts_path(run_dir: Path) -> Path | None:
    path = run_dir / "function-facts.jsonl"
    return path if path.exists() else None


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
