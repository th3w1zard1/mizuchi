#!/usr/bin/env python3
"""Build a prioritized swkotor source-recovery queue from current proof artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_jsonl(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def match_key(row: dict) -> tuple[str, str]:
    return (str(row.get("name")), str(row.get("entry")))


def load_matched(paths: list[Path]) -> dict[tuple[str, str], dict]:
    matched: dict[tuple[str, str], dict] = {}
    for path in paths:
        for row in iter_jsonl(path):
            if row.get("status") == "matched" and row.get("differences") == 0:
                matched[match_key(row)] = row
    return matched


def load_manifest_matched(paths: list[Path]) -> dict[tuple[str, str], dict]:
    matched: dict[tuple[str, str], dict] = {}
    for path in paths:
        if not path.is_file():
            continue
        manifest = read_json(path)
        for row in manifest.get("functions") or []:
            matched[match_key(row)] = row
    return matched


def has_call_or_jump(data: bytes) -> bool:
    if data.startswith(b"\xe9") or data.startswith(b"\xe8"):
        return True
    return any(byte in {0xE8, 0xE9} for byte in data) or data.startswith(b"\xff\x25")


def classify(row: dict, data: bytes) -> list[str]:
    tags: list[str] = []
    size = int(row.get("bodyBytes") or 0)
    instrs = int(row.get("instructionCount") or 0)
    if size <= 12:
        tags.append("tiny-unmatched")
    elif size <= 32:
        tags.append("small-unmatched")
    if has_call_or_jump(data):
        tags.append("call-or-jump")
    if data.startswith(b"\xff\x25"):
        tags.append("import-thunk")
    if data.startswith(b"\x55\x8b\xec"):
        tags.append("ebp-frame")
    if data.startswith(b"\x8b\x41") or data.startswith(b"\x8a\x41") or data.startswith(b"\x0f"):
        tags.append("accessor-near-miss")
    if instrs <= 4 and "call-or-jump" not in tags:
        tags.append("compiler-profile-probe")
    return tags


def priority(row: dict, data: bytes, *, text_section: str = ".textV") -> tuple[int, int, int, str]:
    size = int(row.get("bodyBytes") or 0)
    instrs = int(row.get("instructionCount") or 0)
    penalty = 0
    if has_call_or_jump(data):
        penalty += 40
    if data.startswith(b"\xff\x25"):
        penalty += 25
    if data.startswith(b"\x55\x8b\xec"):
        penalty += 15
    if row.get("section") != text_section:
        penalty += 100
    return (penalty + size, instrs, size, str(row.get("entry", "")))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=ROOT / "target/swkotor-unpack/facts/function-inventory.jsonl")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "target/swkotor-recovery-queue")
    parser.add_argument("--limit", type=int, default=250)
    parser.add_argument(
        "--text-section",
        default=".textV",
        help="Primary executable section for prioritization (e.g. .textV, .textU).",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        action="append",
        default=None,
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        action="append",
        default=[],
        help="Recovered-source manifest whose functions should be treated as already verified/exported.",
    )
    args = parser.parse_args()

    summary_paths = args.summary or [
        ROOT / "target/swkotor-trivial-matches/summary.jsonl",
        ROOT / "target/swkotor-reloc-wrapper-matches/summary.jsonl",
    ]
    matched = load_matched(summary_paths)
    matched.update(load_manifest_matched(args.manifest))
    entries = []
    total = 0
    for row in iter_jsonl(args.inventory):
        total += 1
        name = str(row.get("name"))
        if match_key(row) in matched:
            continue
        data = bytes.fromhex(str(row.get("bytes", "")))
        tags = classify(row, data)
        entries.append(
            {
                "schema": "reconkit.swkotor-recovery-queue-entry.v1",
                "name": name,
                "entry": row.get("entry"),
                "section": row.get("section"),
                "bodyBytes": row.get("bodyBytes"),
                "instructionCount": row.get("instructionCount"),
                "tags": tags,
                "bytes": row.get("bytes"),
                "priority": priority(row, data, text_section=args.text_section)[0],
                "nextAction": "derive high-level C/C++ candidate, compile with candidate compiler profile, accept only objdiff zero",
            }
        )

    entries.sort(key=lambda row: (row["priority"], row["instructionCount"], row["bodyBytes"], row["entry"]))
    selected = entries[: args.limit]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    queue_path = args.out_dir / "queue.jsonl"
    queue_path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in selected) + "\n", encoding="utf-8")

    summary = {
        "schema": "reconkit.swkotor-recovery-queue.v1",
        "inventory": str(args.inventory),
        "queue": str(queue_path),
        "totalInventoryFunctions": total,
        "verifiedMatchedFunctions": len(matched),
        "remainingFunctions": len(entries),
        "selectedFunctions": len(selected),
        "tagCounts": {},
    }
    for row in entries:
        for tag in row["tags"]:
            summary["tagCounts"][tag] = summary["tagCounts"].get(tag, 0) + 1

    summary_path = args.out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# SWKOTOR Recovery Queue",
        "",
        f"- Total inventory functions: `{summary['totalInventoryFunctions']}`",
        f"- Verified matched functions: `{summary['verifiedMatchedFunctions']}`",
        f"- Remaining functions: `{summary['remainingFunctions']}`",
        f"- Selected next functions: `{summary['selectedFunctions']}`",
        "",
        "## Top Candidates",
        "",
        "| Entry | Name | Bytes | Instrs | Tags |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for row in selected[:25]:
        lines.append(
            f"| `{row['entry']}` | `{row['name']}` | {row['bodyBytes']} | {row['instructionCount']} | {', '.join(row['tags'])} |"
        )
    lines.append("")
    lines.append("Acceptance stays strict: generated source must compile as C/C++ and reach objdiff zero for its target slice.")
    (args.out_dir / "next.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
