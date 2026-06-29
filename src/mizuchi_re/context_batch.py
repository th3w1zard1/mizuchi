"""Batch export app/install/archive trees into LLM-readable context packages."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from .context_export import ARCHIVE_SUFFIXES, BINARY_ANALYSIS_SUFFIXES, ExportConfig, export_context
from .state import atomic_write_json, now


DEFAULT_SUFFIXES = sorted(ARCHIVE_SUFFIXES | BINARY_ANALYSIS_SUFFIXES)


def safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.@+-]+", "_", value.strip())
    return cleaned[:180] or "item"


def find_inputs(root: Path, suffixes: set[str], max_items: int, *, min_size: int = 0) -> list[Path]:
    root = root.expanduser().resolve()
    if root.is_file():
        return [root]
    if not root.is_dir():
        raise FileNotFoundError(f"input root does not exist: {root}")
    matches: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in suffixes:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size < min_size:
            continue
        matches.append(path)
        if len(matches) >= max_items:
            break
    return matches


def item_output_dir(out_dir: Path, root: Path, item: Path) -> Path:
    try:
        rel = item.resolve().relative_to(root.resolve() if root.is_dir() else root.resolve().parent)
    except ValueError:
        rel = Path(item.name)
    parts = [safe_component(part) for part in rel.parts]
    if parts:
        parts[-1] = safe_component(parts[-1])
    return out_dir / "items" / Path(*parts)


def export_context_batch(
    *,
    input_path: Path,
    out_dir: Path,
    output_format: str,
    binary_analysis: str,
    extract_containers: bool,
    include_low_signal_members: bool,
    max_items: int,
    min_size: int,
    suffixes: set[str],
    max_files_per_item: int,
    max_depth: int,
    max_hash_bytes: int,
    max_text_bytes: int,
    max_binary_analysis_bytes: int,
    max_container_members: int,
    strings_limit: int,
) -> dict[str, Any]:
    root = input_path.expanduser().resolve()
    out_dir = out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    inputs = find_inputs(root, suffixes, max_items, min_size=min_size)
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(inputs, start=1):
        item_out = item_output_dir(out_dir, root, item)
        manifest = export_context(
            ExportConfig(
                input_path=item,
                out_dir=item_out,
                output_format=output_format,
                binary_analysis=binary_analysis,
                extract_containers=extract_containers,
                include_low_signal_members=include_low_signal_members,
                max_files=max_files_per_item,
                max_depth=max_depth,
                max_hash_bytes=max_hash_bytes,
                max_text_bytes=max_text_bytes,
                max_binary_analysis_bytes=max_binary_analysis_bytes,
                max_container_members=max_container_members,
                strings_limit=strings_limit,
            )
        )
        try:
            rel = item.relative_to(root if root.is_dir() else root.parent)
        except ValueError:
            rel = Path(item.name)
        rows.append(
            {
                "index": index,
                "path": str(rel),
                "sourcePath": str(item),
                "outputDirectory": str(item_out),
                "manifest": str(item_out / "manifest.json"),
                "treeMarkdown": str(item_out / "TREE.md"),
                "filesVisited": manifest.get("filesVisited"),
                "filesExported": manifest.get("filesExported"),
                "truncated": manifest.get("truncated"),
                "kind": (manifest.get("entries") or [{}])[0].get("kind") if isinstance(manifest.get("entries"), list) else None,
            }
        )
    report = {
        "schema": "mizuchi.context-batch-export.v1",
        "createdAt": now(),
        "inputPath": str(root),
        "outputDirectory": str(out_dir),
        "outputFormat": output_format,
        "binaryAnalysis": binary_analysis,
        "extractContainers": extract_containers,
        "suffixes": sorted(suffixes),
        "limits": {
            "maxItems": max_items,
            "minSize": min_size,
            "maxFilesPerItem": max_files_per_item,
            "maxDepth": max_depth,
            "maxHashBytes": max_hash_bytes,
            "maxTextBytes": max_text_bytes,
            "maxBinaryAnalysisBytes": max_binary_analysis_bytes,
            "maxContainerMembers": max_container_members,
            "stringsLimit": strings_limit,
        },
        "itemsDiscovered": len(inputs),
        "itemsExported": len(rows),
        "truncated": len(inputs) >= max_items,
        "items": rows,
        "claimBoundary": "batch context export emits LLM-readable surrogates and extraction manifests; it is not source-parity decompilation proof",
    }
    atomic_write_json(out_dir / "manifest.json", report)
    (out_dir / "TREE.md").write_text(render_batch_tree(report), encoding="utf-8")
    return report


def render_batch_tree(report: dict[str, Any]) -> str:
    lines = [
        f"# Context Batch Export",
        "",
        f"- Input: `{report.get('inputPath')}`",
        f"- Items exported: `{report.get('itemsExported')}`",
        f"- Format: `{report.get('outputFormat')}`",
        "",
        "## Items",
    ]
    for item in report.get("items", []):
        lines.append(f"- `{item.get('path')}` -> `{item.get('manifest')}`")
    lines.append("")
    lines.append(f"Claim boundary: {report.get('claimBoundary')}")
    lines.append("")
    return "\n".join(lines)


def parse_suffixes(values: list[str]) -> set[str]:
    if not values:
        return set(DEFAULT_SUFFIXES)
    suffixes: set[str] = set()
    for value in values:
        for part in value.split(","):
            suffix = part.strip().lower()
            if not suffix:
                continue
            if not suffix.startswith("."):
                suffix = "." + suffix
            suffixes.add(suffix)
    return suffixes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("input", type=Path, help="File or directory tree to batch-export.")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--format", choices=["json", "md"], default="json")
    parser.add_argument("--binary-analysis", choices=["light", "standard", "deep"], default="standard")
    parser.add_argument("--no-extract-containers", action="store_true")
    parser.add_argument("--include-low-signal-members", action="store_true")
    parser.add_argument("--max-items", type=int, default=25)
    parser.add_argument("--min-size", type=int, default=0)
    parser.add_argument("--suffix", action="append", default=[], help="Suffix or comma-separated suffix list. Defaults to EXE/archive/binary suffixes.")
    parser.add_argument("--max-files-per-item", type=int, default=250)
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--max-hash-bytes", type=int, default=512_000_000)
    parser.add_argument("--max-text-bytes", type=int, default=2_000_000)
    parser.add_argument("--max-binary-analysis-bytes", type=int, default=256_000_000)
    parser.add_argument("--max-container-members", type=int, default=120)
    parser.add_argument("--strings-limit", type=int, default=200)
    args = parser.parse_args(argv)

    report = export_context_batch(
        input_path=args.input,
        out_dir=args.out_dir,
        output_format=args.format,
        binary_analysis=args.binary_analysis,
        extract_containers=not args.no_extract_containers,
        include_low_signal_members=args.include_low_signal_members,
        max_items=args.max_items,
        min_size=args.min_size,
        suffixes=parse_suffixes(args.suffix),
        max_files_per_item=args.max_files_per_item,
        max_depth=args.max_depth,
        max_hash_bytes=args.max_hash_bytes,
        max_text_bytes=args.max_text_bytes,
        max_binary_analysis_bytes=args.max_binary_analysis_bytes,
        max_container_members=args.max_container_members,
        strings_limit=args.strings_limit,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
