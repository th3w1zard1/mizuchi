#!/usr/bin/env python3
"""Remove transient verifier outputs from a one-shot source package."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


TRANSIENT_FILES = [
    "verify-standalone-asm.o",
    "verify-standalone-asm.bin",
    "verify-standalone-c-emitter",
    "verify-standalone-c.bin",
    "candidate-source-emitter",
    "candidate-source-output.bin",
    "verify-candidate-source-emitter",
    "verify-candidate-source.bin",
]

TRANSIENT_DIRS = [
    ".ccache",
]

REQUIRED_PACKAGE_FILES = [
    "AUTHORITY_GATES.json",
    "CLAIMS.json",
    "CONTENT_MANIFEST.json",
    "SOURCE_INDEX.json",
    "VERIFIED_SOURCE_CANDIDATES.json",
    "VERIFY.py",
    "VERIFY.sh",
    "full-binary.S",
    "full-binary.c",
    "original.bin",
    "package-manifest.json",
]


def clean_package(package: Path, dry_run: bool) -> dict:
    root = package.resolve()
    if not root.is_dir():
        raise SystemExit(f"not a package directory: {package}")

    missing = [name for name in REQUIRED_PACKAGE_FILES if not (root / name).exists()]
    if missing:
        raise SystemExit(f"not a complete one-shot source package; missing: {', '.join(missing)}")

    removed: list[str] = []
    absent: list[str] = []

    for name in TRANSIENT_FILES:
        path = root / name
        if path.exists() or path.is_symlink():
            removed.append(name)
            if not dry_run:
                path.unlink()
        else:
            absent.append(name)

    for name in TRANSIENT_DIRS:
        path = root / name
        if path.exists() or path.is_symlink():
            removed.append(name)
            if not dry_run:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
        else:
            absent.append(name)

    return {
        "schema": "reconkit.one-shot-source-clean.v1",
        "package": str(root),
        "dryRun": dry_run,
        "removed": removed,
        "absent": absent,
        "preservedAuthorityFiles": REQUIRED_PACKAGE_FILES,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True, help="One-shot source package directory.")
    parser.add_argument("--dry-run", action="store_true", help="Report removable files without deleting them.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args()

    result = clean_package(args.package, args.dry_run)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        verb = "Would remove" if args.dry_run else "Removed"
        if result["removed"]:
            print(f"{verb}:")
            for name in result["removed"]:
                print(f"  {name}")
        else:
            print("No transient verifier artifacts found.")
        print("ONE_SHOT_SOURCE_CLEAN_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
