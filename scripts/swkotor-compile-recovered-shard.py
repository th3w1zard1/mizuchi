#!/usr/bin/env python3
"""Compile exported recovered source files with MSVC, or copy preverified objects."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VC_ROOT = Path("/run/media/brunner56/MyBook/ReconstructKitSource/toolchains/msvc8.0-main")
DEFAULT_WINEPREFIX = ROOT / "target/toolchain-acquire/vctoolkit2003/wineprefix"


def run(args: list[str], *, env: dict[str, str], timeout: int = 240) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, cwd=ROOT, env=env, text=True, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return subprocess.CompletedProcess(args, 124, stdout, f"{stderr}\ntimed out after {timeout} seconds".strip())


def load_build_units(manifest: dict) -> dict[tuple[str, str], dict]:
    manifest_path = manifest.get("buildManifest")
    if not manifest_path:
        return {}
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    units: dict[tuple[str, str], dict] = {}
    for unit in data.get("units") or []:
        key = (str(unit.get("name")), str(unit.get("entry")))
        units[key] = unit
    return units


def prompt_name_from_source(source: Path) -> str | None:
    parts = source.parts
    if "prompts" not in parts:
        return None
    idx = parts.index("prompts")
    if idx + 1 >= len(parts):
        return None
    return parts[idx + 1]


def resolve_preverified_object(fn: dict, unit: dict | None) -> Path | None:
    candidates: list[Path] = []
    if unit:
        verified = unit.get("verifiedObject")
        if verified:
            candidates.append(Path(str(verified)))
        original = unit.get("originalSource")
        if original:
            candidates.append(Path(str(original)).parent / "build" / "candidate.bin")
            candidates.append(Path(str(original)).parent / "candidate.bin")
    prompt = fn.get("prompt")
    if prompt:
        candidates.append(ROOT / "prompts" / str(prompt) / "build" / "candidate.bin")
    for source_key in ("source", "exportedSource"):
        raw = fn.get(source_key)
        if not raw:
            continue
        source = Path(str(raw))
        prompt_name = prompt_name_from_source(source)
        if prompt_name:
            candidates.append(ROOT / "prompts" / prompt_name / "build" / "candidate.bin")
        candidates.append(source.parent / "build" / "candidate.bin")
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def compile_args_for_unit(unit: dict | None, default_args: list[str]) -> list[str]:
    if not unit:
        return default_args
    profile_args = unit.get("compilerProfileArgs")
    if isinstance(profile_args, list) and profile_args:
        return [str(arg) for arg in profile_args]
    return default_args


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "target/swkotor-recovered/simple_matches.manifest.json")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "target/swkotor-recovered/objects")
    parser.add_argument("--summary", type=Path, default=ROOT / "target/swkotor-recovered/compile-summary.json")
    parser.add_argument("--vc-root", type=Path, default=DEFAULT_VC_ROOT)
    parser.add_argument("--wineprefix", type=Path, default=DEFAULT_WINEPREFIX)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--progress-every", type=int, default=0, help="Print compile progress to stderr every N source files.")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    build_units = load_build_units(manifest)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update({"VC_ROOT": str(args.vc_root), "WINEPREFIX": str(args.wineprefix), "CL_OPT": "/O2"})
    default_cl_args = ["/GS-", "/Oy"]
    rows = []
    preverified_copies = 0
    for index, fn in enumerate(manifest["functions"]):
        if args.limit is not None and index >= args.limit:
            break
        source = Path(fn["exportedSource"])
        object_path = args.out_dir / f"{fn.get('entry', 'unknown')}_{fn['name']}.obj"
        unit = build_units.get((str(fn.get("name")), str(fn.get("entry"))))
        preverified = resolve_preverified_object(fn, unit)
        if preverified is not None:
            shutil.copy2(preverified, object_path)
            row = {
                "name": fn["name"],
                "entry": fn.get("entry"),
                "kind": fn.get("kind"),
                "source": str(source),
                "object": str(object_path),
                "status": "compiled",
                "method": "preverified-copy",
                "preverifiedObject": str(preverified),
                "returnCode": 0,
            }
            preverified_copies += 1
            rows.append(row)
        else:
            cl_args = compile_args_for_unit(unit, default_cl_args)
            proc = run(
                [
                    "bash",
                    str(ROOT / "scripts/cl-compile.sh"),
                    str(source),
                    str(object_path),
                    *cl_args,
                ],
                env=env,
            )
            log_base = args.out_dir / f"{fn.get('entry', 'unknown')}_{fn['name']}"
            log_base.with_suffix(".stdout").write_text(proc.stdout, encoding="utf-8")
            log_base.with_suffix(".stderr").write_text(proc.stderr, encoding="utf-8")
            ok = proc.returncode == 0 and object_path.is_file()
            row = {
                "name": fn["name"],
                "entry": fn.get("entry"),
                "kind": fn.get("kind"),
                "source": str(source),
                "object": str(object_path),
                "status": "compiled" if ok else "compile-failed",
                "method": "msvc-compile",
                "returnCode": proc.returncode,
            }
            rows.append(row)
        if args.progress_every and len(rows) % args.progress_every == 0:
            compiled = sum(1 for item in rows if item["status"] == "compiled")
            failed = sum(1 for item in rows if item["status"] != "compiled")
            print(
                f"swkotor-compile-recovered-shard: attempted={len(rows)} compiled={compiled} failed={failed}",
                file=sys.stderr,
                flush=True,
            )
        compiled_count = sum(1 for item in rows if item["status"] == "compiled")
        args.summary.write_text(
            json.dumps(
                {
                    "schema": "reconkit.swkotor-recovered-shard-compile.v1",
                    "manifest": str(args.manifest),
                    "attempted": len(rows),
                    "compiled": compiled_count,
                    "failed": len(rows) - compiled_count,
                    "preverifiedCopyCount": preverified_copies,
                    "objectsDir": str(args.out_dir),
                    "results": rows,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    report = json.loads(args.summary.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {k: report[k] for k in ["attempted", "compiled", "failed", "objectsDir"] if k in report},
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
