"""Command-line entrypoint for the Mizuchi recovery orchestrator."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .agentdecompile import run_agentdecompile_analysis
from .context_batch import main as context_batch_main
from .context_export import ExportConfig, export_context
from .package_sweep import sweep_recovered_source_package
from .package_verify import verify_recovered_source_package
from .pipeline import RecoveryConfig, RecoveryRunner
from .source_parity_profile_corpus import main as source_parity_profile_corpus_main
from .source_parity_synthesize import main as source_parity_synthesize_main
from .targets import identify_binary
from .tools import resolve_steamless_cli
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
    export.add_argument("--binary-analysis", choices=["light", "standard", "deep"], default="standard", help="Binary analysis depth for PE/ELF/container surrogates.")
    export.add_argument("--no-extract-containers", action="store_true", help="Do not recursively extract archives/installers with 7z.")
    export.add_argument("--include-low-signal-members", action="store_true", help="Also export low-signal extracted members such as cursor/icon resources.")
    export.add_argument("--max-files", type=int, default=1000, help="Maximum files to visit across original and extracted trees.")
    export.add_argument("--max-depth", type=int, default=4, help="Maximum recursive container extraction depth.")
    export.add_argument("--max-hash-bytes", type=int, default=512_000_000, help="Maximum bytes hashed per file; larger files record hashScope=prefix.")
    export.add_argument("--max-text-bytes", type=int, default=2_000_000, help="Maximum text bytes copied per file.")
    export.add_argument("--max-binary-analysis-bytes", type=int, default=256_000_000, help="Skip expensive whole-file binary tools above this size.")
    export.add_argument("--max-container-members", type=int, default=300, help="Maximum extracted container members exported per container.")
    export.add_argument("--strings-limit", type=int, default=500, help="Maximum unique strings retained for binary files.")
    export.add_argument("--max-index-text-chars", type=int, default=2_000, help="Maximum text copied into the consolidated LLM_CONTEXT index per file.")

    batch = sub.add_parser("export-context-batch", help="Find EXEs/installers/archives under a tree and export each into LLM-readable context.")
    batch.add_argument("input", type=Path, help="File or directory tree to batch-export.")
    batch.add_argument("--out-dir", type=Path, required=True, help="Output directory for aggregate and per-item manifests.")
    batch.add_argument("--format", choices=["json", "md"], default="json", help="Per-file surrogate format.")
    batch.add_argument("--binary-analysis", choices=["light", "standard", "deep"], default="standard", help="Binary analysis depth for PE/ELF/container surrogates.")
    batch.add_argument("--no-extract-containers", action="store_true", help="Do not recursively extract archives/installers with 7z.")
    batch.add_argument("--include-low-signal-members", action="store_true", help="Also export low-signal extracted members such as cursor/icon resources.")
    batch.add_argument("--max-items", type=int, default=25, help="Maximum root items to export from the input tree.")
    batch.add_argument("--min-size", type=int, default=0, help="Ignore candidate files smaller than this many bytes.")
    batch.add_argument("--item-mode", choices=["matching-files", "top-level"], default="matching-files", help="matching-files scans recursively for suffixes; top-level exports immediate child app/installer directories plus matching files.")
    batch.add_argument("--suffix", action="append", default=[], help="Suffix or comma-separated suffix list. Defaults to EXE/archive/binary suffixes.")
    batch.add_argument("--max-files-per-item", type=int, default=250, help="Maximum files exported per discovered item.")
    batch.add_argument("--max-depth", type=int, default=3, help="Maximum recursive container extraction depth per item.")
    batch.add_argument("--max-hash-bytes", type=int, default=64 * 1024 * 1024, help="Maximum bytes hashed per file; larger files record hashScope=prefix.")
    batch.add_argument("--max-text-bytes", type=int, default=2_000_000, help="Maximum text bytes copied per file.")
    batch.add_argument("--max-binary-analysis-bytes", type=int, default=256_000_000, help="Skip expensive whole-file binary tools above this size.")
    batch.add_argument("--max-container-members", type=int, default=120, help="Maximum extracted members exported per container.")
    batch.add_argument("--strings-limit", type=int, default=200, help="Maximum unique strings retained per binary.")
    batch.add_argument("--max-index-text-chars", type=int, default=2_000, help="Maximum text copied into each consolidated LLM_CONTEXT index per file.")

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
    recover.add_argument("--context-binary-analysis", choices=["light", "standard", "deep"], default="standard", help="Binary analysis depth for the recover context stage.")
    recover.add_argument("--context-max-files", type=int, default=1000, help="Maximum files exported by the recover context stage.")
    recover.add_argument("--context-max-depth", type=int, default=4, help="Maximum recursive container extraction depth for the recover context stage.")
    recover.add_argument("--context-strings-limit", type=int, default=500, help="Maximum unique strings retained per binary in the recover context stage.")
    recover.add_argument("--context-max-index-text-chars", type=int, default=2_000, help="Maximum text copied into the recover context LLM_CONTEXT index per file.")
    recover.add_argument("--no-context-extract-containers", action="store_true", help="Disable archive/installer extraction in the recover context stage.")
    recover.add_argument("--context-include-low-signal-members", action="store_true", help="Also export low-signal extracted members such as cursor/icon resources during recover context export.")

    windows = sub.add_parser("recover-windows", help="Run recovery across deterministic function-candidate windows.")
    windows.add_argument("input", type=Path, help="Folder or binary path.")
    windows.add_argument("--preferred-name", help="Preferred executable basename when input is a folder.")
    windows.add_argument("--work-dir", type=Path, help="Window run/state directory. Defaults to target/mizuchi-recover/<stable-target-id>-windows.")
    windows.add_argument("--resume", action="store_true", help="Reuse complete stage receipts and advance to incomplete windows while preserving prior windows in the recovered-source package.")
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
    windows.add_argument("--context-binary-analysis", choices=["light", "standard", "deep"], default="standard", help="Binary analysis depth for the recover context stage.")
    windows.add_argument("--context-max-files", type=int, default=1000, help="Maximum files exported by the recover context stage.")
    windows.add_argument("--context-max-depth", type=int, default=4, help="Maximum recursive container extraction depth for the recover context stage.")
    windows.add_argument("--context-strings-limit", type=int, default=500, help="Maximum unique strings retained per binary in the recover context stage.")
    windows.add_argument("--context-max-index-text-chars", type=int, default=2_000, help="Maximum text copied into the recover context LLM_CONTEXT index per file.")
    windows.add_argument("--no-context-extract-containers", action="store_true", help="Disable archive/installer extraction in the recover context stage.")
    windows.add_argument("--context-include-low-signal-members", action="store_true", help="Also export low-signal extracted members such as cursor/icon resources during recover context export.")
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
    windows.add_argument("--source-parity-synthesis", action="store_true", help="Run generated source-parity synthesis from explicit queue/inventory artifacts after the package sweep.")
    windows.add_argument("--source-parity-queue", type=Path, help="Recovery queue JSONL consumed by source-parity synthesis.")
    windows.add_argument("--source-parity-inventory", type=Path, help="Function inventory JSONL used to slice target bytes for source-parity synthesis.")
    windows.add_argument("--source-parity-remaining-features", type=Path, help="Optional strategy/features JSONL for source-parity synthesis.")
    windows.add_argument("--source-parity-retrieval", type=Path, help="Optional nearest-example retrieval JSONL for source-parity synthesis.")
    windows.add_argument("--source-parity-matched-summary", type=Path, action="append", default=[], help="Optional existing matched-summary JSONL to skip already matched functions. Repeat for multiple files.")
    windows.add_argument("--source-parity-out-dir", type=Path, help="Output directory for source-parity synthesis. Defaults to <work-dir>/source-parity-synthesis.")
    windows.add_argument("--source-parity-limit", type=int, default=25, help="Maximum queued functions to inspect during source-parity synthesis.")
    windows.add_argument("--source-parity-offset", type=int, default=0, help="Eligible queued functions to skip before source-parity synthesis.")
    windows.add_argument("--source-parity-max-variants-per-function", type=int, default=8, help="Maximum generated source variants per queued function for source-parity synthesis.")
    windows.add_argument("--source-parity-strategies", help="Comma-separated strategy/tag filter for source-parity synthesis.")
    windows.add_argument(
        "--source-parity-compiler-profile",
        action="append",
        default=[],
        help="Comma-separated compiler args for one source-parity profile, e.g. '/O2,/GS-,/Oy'. Repeat for multiple profiles.",
    )
    windows.add_argument("--source-parity-dry-run", action="store_true", help="Generate source-parity candidates without compiling or objdiff-gating them.")
    windows.add_argument("--source-parity-clean", action="store_true", help="Delete previous source-parity synthesis output before running.")
    windows.add_argument("--source-parity-vc-root", type=Path, help="MSVC/VC Toolkit root used by source-parity synthesis. Defaults to --msvc-root when omitted.")
    windows.add_argument("--source-parity-timeout", type=int, help="Timeout per source-parity synthesis compile/objdiff attempt. Defaults to --semantic-sweep-timeout or --stage-timeout.")
    windows.add_argument("--source-parity-progress-every", type=int, default=0, help="Emit source-parity synthesis progress to stderr every N generated candidates.")

    verify = sub.add_parser("verify-package", help="Verify a recovered-source package with explicit syntax/object tiers.")
    add_package_verify_args(verify)
    verify.add_argument("--code-compare", action="store_true", help="Also compare candidate object .text bytes against packaged target slices.")

    match = sub.add_parser("match-package", help="Compile candidates and compare code bytes against packaged target slices.")
    add_package_verify_args(match)

    sweep = sub.add_parser("sweep-package", help="Generate source-shape/compiler variants and compare each against packaged target slices.")
    add_package_verify_args(sweep)
    sweep.add_argument("--max-variants-per-function", type=int, default=8, help="Maximum generated source variants per function.")
    sweep.add_argument("--compiler-profile", "--clang-profile", dest="compiler_profile", action="append", default=[], help="Comma-separated compiler args for one profile, for example --compiler-profile=-O2 or --compiler-profile=/O2,/GS-,/Oy. Repeat for multiple profiles.")

    agent = sub.add_parser("agentdecompile", help="Run AgentDecompile directly against one binary and emit function facts JSONL.")
    agent.add_argument("input", type=Path, help="Binary to analyze.")
    agent.add_argument("--out", type=Path, required=True, help="Output JSONL for function facts.")
    agent.add_argument("--run-dir", type=Path, default=Path("target/agentdecompile"), help="Working directory for AgentDecompile cache and project state.")
    agent.add_argument("--limit", type=int, default=25, help="Maximum functions to discover or decompile per run.")
    agent.add_argument("--offset", type=int, default=0, help="Skip this many eligible seed candidates before decompiling.")
    agent.add_argument("--timeout", type=int, default=120, help="Per tool-seq timeout in seconds.")
    agent.add_argument("--batch-size", type=int, default=25, help="Seed batch size for decompile stage.")
    agent.add_argument("--seed-facts-jsonl", type=Path, help="Optional seed function facts JSONL to prioritize decompilation.")
    agent.add_argument("--no-auto-analysis-image", action="store_true", help="Do not attempt Steamless unpacking for PE inputs before AgentDecompile.")
    agent.add_argument("--steamless-cli", type=Path, help="Steamless CLI used to prepare packed PE analysis images when available.")
    agent.add_argument("--agentdecompile-server-url", help="Optional AgentDecompile MCP/CLI server URL.")
    agent.add_argument("--agentdecompile-mode", choices=["auto", "local"], default="local", help="AgentDecompile execution mode. local uses uvx plus PyGhidra in-process.")

    profile = sub.add_parser("compiler-profile-corpus", help="Select and sweep a compiler-profile corpus from verified matched examples.")
    profile.add_argument("--matched-examples", type=Path, default=Path("target/source-parity-index/swkotor/matched-examples.jsonl"))
    profile.add_argument("--out-dir", type=Path, default=Path("target/source-parity-profile/swkotor"))
    profile.add_argument("--max-cases", type=int, default=6)
    profile.add_argument("--select-only", "--dry-run", dest="select_only", action="store_true", help="Select corpus cases without compiling.")
    profile.add_argument("--profile", action="append", default=[], help="Compiler profile as NAME=VC_ROOT. Repeat for multiple toolchains.")
    profile.add_argument("--flag-set", action="append", default=[], help="Flag set as NAME='/O2 /Oy /GS-'. Repeat for custom matrix.")
    profile.add_argument("--wine", default="wine")
    profile.add_argument("--wineprefix", type=Path)
    profile.add_argument("--timeout", type=int, default=120)
    profile.add_argument("--clean", action="store_true")

    synth = sub.add_parser("source-parity-synthesize", help="Generate and objdiff-gate source candidates from a recovery queue.")
    synth.add_argument("--queue", type=Path, default=Path("target/swkotor-recovery-queue/queue.jsonl"))
    synth.add_argument("--inventory", type=Path, default=Path("target/swkotor-unpack/facts/function-inventory.jsonl"))
    synth.add_argument("--remaining-features", type=Path, default=Path("target/source-parity-index/swkotor/remaining-features.jsonl"))
    synth.add_argument("--retrieval", type=Path, default=Path("target/source-parity-index/swkotor/retrieval.jsonl"))
    synth.add_argument("--matched-summary", type=Path, action="append")
    synth.add_argument("--out-dir", type=Path, default=Path("target/source-parity-synthesis/swkotor"))
    synth.add_argument("--limit", type=int, default=25)
    synth.add_argument("--offset", type=int, default=0)
    synth.add_argument("--max-variants-per-function", type=int, default=8)
    synth.add_argument("--strategies")
    synth.add_argument("--compiler-profile", action="append", default=[], help="Compiler profile as NAME='/O2 /Oy /GS-'. Repeat for multiple profiles.")
    synth.add_argument("--dry-run", action="store_true")
    synth.add_argument("--clean", action="store_true")
    synth.add_argument("--vc-root", type=Path)
    synth.add_argument("--wine", default="wine")
    synth.add_argument("--wineprefix", type=Path)
    synth.add_argument("--timeout", type=int, default=120)
    synth.add_argument("--progress-every", type=int, default=0)
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
        context_binary_analysis=args.context_binary_analysis,
        context_max_files=args.context_max_files,
        context_max_depth=args.context_max_depth,
        context_strings_limit=args.context_strings_limit,
        context_max_index_text_chars=args.context_max_index_text_chars,
        context_extract_containers=not args.no_context_extract_containers,
        context_include_low_signal_members=args.context_include_low_signal_members,
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
        context_binary_analysis=args.context_binary_analysis,
        context_max_files=args.context_max_files,
        context_max_depth=args.context_max_depth,
        context_strings_limit=args.context_strings_limit,
        context_max_index_text_chars=args.context_max_index_text_chars,
        context_extract_containers=not args.no_context_extract_containers,
        context_include_low_signal_members=args.context_include_low_signal_members,
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
        source_parity_synthesis=args.source_parity_synthesis,
        source_parity_queue=args.source_parity_queue,
        source_parity_inventory=args.source_parity_inventory,
        source_parity_remaining_features=args.source_parity_remaining_features,
        source_parity_retrieval=args.source_parity_retrieval,
        source_parity_matched_summaries=args.source_parity_matched_summary,
        source_parity_out_dir=args.source_parity_out_dir,
        source_parity_limit=args.source_parity_limit,
        source_parity_offset=args.source_parity_offset,
        source_parity_max_variants_per_function=args.source_parity_max_variants_per_function,
        source_parity_strategies=args.source_parity_strategies,
        source_parity_dry_run=args.source_parity_dry_run,
        source_parity_clean=args.source_parity_clean,
        source_parity_vc_root=args.source_parity_vc_root,
        source_parity_wine=args.wine,
        source_parity_timeout=args.source_parity_timeout,
        source_parity_progress_every=args.source_parity_progress_every,
        source_parity_compiler_profiles=args.source_parity_compiler_profile,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary.get("status") == "complete" else 1


def run_export_context(args: argparse.Namespace) -> int:
    manifest = export_context(
        ExportConfig(
            input_path=args.input,
            out_dir=args.out_dir,
            output_format=args.format,
            binary_analysis=args.binary_analysis,
            extract_containers=not args.no_extract_containers,
            include_low_signal_members=args.include_low_signal_members,
            max_files=args.max_files,
            max_depth=args.max_depth,
            max_hash_bytes=args.max_hash_bytes,
            max_text_bytes=args.max_text_bytes,
            max_binary_analysis_bytes=args.max_binary_analysis_bytes,
            max_container_members=args.max_container_members,
            strings_limit=args.strings_limit,
            max_index_text_chars=args.max_index_text_chars,
        )
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


def run_export_context_batch(args: argparse.Namespace) -> int:
    argv = [
        str(args.input.resolve()),
        "--out-dir",
        str(args.out_dir.resolve()),
        "--format",
        args.format,
        "--binary-analysis",
        args.binary_analysis,
        "--max-items",
        str(args.max_items),
        "--min-size",
        str(args.min_size),
        "--item-mode",
        args.item_mode,
        "--max-files-per-item",
        str(args.max_files_per_item),
        "--max-depth",
        str(args.max_depth),
        "--max-hash-bytes",
        str(args.max_hash_bytes),
        "--max-text-bytes",
        str(args.max_text_bytes),
        "--max-binary-analysis-bytes",
        str(args.max_binary_analysis_bytes),
        "--max-container-members",
        str(args.max_container_members),
        "--strings-limit",
        str(args.strings_limit),
        "--max-index-text-chars",
        str(args.max_index_text_chars),
    ]
    if args.no_extract_containers:
        argv.append("--no-extract-containers")
    if args.include_low_signal_members:
        argv.append("--include-low-signal-members")
    for suffix in args.suffix:
        argv.extend(["--suffix", suffix])
    return context_batch_main(argv)


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


def run_agentdecompile(args: argparse.Namespace) -> int:
    input_path, prep_summary = prepare_agentdecompile_input(args)
    seed_facts_path = args.seed_facts_jsonl or default_agentdecompile_seed_facts(args.input, input_path)
    seed_rows: list[dict] = []
    if seed_facts_path and seed_facts_path.exists():
        for line in seed_facts_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                seed_rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    summary = run_agentdecompile_analysis(
        binary_path=input_path,
        out_path=args.out,
        run_dir=args.run_dir,
        limit=args.limit,
        timeout=args.timeout,
        offset=args.offset,
        candidate_functions=seed_rows,
        batch_size=args.batch_size,
        server_url=args.agentdecompile_server_url,
        mode=args.agentdecompile_mode,
    )
    summary["analysisInput"] = prep_summary
    if seed_facts_path:
        summary["seedFactsPath"] = str(seed_facts_path)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary.get("status") == "complete" else 1


def prepare_agentdecompile_input(args: argparse.Namespace) -> tuple[Path, dict[str, object]]:
    identity = identify_binary(args.input)
    summary: dict[str, object] = {
        "originalBinaryPath": str(identity.binary_path),
        "analysisBinaryPath": str(identity.binary_path),
        "status": "original",
        "transform": None,
        "claimBoundary": "analysis image is an acquisition input; it is not source parity proof",
    }
    if args.no_auto_analysis_image or identity.format != "pe":
        return identity.binary_path, summary

    steamless = resolve_steamless_cli(Path.cwd(), args.steamless_cli)
    mono = shutil.which("mono")
    if not steamless or not mono:
        summary.update(
            {
                "status": "original",
                "transformAttempted": "steamless-unpacked-pe",
                "transformResult": "missing-tool",
                "steamlessCli": str(steamless) if steamless else None,
                "mono": mono,
            }
        )
        return identity.binary_path, summary

    image_dir = (args.run_dir / "analysis-image").resolve()
    image_dir.mkdir(parents=True, exist_ok=True)
    original_copy = image_dir / identity.binary_path.name
    if not original_copy.exists() or original_copy.stat().st_size != identity.binary_path.stat().st_size:
        shutil.copy2(identity.binary_path, original_copy)
    unpacked = Path(str(original_copy) + ".unpacked.exe")
    proc: subprocess.CompletedProcess[str] | None = None
    if not unpacked.exists():
        proc = subprocess.run(
            ["mono", str(steamless), "--quiet", "--keepbind", "--dumppayload", "--dumpdrmp", str(original_copy)],
            text=True,
            capture_output=True,
            check=False,
            timeout=args.timeout,
        )
    if unpacked.exists():
        summary.update(
            {
                "analysisBinaryPath": str(unpacked),
                "status": "transformed",
                "transform": "steamless-unpacked-pe",
                "transformTool": str(steamless),
                "transformReturnCode": proc.returncode if proc is not None else None,
            }
        )
        return unpacked, summary
    summary.update(
        {
            "status": "original",
            "transformAttempted": "steamless-unpacked-pe",
            "transformResult": "not-produced",
            "transformTool": str(steamless),
            "transformReturnCode": proc.returncode if proc is not None else None,
            "transformStdout": proc.stdout[-4000:] if proc is not None else "",
            "transformStderr": proc.stderr[-4000:] if proc is not None else "",
        }
    )
    return identity.binary_path, summary


def default_agentdecompile_seed_facts(original_input: Path, analysis_input: Path) -> Path | None:
    """Use the checked-in SWKOTOR acquisition convention when the caller gives the Steam exe directly."""

    names = {original_input.name.lower(), analysis_input.name.lower()}
    if "swkotor.exe" not in names and "swkotor.original.exe.unpacked.exe" not in names:
        return None
    candidate = Path("target/swkotor-unpack/facts/function-inventory.jsonl")
    return candidate if candidate.exists() else None


def run_source_parity_synthesize(args: argparse.Namespace) -> int:
    argv = [
        "--queue",
        str(args.queue.resolve()),
        "--inventory",
        str(args.inventory.resolve()),
        "--remaining-features",
        str(args.remaining_features.resolve()),
        "--retrieval",
        str(args.retrieval.resolve()),
        "--out-dir",
        str(args.out_dir.resolve()),
        "--limit",
        str(args.limit),
        "--offset",
        str(args.offset),
        "--max-variants-per-function",
        str(args.max_variants_per_function),
        "--timeout",
        str(args.timeout),
        "--progress-every",
        str(args.progress_every),
    ]
    for matched_summary in args.matched_summary or []:
        argv.extend(["--matched-summary", str(matched_summary.resolve())])
    if args.strategies:
        argv.extend(["--strategies", args.strategies])
    for profile in args.compiler_profile:
        argv.extend(["--compiler-profile", profile])
    if args.dry_run:
        argv.append("--dry-run")
    if args.clean:
        argv.append("--clean")
    if args.vc_root:
        argv.extend(["--vc-root", str(args.vc_root.resolve())])
    if args.wine:
        argv.extend(["--wine", args.wine])
    if args.wineprefix:
        argv.extend(["--wineprefix", str(args.wineprefix.resolve())])
    return source_parity_synthesize_main(argv)


def run_compiler_profile_corpus(args: argparse.Namespace) -> int:
    argv = [
        "--matched-examples",
        str(args.matched_examples.resolve()),
        "--out-dir",
        str(args.out_dir.resolve()),
        "--max-cases",
        str(args.max_cases),
        "--timeout",
        str(args.timeout),
    ]
    if args.select_only:
        argv.append("--select-only")
    if args.clean:
        argv.append("--clean")
    if args.wine:
        argv.extend(["--wine", args.wine])
    if args.wineprefix:
        argv.extend(["--wineprefix", str(args.wineprefix.resolve())])
    for profile in args.profile:
        argv.extend(["--profile", profile])
    for flag_set in args.flag_set:
        argv.extend(["--flag-set", flag_set])
    return source_parity_profile_corpus_main(argv)


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
    if args.command == "export-context-batch":
        return run_export_context_batch(args)
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
    if args.command == "compiler-profile-corpus":
        return run_compiler_profile_corpus(args)
    if args.command == "source-parity-synthesize":
        return run_source_parity_synthesize(args)
    if args.command == "agentdecompile":
        return run_agentdecompile(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
