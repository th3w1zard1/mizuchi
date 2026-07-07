"""Compiler-profile corpus selection and sweep primitives.

This module turns already verified matched examples into compiler-forensics
evidence. It is intentionally not a source-recovery claim: matching source still
requires each promoted function to pass its own objdiff gate.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .package_verify import compile_with_msvc
from .source_parity_synthesize import run_objdiff


DEFAULT_FLAGS = [
    ("od_oyminus_gsminus", ["/Od", "/Oy-", "/GS-"]),
    ("od_gz_oyminus_gsminus", ["/Od", "/GZ", "/Oy-", "/GS-"]),
    ("od_rtc1_oyminus_gsminus", ["/Od", "/RTC1", "/Oy-", "/GS-"]),
    ("od_g7_oyminus_gsminus", ["/Od", "/G7", "/Oy-", "/GS-"]),
    ("od_oi_oyminus_gsminus", ["/Od", "/Oi", "/Oy-", "/GS-"]),
    ("o1_oyminus_gsminus", ["/O1", "/Oy-", "/GS-"]),
    ("o2_oyminus_gsminus", ["/O2", "/Oy-", "/GS-"]),
    ("o2_oy_gsminus", ["/O2", "/Oy", "/GS-"]),
]


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def selectable(row: dict[str, Any]) -> bool:
    out_dir = Path(str(row.get("outDir", "")))
    candidate = Path(str(row.get("candidateSource") or out_dir / "candidate.c"))
    target = out_dir / "target.obj"
    return bool(row.get("matched")) and candidate.exists() and target.exists()


def diversity_key(row: dict[str, Any]) -> tuple[str, str]:
    kind = str(row.get("kind", "unknown"))
    tags = set(row.get("tags", []))
    if "fastcall-accessor" in tags:
        family = "fastcall-accessor"
    elif "absolute-global" in tags:
        family = "absolute-global"
    elif "stdcall-ret" in tags:
        family = "stdcall"
    elif "cdecl-ret" in tags:
        family = "cdecl"
    elif "compiler-profile-probe" in tags:
        family = "compiler-profile-probe"
    else:
        family = "other"
    return (family, kind)


def select_cases(rows: list[dict[str, Any]], max_cases: int) -> list[dict[str, Any]]:
    candidates = [row for row in rows if selectable(row)]
    buckets: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in candidates:
        family, kind = diversity_key(row)
        buckets[family][kind].append(row)
    for family_buckets in buckets.values():
        for bucket in family_buckets.values():
            bucket.sort(key=lambda row: (-int(row.get("bodyBytes") or 0), str(row.get("entry"))))

    selected: list[dict[str, Any]] = []
    family_order = ["fastcall-accessor", "absolute-global", "stdcall", "cdecl", "compiler-profile-probe", "other"]
    while len(selected) < max_cases:
        progressed = False
        for family in [item for item in family_order if item in buckets] + sorted(set(buckets) - set(family_order)):
            for kind in sorted(buckets[family]):
                bucket = buckets[family][kind]
                if not bucket:
                    continue
                selected.append(bucket.pop(0))
                progressed = True
                break
            if len(selected) >= max_cases:
                break
        if not progressed:
            break
    return selected


def parse_profile(value: str) -> tuple[str, Path]:
    if "=" in value:
        name, root = value.split("=", 1)
    else:
        root_path = Path(value)
        name = root_path.name or "msvc"
        root = value
    return name.strip() or "msvc", Path(root).expanduser().resolve()


def parse_flag_set(value: str) -> tuple[str, list[str]]:
    if "=" in value:
        name, flags = value.split("=", 1)
    else:
        flags = value
        name = "_".join(part.strip("/-").lower() for part in flags.replace(",", " ").split() if part) or "custom"
    return name.strip() or "custom", [part for part in flags.replace(",", " ").split() if part]


def default_profiles() -> list[tuple[str, Path]]:
    profiles: list[tuple[str, Path]] = []
    env_root = os.environ.get("VC_ROOT")
    if env_root:
        profiles.append(("env-vc-root", Path(env_root).expanduser().resolve()))
    vc71 = Path("target/toolchain-acquire/vctoolkit2003/msitools-extract/Program Files/Microsoft Visual C++ Toolkit 2003")
    if (vc71 / "bin" / "cl.exe").exists():
        profiles.append(("vc71", vc71.resolve()))
    vc80 = Path("/run/media/brunner56/MyBook/ReconstructKitSource/toolchains/msvc8.0-main")
    if (vc80 / "bin" / "cl.exe").exists():
        profiles.append(("vc80", vc80.resolve()))
    return profiles


def compiler_banner(msvc_root: Path, wine: str, wineprefix: Path | None, timeout: int) -> str:
    cl_exe = msvc_root / "bin" / "cl.exe"
    if not cl_exe.exists():
        return f"cl.exe not found at {cl_exe}"
    env = dict(os.environ)
    if wineprefix is not None:
        env["WINEPREFIX"] = str(wineprefix.expanduser().resolve())
    env["WINEDEBUG"] = env.get("WINEDEBUG", "-all")
    try:
        proc = subprocess.run([wine, str(cl_exe)], env=env, text=True, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"cl.exe banner timed out after {timeout} seconds"
    return (proc.stdout + proc.stderr).replace("\r", "\n")[:1000]


def summarize(summary_jsonl: Path) -> dict[str, Any]:
    rows = list(iter_jsonl(summary_jsonl))
    by_profile_flag: Counter[str] = Counter()
    matched = 0
    mismatched = 0
    compile_failed = 0
    missing_input = 0
    best_by_case: dict[str, dict[str, Any]] = {}
    for row in rows:
        status = row.get("status")
        if status == "matched" and row.get("differences") == 0:
            matched += 1
            by_profile_flag[f"{row.get('profile')} {row.get('flagSet')}"] += 1
        elif status == "compile-failed":
            compile_failed += 1
        elif status == "missing-input":
            missing_input += 1
        else:
            mismatched += 1

        case = str(row.get("case"))
        score = 100.0 if status == "matched" and row.get("differences") == 0 else float(row.get("bestMatchPercent") or -1)
        current = best_by_case.get(case)
        if current is None or score > float(current.get("score", -1)):
            best_by_case[case] = {
                "case": case,
                "score": score,
                "status": status,
                "profile": row.get("profile"),
                "flagSet": row.get("flagSet"),
                "compilerArgs": row.get("compilerArgs"),
            }
    return {
        "attempts": len(rows),
        "matchedAttempts": matched,
        "mismatchedAttempts": mismatched,
        "compileFailedAttempts": compile_failed,
        "missingInputAttempts": missing_input,
        "profileFlagMatches": dict(by_profile_flag.most_common()),
        "bestByCase": sorted(best_by_case.values(), key=lambda row: row["case"]),
    }


def select_report(selected: list[dict[str, Any]], matched_examples: Path, max_cases: int) -> dict[str, Any]:
    return {
        "schema": "reconkit.source-parity-profile-corpus-selection.v1",
        "matchedExamples": str(matched_examples),
        "maxCases": max_cases,
        "selectedCount": len(selected),
        "cases": [
            {
                "name": row.get("name"),
                "entry": row.get("entry"),
                "kind": row.get("kind"),
                "tags": row.get("tags", []),
                "outDir": row.get("outDir"),
                "candidateSource": row.get("candidateSource"),
            }
            for row in selected
        ],
        "selectionRule": "round-robin by instruction family and matched kind, preferring larger examples inside each bucket",
    }


def run_sweep(
    *,
    selected: list[dict[str, Any]],
    out_dir: Path,
    profiles: list[tuple[str, Path]],
    flag_sets: list[tuple[str, list[str]]],
    wine: str,
    wineprefix: Path | None,
    timeout: int,
    clean: bool,
) -> Path:
    runs_dir = out_dir / "runs"
    if clean and runs_dir.exists():
        shutil.rmtree(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    summary_jsonl = runs_dir / "summary.jsonl"
    summary_jsonl.write_text("", encoding="utf-8")

    if not profiles:
        for row in selected:
            append_jsonl(
                summary_jsonl,
                {
                    "schema": "reconkit.source-parity-profile-corpus-attempt.v1",
                    "case": row.get("name"),
                    "status": "missing-input",
                    "reason": "no compiler profiles available; pass --profile NAME=VC_ROOT or set VC_ROOT",
                },
            )
        return summary_jsonl

    for row in selected:
        case_name = str(row.get("name"))
        out_case_dir = Path(str(row.get("outDir", "")))
        candidate = Path(str(row.get("candidateSource") or out_case_dir / "candidate.c"))
        target = out_case_dir / "target.obj"
        if not candidate.exists() or not target.exists():
            append_jsonl(
                summary_jsonl,
                {
                    "schema": "reconkit.source-parity-profile-corpus-attempt.v1",
                    "case": case_name,
                    "status": "missing-input",
                    "candidate": str(candidate),
                    "target": str(target),
                },
            )
            continue
        for profile_name, msvc_root in profiles:
            banner = compiler_banner(msvc_root, wine, wineprefix, min(timeout, 30))
            for flag_name, flags in flag_sets:
                run_dir = runs_dir / case_name / profile_name / flag_name
                run_dir.mkdir(parents=True, exist_ok=True)
                source = run_dir / "candidate.c"
                source.write_text(candidate.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
                candidate_obj = run_dir / "candidate.obj"
                compile_result = compile_with_msvc(
                    source=source,
                    object_path=candidate_obj,
                    out_dir=run_dir,
                    stem="candidate",
                    args=flags,
                    timeout=timeout,
                    msvc_root=msvc_root,
                    wine=wine,
                    wineprefix=wineprefix,
                )
                if compile_result.get("status") != "ok":
                    append_jsonl(
                        summary_jsonl,
                        {
                            "schema": "reconkit.source-parity-profile-corpus-attempt.v1",
                            "case": case_name,
                            "profile": profile_name,
                            "flagSet": flag_name,
                            "compilerArgs": flags,
                            "status": "compile-failed",
                            "compileExit": compile_result.get("returnCode"),
                            "compilerRoot": str(msvc_root),
                            "banner": banner,
                            "compileStderrTail": compile_result.get("stderrTail"),
                        },
                    )
                    continue
                verify = run_objdiff(target, candidate_obj, run_dir, timeout=timeout)
                append_jsonl(
                    summary_jsonl,
                    {
                        "schema": "reconkit.source-parity-profile-corpus-attempt.v1",
                        "case": case_name,
                        "profile": profile_name,
                        "flagSet": flag_name,
                        "compilerArgs": flags,
                        "status": verify.get("status"),
                        "differences": verify.get("differences"),
                        "bestMatchPercent": verify.get("bestMatchPercent"),
                        "objdiffExit": verify.get("objdiffExit"),
                        "compilerRoot": str(msvc_root),
                        "object": str(candidate_obj),
                        "verify": str(run_dir / "verify.json"),
                        "banner": banner,
                    },
                )
    return summary_jsonl


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--matched-examples", type=Path, default=Path("target/source-parity-index/swkotor/matched-examples.jsonl"))
    parser.add_argument("--out-dir", type=Path, default=Path("target/source-parity-profile/swkotor"))
    parser.add_argument("--max-cases", type=int, default=6)
    parser.add_argument("--select-only", "--dry-run", dest="select_only", action="store_true", help="Select corpus cases without compiling.")
    parser.add_argument("--profile", action="append", default=[], help="Compiler profile as NAME=VC_ROOT. Repeat for multiple toolchains.")
    parser.add_argument("--flag-set", action="append", default=[], help="Flag set as NAME='/O2 /Oy /GS-'. Repeat for custom matrix.")
    parser.add_argument("--wine", default="wine")
    parser.add_argument("--wineprefix", type=Path)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args(argv)

    rows = list(iter_jsonl(args.matched_examples))
    selected = select_cases(rows, args.max_cases)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    selected_path = args.out_dir / "selected-cases.json"
    write_json(selected_path, select_report(selected, args.matched_examples, args.max_cases))

    if args.select_only:
        summary = {
            "schema": "reconkit.source-parity-profile-corpus-summary.v1",
            "status": "selected-only",
            "selectedCases": len(selected),
            "selectedCasesPath": str(selected_path),
            "claimBoundary": "compiler-profile corpus selection is not source recovery; accepted source still requires per-function objdiff zero",
        }
        write_json(args.out_dir / "summary.json", summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    profiles = [parse_profile(value) for value in args.profile] if args.profile else default_profiles()
    flag_sets = [parse_flag_set(value) for value in args.flag_set] if args.flag_set else DEFAULT_FLAGS
    summary_jsonl = run_sweep(
        selected=selected,
        out_dir=args.out_dir,
        profiles=profiles,
        flag_sets=flag_sets,
        wine=args.wine,
        wineprefix=args.wineprefix,
        timeout=args.timeout,
        clean=args.clean,
    )
    summary = {
        "schema": "reconkit.source-parity-profile-corpus-summary.v1",
        "status": "complete",
        "selectedCases": len(selected),
        "selectedCasesPath": str(selected_path),
        "summaryJsonl": str(summary_jsonl),
        "evidence": summarize(summary_jsonl),
        "compilerProfiles": [{"name": name, "root": str(root)} for name, root in profiles],
        "flagSets": [{"name": name, "args": flags} for name, flags in flag_sets],
        "claimBoundary": "compiler-profile scores guide future candidate generation; accepted source still requires per-function objdiff zero",
    }
    write_json(args.out_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
