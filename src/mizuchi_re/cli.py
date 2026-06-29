"""Command-line entrypoint for the Mizuchi recovery orchestrator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .context_export import ExportConfig, export_context
from .pipeline import RecoveryConfig, RecoveryRunner
from .targets import identify_binary
from .windows import run_recovery_windows


def default_work_dir(target_path: Path) -> Path:
    identity = identify_binary(target_path)
    return Path("target/mizuchi-recover") / identity.stable_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mizuchi-recover", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    sub = parser.add_subparsers(dest="command")

    inspect = sub.add_parser("inspect", help="Resolve and identify the target binary.")
    inspect.add_argument("input", type=Path)
    inspect.add_argument("--preferred-name")

    export = sub.add_parser("export-context", help="Export an app, installer, archive, or binary tree into LLM-readable JSON/Markdown.")
    export.add_argument("input", type=Path, help="File or folder to export.")
    export.add_argument("--out-dir", type=Path, required=True, help="Output directory for manifest and per-file surrogates.")
    export.add_argument("--format", choices=["json", "md"], default="json", help="Per-file surrogate format.")
    export.add_argument("--no-extract-containers", action="store_true", help="Do not recursively extract archives/installers with 7z.")
    export.add_argument("--max-files", type=int, default=1000, help="Maximum files to visit across original and extracted trees.")
    export.add_argument("--max-depth", type=int, default=4, help="Maximum recursive container extraction depth.")
    export.add_argument("--max-text-bytes", type=int, default=2_000_000, help="Maximum text bytes copied per file.")
    export.add_argument("--max-container-members", type=int, default=300, help="Reserved member cap recorded in manifests.")
    export.add_argument("--strings-limit", type=int, default=500, help="Maximum unique strings retained for binary files.")

    recover = sub.add_parser("recover", help="Run the resumable recovery orchestration pipeline.")
    recover.add_argument("input", type=Path, help="Folder or binary path.")
    recover.add_argument("--preferred-name", help="Preferred executable basename when input is a folder.")
    recover.add_argument("--work-dir", type=Path, help="Run/state directory. Defaults to target/mizuchi-recover/<stable-target-id>.")
    recover.add_argument("--resume", action="store_true", help="Reuse complete stage receipts with matching config.")
    recover.add_argument("--force", action="store_true", help="Rerun selected stages even when receipts exist.")
    recover.add_argument("--stop-after", choices=["discover", "inspect-capabilities", "prepare-analysis-image", "export-context", "inventory-binary", "discover-functions", "analyze-functions", "generate-source-candidates", "plan-strategy", "byte-authority", "legacy-adapter", "snapshot-existing-recovery", "report"])
    recover.add_argument("--json", action="store_true", help="Emit progress as JSON lines.")
    recover.add_argument("--progress-width", type=int, default=24)
    recover.add_argument("--stage-timeout", type=int, default=300)
    recover.add_argument("--byte-authority", action="store_true", help="Emit generic byte-exact source authority package.")
    recover.add_argument("--legacy-adapters", action="store_true", help="Allow explicit dispatch to legacy target-specific scripts.")
    recover.add_argument("--snapshot-existing-recovery", metavar="LABEL", help="Copy any previously verified recovery artifacts for this target into a labeled snapshot, for example rev1.")
    recover.add_argument("--function-analysis", choices=["auto", "none", "objdump", "agentdecompile"], default="auto", help="Tool-backed function-boundary analysis mode.")
    recover.add_argument("--agentdecompile-server-url", help="Optional AgentDecompile MCP/CLI server URL. Omit to use uvx local mode.")
    recover.add_argument("--agentdecompile-mode", choices=["auto", "local"], default="local", help="AgentDecompile execution mode. local uses uvx plus PyGhidra in-process.")
    recover.add_argument("--agentdecompile-batch-size", type=int, default=25, help="Seed/decompile this many function candidates per AgentDecompile subprocess.")
    recover.add_argument("--function-facts-jsonl", type=Path, help="Machine-generated function facts JSONL, for example AgentDecompile list/decompile output.")
    recover.add_argument("--source-task-limit", type=int, default=500, help="Maximum function candidates to queue for automatic source generation.")
    recover.add_argument("--source-task-offset", type=int, default=0, help="Skip this many eligible function candidates before analysis/source generation.")
    recover.add_argument("--steamless-cli", type=Path, help="Steamless CLI used to prepare PE analysis images when applicable.")
    recover.add_argument("--context-format", choices=["json", "md"], default="json", help="LLM-readable context export format used by the recover pipeline.")
    recover.add_argument("--context-max-files", type=int, default=1000, help="Maximum files exported by the recover context stage.")
    recover.add_argument("--context-max-depth", type=int, default=4, help="Maximum recursive container extraction depth for the recover context stage.")
    recover.add_argument("--context-strings-limit", type=int, default=500, help="Maximum unique strings retained per binary in the recover context stage.")
    recover.add_argument("--no-context-extract-containers", action="store_true", help="Disable archive/installer extraction in the recover context stage.")

    windows = sub.add_parser("recover-windows", help="Run recovery across deterministic function-candidate windows.")
    windows.add_argument("input", type=Path, help="Folder or binary path.")
    windows.add_argument("--preferred-name", help="Preferred executable basename when input is a folder.")
    windows.add_argument("--work-dir", type=Path, help="Window run/state directory. Defaults to target/mizuchi-recover/<stable-target-id>-windows.")
    windows.add_argument("--resume", action="store_true", help="Reuse complete stage receipts with matching config inside each window.")
    windows.add_argument("--force", action="store_true", help="Rerun selected window stages even when receipts exist.")
    windows.add_argument("--json", action="store_true", help="Emit per-stage progress as JSON lines.")
    windows.add_argument("--progress-width", type=int, default=24)
    windows.add_argument("--stage-timeout", type=int, default=300)
    windows.add_argument("--window-size", type=int, default=25, help="Function candidates per recovery window.")
    windows.add_argument("--start-offset", type=int, default=0, help="Recoverable candidate offset for the first window.")
    windows.add_argument("--max-windows", type=int, help="Maximum number of windows to process in this invocation.")
    windows.add_argument("--function-analysis", choices=["auto", "none", "objdump", "agentdecompile"], default="agentdecompile", help="Tool-backed function-boundary analysis mode for each window.")
    windows.add_argument("--agentdecompile-server-url", help="Optional AgentDecompile MCP/CLI server URL. Omit to use uvx local mode.")
    windows.add_argument("--agentdecompile-mode", choices=["auto", "local"], default="local", help="AgentDecompile execution mode.")
    windows.add_argument("--agentdecompile-batch-size", type=int, default=25, help="Seed/decompile this many function candidates per AgentDecompile subprocess.")
    windows.add_argument("--steamless-cli", type=Path, help="Steamless CLI used to prepare PE analysis images when applicable.")
    windows.add_argument("--context-format", choices=["json", "md"], default="json", help="LLM-readable context export format used by the recover pipeline.")
    windows.add_argument("--context-max-files", type=int, default=1000, help="Maximum files exported by the recover context stage.")
    windows.add_argument("--context-max-depth", type=int, default=4, help="Maximum recursive container extraction depth for the recover context stage.")
    windows.add_argument("--context-strings-limit", type=int, default=500, help="Maximum unique strings retained per binary in the recover context stage.")
    windows.add_argument("--no-context-extract-containers", action="store_true", help="Disable archive/installer extraction in the recover context stage.")
    return parser


def run_inspect(args: argparse.Namespace) -> int:
    identity = identify_binary(args.input, args.preferred_name)
    print(json.dumps(identity.to_json(), indent=2, sort_keys=True))
    return 0


def run_recover(args: argparse.Namespace) -> int:
    if args.force and args.resume:
        raise SystemExit("--force and --resume are mutually exclusive")
    work_dir = args.work_dir or default_work_dir(args.input)
    config = RecoveryConfig(
        input_path=args.input,
        work_dir=work_dir,
        preferred_name=args.preferred_name,
        resume=args.resume,
        force=args.force,
        stop_after=args.stop_after,
        json_output=args.json,
        progress_width=args.progress_width,
        stage_timeout=args.stage_timeout,
        enable_byte_authority=args.byte_authority,
        enable_legacy_adapters=args.legacy_adapters,
        snapshot_existing_label=args.snapshot_existing_recovery,
        function_analysis=args.function_analysis,
        agentdecompile_server_url=args.agentdecompile_server_url,
        agentdecompile_mode=args.agentdecompile_mode,
        agentdecompile_batch_size=args.agentdecompile_batch_size,
        function_facts_jsonl=args.function_facts_jsonl,
        source_task_limit=args.source_task_limit,
        source_task_offset=args.source_task_offset,
        steamless_cli=args.steamless_cli,
        context_format=args.context_format,
        context_max_files=args.context_max_files,
        context_max_depth=args.context_max_depth,
        context_strings_limit=args.context_strings_limit,
        context_extract_containers=not args.no_context_extract_containers,
    )
    return RecoveryRunner(config).run()


def run_recover_windows(args: argparse.Namespace) -> int:
    if args.force and args.resume:
        raise SystemExit("--force and --resume are mutually exclusive")
    identity = identify_binary(args.input, args.preferred_name)
    work_dir = args.work_dir or Path("target/mizuchi-recover") / f"{identity.stable_id}-windows"
    config = RecoveryConfig(
        input_path=args.input,
        work_dir=work_dir,
        preferred_name=args.preferred_name,
        resume=args.resume,
        force=args.force,
        stop_after=None,
        json_output=args.json,
        progress_width=args.progress_width,
        stage_timeout=args.stage_timeout,
        function_analysis=args.function_analysis,
        agentdecompile_server_url=args.agentdecompile_server_url,
        agentdecompile_mode=args.agentdecompile_mode,
        agentdecompile_batch_size=args.agentdecompile_batch_size,
        steamless_cli=args.steamless_cli,
        context_format=args.context_format,
        context_max_files=args.context_max_files,
        context_max_depth=args.context_max_depth,
        context_strings_limit=args.context_strings_limit,
        context_extract_containers=not args.no_context_extract_containers,
    )
    summary = run_recovery_windows(
        base_config=config,
        window_size=args.window_size,
        start_offset=args.start_offset,
        max_windows=args.max_windows,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary.get("status") == "complete" else 1


def run_export_context(args: argparse.Namespace) -> int:
    manifest = export_context(
        ExportConfig(
            input_path=args.input,
            out_dir=args.out_dir,
            output_format=args.format,
            extract_containers=not args.no_extract_containers,
            max_files=args.max_files,
            max_depth=args.max_depth,
            max_text_bytes=args.max_text_bytes,
            max_container_members=args.max_container_members,
            strings_limit=args.strings_limit,
        )
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if args.command == "inspect":
        return run_inspect(args)
    if args.command == "export-context":
        return run_export_context(args)
    if args.command == "recover":
        return run_recover(args)
    if args.command == "recover-windows":
        return run_recover_windows(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
