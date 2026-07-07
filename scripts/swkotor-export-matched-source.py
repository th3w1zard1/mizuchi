#!/usr/bin/env python3
"""Export objdiff/code-slice-verified swkotor candidate functions into source shards."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mizuchi_re.source_export import export_recovered_source  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--summary",
        type=Path,
        action="append",
        default=None,
        help="JSONL match summary to export. May be repeated.",
    )
    parser.add_argument("--out-dir", type=Path, default=ROOT / "target/swkotor-recovered")
    parser.add_argument("--source-name", default="simple_matches.c")
    args = parser.parse_args()

    summaries = args.summary or [
        ROOT / "target/swkotor-trivial-matches/summary.jsonl",
        ROOT / "target/swkotor-reloc-wrapper-matches/summary.jsonl",
    ]
    result = export_recovered_source(summaries, out_dir=args.out_dir, source_name=args.source_name)
    if result["status"] == "empty":
        raise SystemExit(f"no verified matched rows found in {', '.join(map(str, summaries))}")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
