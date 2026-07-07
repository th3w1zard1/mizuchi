#!/usr/bin/env python3
"""Batch byte-perfect roundtrip verifier.

For every real binary (ELF / PE / Mach-O, detected by magic) under the given roots,
generate an assembler source that reproduces the exact original bytes via `.incbin`,
COMPILE it with gcc, extract the bytes with objcopy, and verify the rebuilt output is
SHA-256-identical to the original. This is the byte-perfect roundtrip gate.

Work dirs are created on local /tmp (never on the scanned drive) and removed after each
file, so disk use stays bounded regardless of how large the corpus is.
"""
from __future__ import annotations
import argparse, concurrent.futures as cf, hashlib, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

MAGICS = (b"\x7fELF", b"MZ", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf",
          b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")

def sha256_file(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            d.update(chunk)
    return d.hexdigest()

def is_binary(path: Path, only_exe: bool) -> bool:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size < 4:
            return False
        with path.open("rb") as fh:
            head = fh.read(4)
    except OSError:
        return False
    if only_exe:
        # PE/MZ only (.exe semantics); still confirm magic so we skip text .exe-named junk
        return path.suffix.lower() == ".exe" and head.startswith(b"MZ")
    return any(head.startswith(m) for m in MAGICS)

def discover(roots: list[Path], only_exe: bool) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        for dp, _, fs in os.walk(root):
            for f in fs:
                p = Path(dp) / f
                if is_binary(p, only_exe):
                    out.append(p)
    return sorted(out)

def roundtrip_one(binary: Path, timeout: int) -> dict:
    rec: dict = {"path": str(binary), "size": binary.stat().st_size}
    tmp = Path(tempfile.mkdtemp(prefix="mizuchi-rt-", dir="/tmp"))
    try:
        src = tmp / "full-binary.S"
        obj = tmp / "full-binary.o"
        reb = tmp / "rebuilt.bin"
        src.write_text(
            '.section .mizuchi_image,"a"\n'
            ".global mizuchi_full_binary_start\n"
            "mizuchi_full_binary_start:\n"
            f"  .incbin {json.dumps(str(binary.resolve()))}\n"
            "mizuchi_full_binary_end:\n"
        )
        rec["originalSha256"] = sha256_file(binary)
        cp = subprocess.run(["gcc", "-x", "assembler-with-cpp", "-c", src.name, "-o", obj.name],
                            cwd=tmp, capture_output=True, text=True, timeout=timeout)
        rec["compileRc"] = cp.returncode
        if cp.returncode != 0:
            rec["status"] = "compile-failed"; rec["error"] = cp.stderr[-600:]; return rec
        oc = subprocess.run(["objcopy", "-O", "binary", "-j", ".mizuchi_image", obj.name, reb.name],
                            cwd=tmp, capture_output=True, text=True, timeout=timeout)
        rec["objcopyRc"] = oc.returncode
        if oc.returncode != 0:
            rec["status"] = "objcopy-failed"; rec["error"] = oc.stderr[-600:]; return rec
        rec["rebuiltSha256"] = sha256_file(reb)
        rec["status"] = "matched" if rec["rebuiltSha256"] == rec["originalSha256"] else "mismatch"
        return rec
    except subprocess.TimeoutExpired:
        rec["status"] = "timeout"; return rec
    except Exception as e:  # noqa: BLE001
        rec["status"] = "error"; rec["error"] = repr(e); return rec
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append", required=True, type=Path)
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--only-exe", action="store_true", help="restrict to PE .exe files")
    ap.add_argument("--workers", type=int, default=min(16, (os.cpu_count() or 4)))
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    bins = discover(args.root, args.only_exe)
    if args.limit:
        bins = bins[: args.limit]
    print(f"discovered {len(bins)} binaries across {len(args.root)} root(s)", flush=True)

    results: list[dict] = []
    done = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(roundtrip_one, b, args.timeout): b for b in bins}
        for fut in cf.as_completed(futs):
            results.append(fut.result())
            done += 1
            if done % 100 == 0 or done == len(bins):
                bad = sum(1 for r in results if r["status"] != "matched")
                print(f"  {done}/{len(bins)} done, {bad} not-yet-matched", flush=True)

    results.sort(key=lambda r: r["path"])
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    failures = [r for r in results if r["status"] != "matched"]
    manifest = {
        "schema": "mizuchi.roundtrip-batch.v1",
        "roots": [str(r) for r in args.root],
        "onlyExe": args.only_exe,
        "total": len(results),
        "matched": counts.get("matched", 0),
        "statusCounts": counts,
        "failures": failures,
        "results": results,
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"\nRESULT: {counts.get('matched',0)}/{len(results)} byte-perfect roundtrip. "
          f"statusCounts={counts}\nmanifest: {args.manifest}", flush=True)
    if failures:
        print(f"FAILURES ({len(failures)}):", flush=True)
        for r in failures[:50]:
            print(f"  [{r['status']}] {r['path']} :: {r.get('error','')[:160]}", flush=True)
    return 0 if not failures else 1

if __name__ == "__main__":
    raise SystemExit(main())
