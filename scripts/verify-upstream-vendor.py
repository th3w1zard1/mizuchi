#!/usr/bin/env python3
"""Verify a vendored upstream Mizuchi tree against its audit manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"missing manifest: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid manifest {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"manifest root must be an object: {path}")
    return data


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("docs/upstream-audit/macabeus-mizuchi-main-218ecfe-vendor-manifest.json"),
    )
    args = parser.parse_args()

    manifest = read_json(args.manifest)
    files = manifest.get("files")
    if not isinstance(files, list):
        raise SystemExit("manifest files must be a list")

    missing = []
    changed = []
    for item in files:
        if not isinstance(item, dict):
            continue
        path = Path(str(item.get("vendoredPath") or ""))
        if not path.is_file():
            missing.append(str(path))
            continue
        expected = item.get("sha256")
        actual = sha256_file(path)
        if expected and actual != expected:
            changed.append({"path": str(path), "expected": expected, "actual": actual})

    report = {
        "schema": "mizuchi.upstream-vendor-verification.v1",
        "manifest": str(args.manifest),
        "upstream": manifest.get("upstream"),
        "upstreamCommit": manifest.get("upstreamCommit"),
        "checkedFiles": len(files),
        "missingFiles": missing,
        "changedFiles": changed,
        "status": "verified" if not missing and not changed else "failed",
        "claimBoundary": "Verifies vendored upstream blob files against the recorded manifest. Upstream submodule gitlinks are recorded but not expanded.",
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
