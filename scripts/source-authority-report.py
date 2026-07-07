#!/usr/bin/env python3
"""Summarize what a generated source artifact is authoritative for."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise SystemExit(f"missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"unexpected JSON root in {path}")
    return data


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def artifact_row(item: dict[str, Any]) -> dict[str, Any]:
    semantic = item.get("semanticDecompilation")
    source_type = item.get("sourceType")
    strategy = item.get("strategy")
    byte_identical = item.get("byteIdentical") is True
    if semantic is True:
        authority = "semantic-source-slice" if byte_identical else "unverified-semantic-source"
    elif source_type == "byte-source" or semantic is False or strategy in {"byte-source-incbin", "byte-source-incbin-batch"}:
        authority = "byte-source" if byte_identical else "unverified-byte-source"
    elif byte_identical:
        authority = "byte-identical-ambiguous-source"
    else:
        authority = "unverified"

    return {
        "kind": item.get("kind") or item.get("schema") or "artifact",
        "source": item.get("source"),
        "binary": item.get("binary"),
        "relativePath": item.get("relativePath"),
        "status": item.get("status"),
        "byteIdentical": byte_identical,
        "authority": authority,
        "sourceType": source_type,
        "sourceAuthority": item.get("sourceAuthority"),
        "semanticDecompilation": semantic,
        "strategy": strategy,
        "scopeNote": item.get("scopeNote"),
    }


def manifest_report(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    source_bundles = [item for item in as_list(data.get("sourceBundles")) if isinstance(item, dict)]
    byte_sources = [item for item in as_list(data.get("fullBinaryRoundtrips")) if isinstance(item, dict)]
    rebuilt = [item for item in as_list(data.get("rebuiltBinaries")) if isinstance(item, dict)]

    semantic_rows: list[dict[str, Any]] = []
    for item in source_bundles:
        semantic_rows.append(
            {
                "kind": item.get("kind"),
                "source": item.get("source"),
                "binary": item.get("binary"),
                "byteIdentical": item.get("byteIdentical") is True,
                "matchedSymbols": int(item.get("matchedSymbols") or 0),
                "authority": "semantic-source-slice" if item.get("byteIdentical") is True else "unverified-semantic-source",
                "scopeNote": "Authoritative only for the matched function/export slices named in the verifier reports.",
            }
        )

    byte_rows = [artifact_row(item) for item in byte_sources]
    rebuilt_rows = [
        {
            "kind": item.get("kind"),
            "binary": item.get("binary"),
            "rebuiltDll": item.get("rebuiltDll"),
            "status": item.get("status"),
            "byteIdenticalExports": item.get("byteIdenticalExports") is True,
            "matchedSymbols": int(item.get("matchedSymbols") or 0),
            "expectedSymbols": int(item.get("expectedSymbols") or 0),
            "authority": "rebuilt-binary-export-slices"
            if item.get("byteIdenticalExports") is True
            else "unverified-rebuilt-binary",
            "scopeNote": item.get("scopeNote"),
        }
        for item in rebuilt
    ]

    semantic_verified = sum(1 for item in semantic_rows if item["byteIdentical"])
    byte_verified = sum(1 for item in byte_rows if item["byteIdentical"])
    rebuilt_verified = sum(1 for item in rebuilt_rows if item["byteIdenticalExports"])
    full_app = data.get("fullAppByteIdentical") is True
    primary = data.get("primaryBinaryByteIdentical") is True
    ambiguous = [
        item
        for item in byte_rows
        if item["authority"] == "byte-identical-ambiguous-source" or item["authority"].startswith("unverified")
    ]

    return {
        "schema": "mizuchi.source-authority-report.v1",
        "input": str(path),
        "inputSchema": data.get("schema"),
        "app": data.get("app"),
        "appid": data.get("appid"),
        "status": "authoritative" if not ambiguous and (semantic_verified or byte_verified or rebuilt_verified) else "incomplete",
        "fullAppByteIdentical": full_app,
        "primaryBinaryByteIdentical": primary,
        "semanticSourceBundles": len(semantic_rows),
        "semanticSourceBundlesVerified": semantic_verified,
        "byteSourceArtifacts": len(byte_rows),
        "byteSourceArtifactsVerified": byte_verified,
        "rebuiltBinaryArtifacts": len(rebuilt_rows),
        "rebuiltBinaryArtifactsVerified": rebuilt_verified,
        "semanticRows": semantic_rows,
        "byteSourceRows": byte_rows,
        "rebuiltBinaryRows": rebuilt_rows,
        "ambiguousOrUnverifiedRows": ambiguous,
        "claimBoundary": (
            "Byte-source artifacts are authoritative for exact bytes only. Semantic source bundles are authoritative only for "
            "verified matched function/export slices. fullAppByteIdentical does not imply full semantic decompilation."
        ),
    }


def binary_roundtrip_report(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    row = artifact_row(data)
    return {
        "schema": "mizuchi.source-authority-report.v1",
        "input": str(path),
        "inputSchema": data.get("schema"),
        "status": "authoritative" if row["byteIdentical"] and row["authority"] == "byte-source" else "incomplete",
        "fullAppByteIdentical": False,
        "primaryBinaryByteIdentical": row["byteIdentical"],
        "semanticSourceBundles": 0,
        "semanticSourceBundlesVerified": 0,
        "byteSourceArtifacts": 1,
        "byteSourceArtifactsVerified": 1 if row["byteIdentical"] else 0,
        "rebuiltBinaryArtifacts": 0,
        "rebuiltBinaryArtifactsVerified": 0,
        "semanticRows": [],
        "byteSourceRows": [row],
        "rebuiltBinaryRows": [],
        "ambiguousOrUnverifiedRows": [] if row["byteIdentical"] and row["authority"] == "byte-source" else [row],
        "claimBoundary": "This artifact is authoritative for exact whole-file bytes only, not semantic decompilation.",
    }


def recovered_source_shard_report(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    functions = [item for item in as_list(data.get("functions")) if isinstance(item, dict)]
    rows = [
        {
            "kind": "function-source-slice",
            "name": item.get("name"),
            "entry": item.get("entry"),
            "source": item.get("exportedSource") or item.get("source"),
            "originalSource": item.get("source"),
            "status": item.get("status"),
            "byteIdentical": item.get("differences") == 0 or item.get("status") == "source-shape-code-slice-matched",
            "authority": item.get("authority") or "semantic-source-slice",
            "sourceQuality": item.get("sourceQuality"),
            "sourceRecoveryScope": item.get("sourceRecoveryScope"),
            "verificationTier": item.get("verificationTier"),
            "scopeNote": item.get("claimBoundary"),
        }
        for item in functions
    ]
    verified = sum(1 for item in rows if item["byteIdentical"])
    ambiguous = [item for item in rows if not item["byteIdentical"] or not str(item.get("authority") or "").startswith("semantic-")]
    return {
        "schema": "mizuchi.source-authority-report.v1",
        "input": str(path),
        "inputSchema": data.get("schema"),
        "status": "authoritative" if rows and not ambiguous else "incomplete",
        "fullAppByteIdentical": False,
        "primaryBinaryByteIdentical": False,
        "semanticSourceBundles": len(rows),
        "semanticSourceBundlesVerified": verified,
        "byteSourceArtifacts": 0,
        "byteSourceArtifactsVerified": 0,
        "rebuiltBinaryArtifacts": 0,
        "rebuiltBinaryArtifactsVerified": 0,
        "semanticRows": rows,
        "byteSourceRows": [],
        "rebuiltBinaryRows": [],
        "ambiguousOrUnverifiedRows": ambiguous,
        "claimBoundary": data.get("claimBoundary")
        or "Recovered source shard authority is limited to verified named function/source slices.",
    }


def build_report(path: Path) -> dict[str, Any]:
    data = read_json(path)
    schema = str(data.get("schema") or "")
    if schema == "mizuchi.app-source-roundtrip-manifest.v1":
        return manifest_report(path, data)
    if schema == "mizuchi.binary-source-roundtrip.v1":
        return binary_roundtrip_report(path, data)
    if schema == "mizuchi.swkotor-recovered-source-shard.v1":
        return recovered_source_shard_report(path, data)
    raise SystemExit(f"unsupported source artifact schema: {schema or '<missing>'}")


def print_markdown(report: dict[str, Any]) -> None:
    print("# Source Authority Report")
    print()
    print(f"Input: `{report['input']}`")
    print(f"Status: `{report['status']}`")
    print(f"Full app byte-identical: `{str(report['fullAppByteIdentical']).lower()}`")
    print(f"Primary binary byte-identical: `{str(report['primaryBinaryByteIdentical']).lower()}`")
    print()
    print("| Surface | Verified | Total |")
    print("| --- | ---: | ---: |")
    print(f"| Semantic source bundles | {report['semanticSourceBundlesVerified']} | {report['semanticSourceBundles']} |")
    print(f"| Byte-source artifacts | {report['byteSourceArtifactsVerified']} | {report['byteSourceArtifacts']} |")
    print(f"| Rebuilt binary artifacts | {report['rebuiltBinaryArtifactsVerified']} | {report['rebuiltBinaryArtifacts']} |")
    print()
    print(report["claimBoundary"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="Write JSON authority report to this path")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown")
    args = parser.parse_args()

    report = build_report(args.input)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_markdown(report)
    return 0 if report["status"] == "authoritative" else 1


if __name__ == "__main__":
    raise SystemExit(main())
