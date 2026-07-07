"""Installable one-shot CLI front door.

This module intentionally keeps the old subcommand CLI available while adding
the command shape users expect from an installable tool:

    mizuchi-cli path/to/binary

The default path runs the generic recovery orchestrator with byte-authority
packaging enabled and the upstream-style plugin synthesis engine selected.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .cli import main as legacy_main
from .pipeline import RecoveryConfig, RecoveryRunner
from .targets import identify_binary
from .tools import inspect_capabilities, resolve_script_asset


LEGACY_COMMANDS = {
    "inspect",
    "export-context",
    "export-context-batch",
    "recover",
    "recover-windows",
    "verify-package",
    "match-package",
    "sweep-package",
    "compiler-profile-corpus",
    "source-parity-synthesize",
    "source-plugin-pipeline",
}

UPSTREAM_COMMANDS = {"run", "atlas", "index-codebase"}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def run_decomp_cli_bridge(args: list[str]) -> int:
    root = repo_root()
    decomp = root / "scripts" / "decomp-cli.sh"
    if not decomp.exists():
        print(f"mizuchi-cli: missing workspace bridge {decomp}", file=sys.stderr)
        return 1
    proc = subprocess.run([str(decomp), *args], cwd=root)
    return int(proc.returncode or 0)


def run_upstream_command(command: str, argv: list[str]) -> int:
    if command == "run":
        bridge_args = ["vacuum", "start", "--queue", "state/queue.json", "--max-functions", "1"]
        if argv:
            bridge_args = ["vacuum", "start", *argv]
        return run_decomp_cli_bridge(bridge_args)
    if command == "atlas":
        if not argv:
            print("Usage: mizuchi-cli atlas <prompt-name>", file=sys.stderr)
            return 2
        return run_decomp_cli_bridge(["decomp-atlas", argv[0], *argv[1:]])
    if command == "index-codebase":
        return run_decomp_cli_bridge(["source-parity-feature-index", *argv])
    return run_upstream_command_guard(command)


def default_work_dir(target_path: Path, preferred_name: str | None = None) -> Path:
    identity = identify_binary(target_path, preferred_name)
    return Path("target/mizuchi-cli") / identity.stable_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mizuchi-cli",
        description="One-shot binary recovery/source-parity packaging front door.",
    )
    parser.add_argument("input", type=Path, help="Binary, archive, installer, or app directory to recover.")
    parser.add_argument("--preferred-name", help="Preferred executable basename when input is a folder.")
    parser.add_argument("--work-dir", type=Path, help="Run/state directory. Defaults to target/mizuchi-cli/<stable-target-id>.")
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reuse complete stage receipts with matching config.",
    )
    parser.add_argument("--force", action="store_true", help="Rerun selected stages even when receipts exist.")
    parser.add_argument(
        "--stop-after",
        choices=[
            "discover",
            "inspect-capabilities",
            "prepare-analysis-image",
            "export-context",
            "inventory-binary",
            "discover-functions",
            "analyze-functions",
            "generate-source-candidates",
            "synthesize-source-tasks",
            "plan-strategy",
            "byte-authority",
            "legacy-adapter",
            "snapshot-existing-recovery",
            "report",
        ],
        help="Stop after a named stage for bounded runs.",
    )
    parser.add_argument("--json", action="store_true", help="Emit progress as JSON lines.")
    parser.add_argument("--stage-timeout", type=int, default=300, help="Timeout per orchestration stage.")
    parser.add_argument("--progress-width", type=int, default=24)
    parser.add_argument("--no-byte-authority", action="store_true", help="Disable byte-exact source authority package generation.")
    parser.add_argument("--function-analysis", choices=["auto", "none", "objdump"], default="auto")
    parser.add_argument("--source-task-limit", type=int, default=500, help="Maximum function candidates to queue.")
    parser.add_argument("--source-task-offset", type=int, default=0, help="Skip this many eligible candidates before queueing.")
    parser.add_argument(
        "--source-synthesis",
        choices=["none", "dry-run", "clang", "clang-cl", "msvc"],
        default="clang",
        help="Compiler lane used for bounded generated source verification.",
    )
    parser.add_argument(
        "--source-synthesis-engine",
        choices=["plugin", "legacy"],
        default="plugin",
        help="plugin uses the upstream-style setup/programmatic/retry lifecycle.",
    )
    parser.add_argument("--source-synthesis-limit", type=int, default=50, help="Maximum source tasks to inspect in this invocation.")
    parser.add_argument("--source-synthesis-max-variants", type=int, default=8)
    parser.add_argument("--source-synthesis-strategies", help="Comma-separated strategy/tag/rule filter.")
    parser.add_argument(
        "--source-synthesis-source-quality",
        action="append",
        default=[],
        help="Only verify generated candidates with this source quality. Repeat or comma-separate.",
    )
    parser.add_argument("--source-synthesis-vc-root", type=Path, help="MSVC/VC Toolkit root used by source synthesis.")
    parser.add_argument("--source-synthesis-wine", default="wine", help="Wine executable used by MSVC source synthesis.")
    parser.add_argument("--source-synthesis-wineprefix", type=Path, help="Wine prefix used by MSVC source synthesis.")
    parser.add_argument("--steamless-cli", type=Path, help="Steamless CLI used to prepare PE analysis images when applicable.")
    parser.add_argument("--context-format", choices=["json", "md"], default="json")
    parser.add_argument("--context-binary-analysis", choices=["light", "standard", "deep"], default="standard")
    parser.add_argument("--context-max-files", type=int, default=1000)
    parser.add_argument("--context-max-depth", type=int, default=4)
    parser.add_argument("--context-strings-limit", type=int, default=500)
    parser.add_argument("--context-max-index-text-chars", type=int, default=2000)
    parser.add_argument("--no-context-extract-containers", action="store_true")
    parser.add_argument("--context-include-low-signal-members", action="store_true")
    return parser


def build_self_check_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mizuchi-cli self-check",
        description="Verify install-time Mizuchi assets and local recovery tool availability.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2], help="Checkout root used for script discovery.")
    return parser


def build_upstream_status_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mizuchi-cli upstream-status",
        description="Report how this package maps upstream macabeus/mizuchi surfaces.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser


def parse_csv_values(values: list[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                item.strip()
                for value in values
                for item in value.split(",")
                if item.strip()
            }
        )
    )


def parse_csv_string(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(sorted({item.strip() for item in value.split(",") if item.strip()}))


def run_one_shot(args: argparse.Namespace) -> int:
    if args.force:
        args.resume = False
    work_dir = args.work_dir or default_work_dir(args.input, args.preferred_name)
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
        enable_byte_authority=not args.no_byte_authority,
        enable_legacy_adapters=False,
        function_analysis=args.function_analysis,
        source_task_limit=args.source_task_limit,
        source_task_offset=args.source_task_offset,
        source_synthesis_engine=args.source_synthesis_engine,
        source_synthesis_mode=args.source_synthesis,
        source_synthesis_limit=args.source_synthesis_limit,
        source_synthesis_max_variants=args.source_synthesis_max_variants,
        source_synthesis_strategies=parse_csv_string(args.source_synthesis_strategies),
        source_synthesis_source_qualities=parse_csv_values(args.source_synthesis_source_quality),
        source_synthesis_vc_root=args.source_synthesis_vc_root,
        source_synthesis_wine=args.source_synthesis_wine,
        source_synthesis_wineprefix=args.source_synthesis_wineprefix,
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
    rc = RecoveryRunner(config).run()
    report_path = work_dir / "report.json"
    if report_path.exists() and not args.json:
        print(json.dumps({"status": "complete" if rc == 0 else "failed", "workDir": str(work_dir), "report": str(report_path)}, indent=2))
    return rc


def upstream_status() -> dict[str, Any]:
    return {
        "schema": "mizuchi.upstream-status.v1",
        "upstream": {
            "repository": "https://github.com/macabeus/mizuchi",
            "vendoredCommit": "218ecfe220ec9559ec914657f882b4e617cffe43",
            "commands": ["run", "atlas", "index-codebase"],
        },
        "mappedSurfaces": [
            {
                "upstreamSurface": "plugin lifecycle",
                "localSurface": "mizuchi_re.plugin_pipeline.PluginPipeline",
                "status": "ported",
            },
            {
                "upstreamSurface": "programmatic source/objdiff phase",
                "localSurface": "mizuchi_re.source_plugin_runner.run_source_plugin_pipeline",
                "status": "ported-for-source-slices",
            },
            {
                "upstreamSurface": "installable CLI front door",
                "localSurface": "mizuchi-cli <binary-or-folder>",
                "status": "adapted-for-binary-recovery",
            },
            {
                "upstreamSurface": "JSON reports",
                "localSurface": "target/mizuchi-cli/<target-id>/report.json and stage receipts",
                "status": "ported-for-binary-recovery",
            },
            {
                "upstreamSurface": "run",
                "localSurface": "scripts/decomp-cli.sh vacuum start (prompt-folder matching loop)",
                "status": "bridged",
            },
            {
                "upstreamSurface": "atlas",
                "localSurface": "scripts/decomp-cli.sh decomp-atlas",
                "status": "bridged",
            },
            {
                "upstreamSurface": "index-codebase",
                "localSurface": "scripts/decomp-cli.sh source-parity-feature-index",
                "status": "bridged",
            },
        ],
        "unmappedSurfaces": [
            {
                "upstreamSurface": "Claude runner",
                "reason": "default installable path uses deterministic source generation plus compiler/object gates, not Claude SDK calls",
            },
        ],
        "claimBoundary": "This reports CLI/core surface coverage only. It is not a semantic source recovery claim.",
    }


def run_upstream_status(args: argparse.Namespace) -> int:
    status = upstream_status()
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0
    print("Upstream macabeus/mizuchi surface mapping")
    print(f"- Vendored commit: {status['upstream']['vendoredCommit']}")
    for row in status["mappedSurfaces"]:
        print(f"- mapped: {row['upstreamSurface']} -> {row['localSurface']} ({row['status']})")
    for row in status["unmappedSurfaces"]:
        print(f"- not packaged: {row['upstreamSurface']} - {row['reason']}")
    print(f"- boundary: {status['claimBoundary']}")
    return 0


def run_self_check(args: argparse.Namespace) -> int:
    repo_root = args.repo_root.resolve()
    capabilities = inspect_capabilities(repo_root)
    required_scripts = [
        "one-shot-source.py",
        "binary-source-roundtrip.py",
        "source-authority-report.py",
        "one-shot-source-proof.py",
        "one-shot-source-archive-verify.py",
        "one-shot-source-deliverable-verify.py",
        "one-shot-source-claims.py",
        "one-shot-source-validate.py",
        "one-shot-source-verify.py",
    ]
    scripts = {
        name: {
            "available": (path := resolve_script_asset(repo_root, name)) is not None,
            "path": str(path) if path else None,
        }
        for name in required_scripts
    }
    ok = all(item["available"] for item in scripts.values())
    report = {
        "schema": "mizuchi.install-self-check.v1",
        "status": "ok" if ok else "missing-assets",
        "repoRoot": str(repo_root),
        "scriptAssets": scripts,
        "capabilities": capabilities,
        "upstreamStatus": upstream_status(),
        "claimBoundary": "Self-check verifies packaging and local tool discovery only; it does not run source recovery.",
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Status: {report['status']}")
        print(f"Repo root: {repo_root}")
        for name, item in scripts.items():
            marker = "ok" if item["available"] else "missing"
            print(f"- {marker}: {name} {item['path'] or ''}".rstrip())
        print(f"Boundary: {report['claimBoundary']}")
    return 0 if ok else 1


def run_upstream_command_guard(command: str) -> int:
    print(
        "\n".join(
            [
                f"mizuchi-cli: upstream command '{command}' is not packaged in this Python front door.",
                "Use `mizuchi-cli upstream-status` for the exact surface mapping.",
                "For direct binary recovery use: `mizuchi-cli <path/to/binary-or-folder>`.",
                "The vendored upstream TypeScript implementation remains under `vendor/upstream-mizuchi/` in this checkout.",
            ]
        ),
        file=sys.stderr,
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] == "self-check":
        return run_self_check(build_self_check_parser().parse_args(args[1:]))
    if args and args[0] == "upstream-status":
        return run_upstream_status(build_upstream_status_parser().parse_args(args[1:]))
    if args and args[0] in UPSTREAM_COMMANDS:
        return run_upstream_command(args[0], args[1:])
    if args and args[0] in LEGACY_COMMANDS:
        return legacy_main(args)
    parser = build_parser()
    return run_one_shot(parser.parse_args(args))


if __name__ == "__main__":
    raise SystemExit(main())
