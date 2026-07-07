#!/usr/bin/env python3
"""Compile exported recovered swkotor source files with MSVC."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VC_ROOT = Path("/run/media/brunner56/MyBook/MizuchiSource/toolchains/msvc8.0-main")
DEFAULT_WINEPREFIX = ROOT / "target/toolchain-acquire/vctoolkit2003/wineprefix"


def run(args: list[str], *, env: dict[str, str], timeout: int = 240) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, cwd=ROOT, env=env, text=True, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return subprocess.CompletedProcess(args, 124, stdout, f"{stderr}\ntimed out after {timeout} seconds".strip())


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
    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update({"VC_ROOT": str(args.vc_root), "WINEPREFIX": str(args.wineprefix), "CL_OPT": "/O2"})
    rows = []
    for index, fn in enumerate(manifest["functions"]):
        if args.limit is not None and index >= args.limit:
            break
        source = Path(fn["exportedSource"])
        object_path = args.out_dir / f"{fn.get('entry', 'unknown')}_{fn['name']}.obj"
        proc = run(
            [
                "bash",
                str(ROOT / "scripts/cl-compile.sh"),
                str(source),
                str(object_path),
                "/GS-",
                "/Oy",
            ],
            env=env,
        )
        log_base = args.out_dir / f"{fn.get('entry', 'unknown')}_{fn['name']}"
        log_base.with_suffix(".stdout").write_text(proc.stdout, encoding="utf-8")
        log_base.with_suffix(".stderr").write_text(proc.stderr, encoding="utf-8")
        row = {
            "name": fn["name"],
            "entry": fn.get("entry"),
            "kind": fn.get("kind"),
            "source": str(source),
            "object": str(object_path),
            "status": "compiled" if proc.returncode == 0 and object_path.is_file() else "compile-failed",
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
        args.summary.write_text(
            json.dumps(
                {
                    "schema": "mizuchi.swkotor-recovered-shard-compile.v1",
                    "manifest": str(args.manifest),
                    "attempted": len(rows),
                    "compiled": sum(1 for item in rows if item["status"] == "compiled"),
                    "failed": sum(1 for item in rows if item["status"] != "compiled"),
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
    print(json.dumps({k: report[k] for k in ["attempted", "compiled", "failed", "objectsDir"]}, indent=2, sort_keys=True))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
