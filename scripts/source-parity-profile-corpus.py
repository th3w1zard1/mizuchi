#!/usr/bin/env python3
"""Select and run a compiler-profile corpus from verified matched examples."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def iter_jsonl(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def selectable(row: dict[str, Any]) -> bool:
    out_dir = Path(str(row.get("outDir", "")))
    return (
        bool(row.get("matched"))
        and out_dir.name == str(row.get("name"))
        and (out_dir / "candidate.c").exists()
        and (out_dir / "target.obj").exists()
    )


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
    family_order = ["fastcall-accessor", "absolute-global", "stdcall", "cdecl", "other"]
    while len(selected) < max_cases:
        progressed = False
        for family in [item for item in family_order if item in buckets] + sorted(set(buckets) - set(family_order)):
            family_buckets = buckets[family]
            for kind in sorted(family_buckets):
                bucket = family_buckets[kind]
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
                "clOpt": row.get("clOpt"),
                "extraFlags": row.get("extraFlags"),
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


def main() -> int:
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--matched-examples", type=Path, default=ROOT / "target/source-parity-index/swkotor/matched-examples.jsonl")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "target/source-parity-profile/swkotor")
    parser.add_argument("--max-cases", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true", help="Select corpus but do not run compiler sweep.")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    rows = list(iter_jsonl(args.matched_examples))
    selected = select_cases(rows, args.max_cases)
    case_names = [str(row["name"]) for row in selected]
    args.out_dir.mkdir(parents=True, exist_ok=True)

    selected_path = args.out_dir / "selected-cases.json"
    write_json(
        selected_path,
        {
            "schema": "mizuchi.source-parity-profile-corpus-selection.v1",
            "matchedExamples": str(args.matched_examples),
            "maxCases": args.max_cases,
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
        },
    )

    if args.dry_run or not case_names:
        summary = {
            "schema": "mizuchi.source-parity-profile-corpus-summary.v1",
            "status": "selected-only",
            "selectedCases": len(case_names),
            "selectedCasesPath": str(selected_path),
        }
        write_json(args.out_dir / "summary.json", summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    command = [str(ROOT / "scripts/swkotor-compiler-profile.sh")]
    for case in case_names:
        command.extend(["--case", case])
    command.extend(["--out", str(args.out_dir / "runs")])

    env = os.environ.copy()
    proc = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, check=False, timeout=args.timeout)
    (args.out_dir / "profile.stdout").write_text(proc.stdout, encoding="utf-8")
    (args.out_dir / "profile.stderr").write_text(proc.stderr, encoding="utf-8")

    profile_summary_jsonl = args.out_dir / "runs/summary.jsonl"
    profile_summary_tsv = args.out_dir / "runs/summary.tsv"
    evidence = summarize(profile_summary_jsonl) if profile_summary_jsonl.exists() else {}
    summary = {
        "schema": "mizuchi.source-parity-profile-corpus-summary.v1",
        "status": "complete" if proc.returncode == 0 else "failed",
        "returnCode": proc.returncode,
        "selectedCases": len(case_names),
        "selectedCasesPath": str(selected_path),
        "summaryJsonl": str(profile_summary_jsonl),
        "summaryTsv": str(profile_summary_tsv),
        "evidence": evidence,
        "claimBoundary": "compiler-profile scores guide future candidate generation; accepted source still requires per-function objdiff zero",
    }
    write_json(args.out_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
