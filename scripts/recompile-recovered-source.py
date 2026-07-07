#!/usr/bin/env python3
"""Replay-compile exported recovered-source units from build_manifest.json."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from recovery_runtime.package_verify import compile_with_msvc  # noqa: E402
from recovery_runtime.source_parity_synthesize import compile_with_clang, compile_with_clangcl, object_text_bytes  # noqa: E402


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generic_object_text_bytes(obj: Path, *, objdump: str, timeout: int) -> bytes:
    if not obj.exists():
        return b""
    proc = subprocess.run(
        [objdump, "-d", str(obj)],
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    if proc.returncode != 0:
        return b""
    values: list[int] = []
    for line in proc.stdout.splitlines():
        if ":" not in line or "\t" not in line:
            continue
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        for token in fields[1].split():
            if re.fullmatch(r"[0-9a-fA-F]{2}", token):
                values.append(int(token, 16))
    return bytes(values)


def replay_text_bytes(obj: Path, *, compiler: str, objdump: str, timeout: int) -> bytes:
    if compiler == "msvc":
        return object_text_bytes(obj, timeout=timeout)
    return generic_object_text_bytes(obj, objdump=objdump, timeout=timeout)


def compile_unit(
    unit: dict[str, Any],
    *,
    source: Path,
    object_path: Path,
    out_dir: Path,
    stem: str,
    vc_root: Path | None,
    wine: str,
    wineprefix: Path | None,
    clang: str,
    clang_cl: str,
    timeout: int,
) -> dict[str, Any]:
    compiler = str(unit.get("compiler") or "msvc")
    args = [str(arg) for arg in unit.get("compilerProfileArgs") or []]
    if compiler == "clang":
        return compile_with_clang(clang=clang, source=source, object_path=object_path, args=args, timeout=timeout)
    if compiler == "clang-cl":
        return compile_with_clangcl(clang_cl=clang_cl, source=source, object_path=object_path, args=args, timeout=timeout)
    return compile_with_msvc(
        source=source,
        object_path=object_path,
        out_dir=out_dir,
        stem=stem,
        args=args,
        timeout=timeout,
        msvc_root=vc_root,
        wine=wine,
        wineprefix=wineprefix,
    )


def replay_unit(
    unit: dict[str, Any],
    *,
    out_dir: Path,
    vc_root: Path | None,
    wine: str,
    wineprefix: Path | None,
    clang: str,
    clang_cl: str,
    objdump: str,
    timeout: int,
) -> dict[str, Any]:
    source = Path(str(unit["source"]))
    stem = f"{unit.get('entry', 'unknown')}_{unit.get('name', 'function')}"
    stem = "".join(ch if ch.isalnum() or ch in "._@+-" else "_" for ch in stem)[:160]
    object_path = out_dir / f"{stem}.obj"
    compiler = str(unit.get("compiler") or "msvc")
    compile_result = compile_unit(
        unit,
        source=source,
        object_path=object_path,
        out_dir=out_dir,
        stem=stem,
        vc_root=vc_root,
        wine=wine,
        wineprefix=wineprefix,
        clang=clang,
        clang_cl=clang_cl,
        timeout=timeout,
    )
    verified_object = Path(str(unit.get("verifiedObject") or ""))
    text_match = None
    if object_path.is_file() and verified_object.is_file():
        text_match = replay_text_bytes(object_path, compiler=compiler, objdump=objdump, timeout=timeout) == replay_text_bytes(
            verified_object,
            compiler=compiler,
            objdump=objdump,
            timeout=timeout,
        )
    return {
        "name": unit.get("name"),
        "entry": unit.get("entry"),
        "source": str(source),
        "language": unit.get("language"),
        "compiler": compiler,
        "sourceQuality": unit.get("sourceQuality"),
        "compileStatus": compile_result.get("status"),
        "compiledObject": str(object_path) if object_path.is_file() else None,
        "compiledObjectSha256": sha256(object_path),
        "verifiedObject": str(verified_object) if verified_object.is_file() else None,
        "verifiedObjectSha256": sha256(verified_object),
        "textBytesMatchVerifiedObject": text_match,
        "compilerReturnCode": compile_result.get("returnCode"),
        "stderrTail": compile_result.get("stderrTail"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="recovered-source/build_manifest.json")
    parser.add_argument("--out-dir", type=Path, required=True, help="Replay object output directory")
    parser.add_argument("--vc-root", type=Path, default=None, help="MSVC root containing bin/cl.exe and bin/ml.exe")
    parser.add_argument("--wine", default="wine", help="Wine executable")
    parser.add_argument("--wineprefix", type=Path, default=None, help="Wine prefix for MSVC")
    parser.add_argument("--clang", default="clang", help="clang executable for clang-recorded units")
    parser.add_argument("--clang-cl", default="clang-cl", help="clang-cl executable for clang-cl-recorded units")
    parser.add_argument("--objdump", default="objdump", help="objdump executable for non-MSVC .text extraction")
    parser.add_argument("--limit", type=int, default=0, help="Maximum units to replay; 0 means all")
    parser.add_argument("--timeout", type=int, default=120, help="Per-unit compile/objdump timeout")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    units = list(manifest.get("units") or [])
    if args.limit > 0:
        units = units[: args.limit]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    results = [
        replay_unit(
            unit,
            out_dir=args.out_dir,
            vc_root=args.vc_root,
            wine=args.wine,
            wineprefix=args.wineprefix,
            clang=args.clang,
            clang_cl=args.clang_cl,
            objdump=args.objdump,
            timeout=args.timeout,
        )
        for unit in units
    ]
    failed = [row for row in results if row.get("compileStatus") != "ok"]
    text_mismatches = [row for row in results if row.get("textBytesMatchVerifiedObject") is not True]
    report = {
        "schema": "reconkit.recovered-source-recompile-report.v1",
        "status": "ok" if not failed and not text_mismatches else "failed",
        "manifest": str(args.manifest),
        "outDir": str(args.out_dir),
        "unitCount": len(results),
        "compiledUnits": sum(1 for row in results if row.get("compileStatus") == "ok"),
        "textMatchedVerifiedObjectUnits": sum(1 for row in results if row.get("textBytesMatchVerifiedObject") is True),
        "failedUnits": len(failed),
        "textMismatchUnits": len(text_mismatches),
        "claimBoundary": "Replay proves exported units still compile and reproduce verified object .text bytes; it is not a whole-executable link proof.",
        "results": results,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
