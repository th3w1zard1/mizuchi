"""Command-line entrypoint for the Mizuchi recovery orchestrator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .context_export import ExportConfig, export_context
from .package_sweep import sweep_recovered_source_package
from .package_verify import verify_recovered_source_package
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
    windows.add_argument("--no-semantic-sweep", action="store_true", help="Do not run compiler-profile semantic source matching after assembling the recovered-source package.")
    windows.add_argument("--semantic-sweep-compiler", choices=["auto", "clang", "msvc"], default="auto", help="Compiler backend for recovered-source semantic sweep. auto prefers MSVC when cl.exe and wine are available.")
    windows.add_argument("--semantic-sweep-profile", action="append", default=[], help="Comma-separated compiler args for one semantic sweep profile. Repeat for multiple profiles.")
    windows.add_argument("--semantic-sweep-timeout", type=int, help="Timeout per semantic sweep compile/compare attempt. Defaults to --stage-timeout.")
    windows.add_argument("--semantic-sweep-max-variants-per-function", type=int, default=8, help="Maximum generated source-shape variants per recovered function.")
    windows.add_argument("--semantic-sweep-clang", default="clang", help="Clang executable used when semantic sweep compiler is clang.")
    windows.add_argument("--semantic-sweep-clang-arg", action="append", default=[], help="Extra clang argument for semantic sweep. Repeat for multiple flags.")
    windows.add_argument("--semantic-sweep-clang-target", default="i686-pc-windows-msvc", help="Optional clang target triple for semantic sweep; empty string disables target override.")
    windows.add_argument("--msvc-root", type=Path, help="MSVC root containing bin/cl.exe for semantic sweep.")
    windows.add_argument("--wine", default="wine", help="Wine executable used by the MSVC semantic sweep backend.")
    windows.add_argument("--wineprefix", type=Path, help="Wine prefix used by the MSVC semantic sweep backend.")
    windows.add_argument("--objcopy", default="objcopy", help="objcopy executable used to extract candidate .text during semantic sweep.")
    windows.add_argument("--objdump", default="objdump", help="objdump executable used for relocation/disassembly evidence during semantic sweep.")

    verify = sub.add_parser("verify-package", help="Verify a recovered-source package with explicit syntax/object tiers.")
    add_package_verify_args(verify)
    verify.add_argument("--code-compare", action="store_true", help="Also compare candidate object .text bytes against packaged target slices.")

    match = sub.add_parser("match-package", help="Compile candidates and compare code bytes against packaged target slices.")
    add_package_verify_args(match)

    sweep = sub.add_parser("sweep-package", help="Generate source-shape/compiler variants and compare each against packaged target slices.")
    add_package_verify_args(sweep)
    sweep.add_argument("--max-variants-per-function", type=int, default=8, help="Maximum generated source variants per function.")
    sweep.add_argument("--compiler-profile", "--clang-profile", dest="compiler_profile", action="append", default=[], help="Comma-separated compiler args for one profile, for example --compiler-profile=-O2 or --compiler-profile=/O2,/GS-,/Oy. Repeat for multiple profiles.")
    return parser


def add_package_verify_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("package", type=Path, help="Path to recovered-source directory or manifest.json.")
    parser.add_argument("--out-dir", type=Path, help="Verification output directory. Defaults to <package>/verification.")
    parser.add_argument("--compiler", choices=["clang", "msvc"], default="clang", help="Compiler backend used for object/code comparison.")
    parser.add_argument("--clang", default="clang", help="Clang executable used for syntax/object tiers.")
    parser.add_argument("--clang-arg", action="append", default=[], help="Extra clang argument. Repeat for multiple flags, for example --clang-arg=-O2.")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout per function/tier in seconds.")
    parser.add_argument("--no-object", action="store_true", help="Only run syntax tier; do not compile object files.")
    parser.add_argument("--clang-target", default="i686-pc-windows-msvc", help="Optional clang target triple for object tier; empty string disables target override.")
    parser.add_argument("--msvc-root", type=Path, help="MSVC root containing bin/cl.exe. Defaults to VC_ROOT or local toolchain discovery.")
    parser.add_argument("--wine", default="wine", help="Wine executable used by the MSVC backend.")
    parser.add_argument("--wineprefix", type=Path, help="Wine prefix used by the MSVC backend.")
    parser.add_argument("--objcopy", default="objcopy", help="objcopy executable used to extract candidate .text for code comparison.")
    parser.add_argument("--objdump", default="objdump", help="objdump executable used for relocations and disassembly evidence.")


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
        semantic_sweep=not args.no_semantic_sweep,
        semantic_sweep_compiler=args.semantic_sweep_compiler,
        semantic_sweep_profiles=parse_clang_profiles(args.semantic_sweep_profile) or None,
        semantic_sweep_timeout=args.semantic_sweep_timeout or args.stage_timeout,
        semantic_sweep_max_variants_per_function=args.semantic_sweep_max_variants_per_function,
        semantic_sweep_clang=args.semantic_sweep_clang,
        semantic_sweep_clang_args=args.semantic_sweep_clang_arg,
        semantic_sweep_clang_target=args.semantic_sweep_clang_target or None,
        msvc_root=args.msvc_root,
        wine=args.wine,
        wineprefix=args.wineprefix,
        objcopy=args.objcopy,
        objdump=args.objdump,
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


def run_verify_package(args: argparse.Namespace) -> int:
    report = verify_recovered_source_package(
        args.package,
        out_dir=args.out_dir,
        compiler=args.compiler,
        clang=args.clang,
        clang_args=args.clang_arg,
        timeout=args.timeout,
        object_compile=not args.no_object,
        clang_target=args.clang_target or None,
        msvc_root=args.msvc_root,
        wine=args.wine,
        wineprefix=args.wineprefix,
        code_compare=bool(getattr(args, "code_compare", False)),
        objcopy=args.objcopy,
        objdump=args.objdump,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("status") in {"syntax-ok", "object-ok", "code-match", "code-relocation-masked-match"} else 1


def run_match_package(args: argparse.Namespace) -> int:
    report = verify_recovered_source_package(
        args.package,
        out_dir=args.out_dir,
        compiler=args.compiler,
        clang=args.clang,
        clang_args=args.clang_arg,
        timeout=args.timeout,
        object_compile=not args.no_object,
        clang_target=args.clang_target or None,
        msvc_root=args.msvc_root,
        wine=args.wine,
        wineprefix=args.wineprefix,
        code_compare=True,
        objcopy=args.objcopy,
        objdump=args.objdump,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("status") in {"code-match", "code-relocation-masked-match"} else 1


def run_sweep_package(args: argparse.Namespace) -> int:
    profiles = parse_clang_profiles(args.compiler_profile)
    report = sweep_recovered_source_package(
        args.package,
        out_dir=args.out_dir,
        compiler=args.compiler,
        clang=args.clang,
        clang_args=args.clang_arg,
        clang_profiles=profiles or None,
        timeout=args.timeout,
        clang_target=args.clang_target or None,
        msvc_root=args.msvc_root,
        wine=args.wine,
        wineprefix=args.wineprefix,
        objcopy=args.objcopy,
        objdump=args.objdump,
        max_variants_per_function=args.max_variants_per_function,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("status") == "matched" else 1


def parse_clang_profiles(values: list[str]) -> list[list[str]]:
    profiles = []
    for value in values:
        if value.strip() == "":
            profiles.append([])
            continue
        profiles.append([item for item in (part.strip() for part in value.split(",")) if item])
    return profiles


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
    if args.command == "verify-package":
        return run_verify_package(args)
    if args.command == "match-package":
        return run_match_package(args)
    if args.command == "sweep-package":
        return run_sweep_package(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
